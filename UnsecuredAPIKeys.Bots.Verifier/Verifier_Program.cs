using Microsoft.EntityFrameworkCore;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Text.RegularExpressions;
using System.Reflection;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Configuration;
using UnsecuredAPIKeys.Data;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Data.Models;
using UnsecuredAPIKeys.Providers;
using UnsecuredAPIKeys.Providers._Interfaces;
using UnsecuredAPIKeys.Providers.Common;
using UnsecuredAPIKeys.Providers.Services;
using UnsecuredAPIKeys.Bots.Verifier.Services;

namespace UnsecuredAPIKeys.Bots.Verifier
{
    internal class CounterState
    {
        public volatile int ValidCount;
        public volatile int InvalidCount;
        public volatile int SkippedCount;
        public volatile int ProcessedCount;
        public volatile int CircuitBreakerTrippedCount;
    }

    // Simple Circuit Breaker implementation
    internal class SimpleCircuitBreaker(int threshold = 5, TimeSpan? retryTimeout = null)
    {
        private readonly TimeSpan _retryTimeout = retryTimeout ?? TimeSpan.FromSeconds(30);
        private int _failureCount;
        private DateTime _lastFailureTime;
        private bool _isOpen;
        private readonly Lock _lock = new();

        public bool IsOpen
        {
            get
            {
                lock (_lock)
                {
                    if (!_isOpen) return false;

                    var elapsed = DateTime.UtcNow - _lastFailureTime;
                    if (elapsed >= _retryTimeout)
                    {
                        _isOpen = false;
                        _failureCount = 0;
                        return false;
                    }
                    return true;
                }
            }
        }

        public void RecordSuccess()
        {
            lock (_lock)
            {
                _failureCount = 0;
                _isOpen = false;
            }
        }

        public void RecordFailure()
        {
            lock (_lock)
            {
                _failureCount++;
                _lastFailureTime = DateTime.UtcNow;
                if (_failureCount >= threshold)
                {
                    _isOpen = true;
                }
            }
        }
    }

    // Enhanced Circuit Breaker with per-provider tracking
    internal class ProviderCircuitBreakers(ILogger? logger)
    {
        private readonly ConcurrentDictionary<string, SimpleCircuitBreaker> _breakers = new();

        public SimpleCircuitBreaker GetBreaker(string providerName)
        {
            return _breakers.GetOrAdd(providerName, name =>
            {
                logger?.LogInformation("Creating circuit breaker for provider {Provider}", name);
                return new SimpleCircuitBreaker(5, TimeSpan.FromSeconds(30));
            });
        }

        public int GetTrippedCount()
        {
            return _breakers.Count(b => b.Value.IsOpen);
        }
    }

    // Simple resource monitoring without PerformanceCounter
    internal class ResourceMonitor
    {
        private readonly ILogger? _logger;
        private readonly Timer _monitorTimer;
        private long _lastGcMemoryBytes;
        private volatile int _activeThreads;

        public ResourceMonitor(ILogger? logger)
        {
            _logger = logger;
            _monitorTimer = new Timer(UpdateMetrics, null, TimeSpan.Zero, TimeSpan.FromSeconds(5));
        }

        private void UpdateMetrics(object? state)
        {
            try
            {
                _lastGcMemoryBytes = GC.GetTotalMemory(false);
                _activeThreads = Process.GetCurrentProcess().Threads.Count;

                var memoryMB = _lastGcMemoryBytes / (1024.0 * 1024.0);
                if (memoryMB > 1000) // Over 1GB
                {
                    _logger?.LogWarning("High memory usage detected: {Memory:F2} MB", memoryMB);
                    GC.Collect();
                    GC.WaitForPendingFinalizers();
                    GC.Collect();
                }
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "Failed to update performance metrics");
            }
        }

        public int GetAdaptiveParallelism(int baseParallelism)
        {
            // Simple adaptive logic based on memory usage and thread count
            var memoryMB = _lastGcMemoryBytes / (1024.0 * 1024.0);
            if (memoryMB > 800) // High memory usage
                return Math.Max(1, baseParallelism / 2);

            if (_activeThreads > Environment.ProcessorCount * 10) // Too many threads
                return Math.Max(1, baseParallelism / 2);

            return baseParallelism;
        }

        public void Dispose()
        {
            _monitorTimer?.Dispose();
        }
    }

    // Cache management with size limits
    internal class CacheManager<TKey, TValue>(int maxSize, TimeSpan expiry, ILogger? logger = null)
        where TKey : notnull
    {
        private readonly ConcurrentDictionary<TKey, (TValue value, DateTime cachedAt)> _cache = new();

        public bool TryGetValue(TKey key, out TValue value)
        {
            if (_cache.TryGetValue(key, out var cached))
            {
                if (DateTime.UtcNow - cached.cachedAt < expiry)
                {
                    value = cached.value;
                    return true;
                }
                else
                {
                    _cache.TryRemove(key, out _);
                }
            }
            value = default!;
            return false;
        }

        public void Set(TKey key, TValue value)
        {
            // Check size limit
            if (_cache.Count >= maxSize)
            {
                Cleanup();
            }
            _cache[key] = (value, DateTime.UtcNow);
        }

        public void Clear()
        {
            _cache.Clear();
        }

        private void Cleanup()
        {
            var itemsToRemove = _cache.Count / 3; // Remove 1/3 of items
            var oldestKeys = _cache
                .OrderBy(x => x.Value.cachedAt)
                .Take(itemsToRemove)
                .Select(x => x.Key)
                .ToList();

            foreach (var key in oldestKeys)
            {
                _cache.TryRemove(key, out _);
            }

            logger?.LogInformation("Cache cleanup: removed {Count} entries", oldestKeys.Count);
        }

        public void ClearExpired()
        {
            var cutoff = DateTime.UtcNow - expiry;
            var expiredKeys = _cache
                .Where(kvp => kvp.Value.cachedAt < cutoff)
                .Select(kvp => kvp.Key)
                .ToList();

            foreach (var key in expiredKeys)
            {
                _cache.TryRemove(key, out _);
            }

            if (expiredKeys.Count > 0)
            {
                logger?.LogInformation("Removed {ExpiredCount} expired cache entries", expiredKeys.Count);
            }
        }
    }

    internal static class Verifier_Program
    {
        static readonly IReadOnlyList<IApiKeyProvider> _providers = ApiProviderRegistry.VerifierProviders;
        private static readonly Dictionary<string, IApiKeyProvider> _patternToProviderMap;
        private static CacheManager<string, (ValidationResult validationResult, ApiTypeEnum Type)>? _keyCache;
        private static readonly Dictionary<string, Regex> _compiledRegexPatterns = [];
        
        // Track which ApiTypes are excluded from verification
        private static readonly HashSet<ApiTypeEnum> _excludedApiTypes = [];

        // Adaptive configuration based on system resources for better performance
        private static readonly int MaxDegreeOfDbParallelism = Math.Min(Environment.ProcessorCount * 2, 16);
        private static readonly int MaxDegreeOfApiParallelism = Math.Min(Environment.ProcessorCount * 4, 100);
        private static readonly int OptimalBatchSize = Math.Min(Environment.ProcessorCount * 10, 200);

        private const string ErrorLogFile = "apiverifier-errors.log";
        private static readonly SemaphoreSlim _logSemaphore = new(1, 1);
        private static readonly TimeSpan CacheExpiry = TimeSpan.FromMinutes(15);
        private const int MaxCacheSize = 10000;

        // Enhanced services
        private static IServiceProvider? _serviceProvider;
        private static IHttpClientFactory? _httpClientFactory;
        private static ILogger? _logger;
        private static CancellationTokenSource? _cancellationTokenSource;
        private static ProviderCircuitBreakers? _circuitBreakers;
        private static ResourceMonitor? _resourceMonitor;
        private static IConfiguration? _configuration;

        static Verifier_Program()
        {
            // First, identify ALL providers (including those with verificationUse: false)
            // to build the exclusion list
            // Get the assembly that contains the providers (same one that ApiProviderRegistry uses)
            var providersAssembly = typeof(ApiProviderRegistry).Assembly;
            
            Console.WriteLine($"Scanning assembly: {providersAssembly.FullName}");
            
            // Get all types that implement IApiKeyProvider
            var providerTypes = providersAssembly
                .GetTypes()
                .Where(type => typeof(IApiKeyProvider).IsAssignableFrom(type)
                           && !type.IsInterface
                           && !type.IsAbstract)
                .ToList();
                
            Console.WriteLine($"Found {providerTypes.Count} provider types");

            // Check each provider type for its ApiProvider attribute
            foreach (var providerType in providerTypes)
            {
                // Get the ApiProvider attribute
                var attrs = providerType.GetCustomAttributes(typeof(ApiProviderAttribute), false);
                if (attrs.Length > 0 && attrs[0] is ApiProviderAttribute attr)
                {
                    Console.WriteLine($"Provider {providerType.Name}: ScraperUse={attr.ScraperUse}, VerificationUse={attr.VerificationUse}");
                    
                    // If verification is disabled, add to exclusion list
                    if (!attr.VerificationUse)
                    {
                        try
                        {
                            var providerInstance = (IApiKeyProvider)Activator.CreateInstance(providerType)!;
                            _excludedApiTypes.Add(providerInstance.ApiType);
                            Console.WriteLine($"  -> Excluding {providerInstance.ApiType} from verification");
                        }
                        catch (Exception ex)
                        {
                            Console.WriteLine($"  -> Error creating instance of {providerType.Name}: {ex.Message}");
                        }
                    }
                }
                else
                {
                    Console.WriteLine($"Provider {providerType.Name}: No ApiProvider attribute found (will use defaults)");
                }
            }
            
            if (_excludedApiTypes.Count == 0)
            {
                Console.WriteLine("No providers excluded from verification");
            }
            else
            {
                Console.WriteLine($"Total excluded ApiTypes: {_excludedApiTypes.Count}");
            }

            _patternToProviderMap = [];
            foreach (var provider in _providers)
            {
                foreach (var pattern in provider.RegexPatterns)
                {
                    _patternToProviderMap[pattern] = provider;
                    try
                    {
                        // Enhanced regex compilation with better performance options
                        var regexOptions = RegexOptions.Compiled | RegexOptions.CultureInvariant;

                        // Check if NonBacktracking is available (.NET 7+)
                        if (Environment.Version.Major >= 7)
                        {
                            try
                            {
                                regexOptions |= RegexOptions.NonBacktracking;
                            }
                            catch
                            {
                                // NonBacktracking not available in this runtime
                            }
                        }

                        // INCREASED TIMEOUT: Changed from 500ms to 5000ms (5 seconds) to give complex patterns more time to compile
                        _compiledRegexPatterns[pattern] = new Regex(pattern, regexOptions, TimeSpan.FromMilliseconds(5000));
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"Error: Failed to compile regex pattern '{pattern}': {ex.Message}");
                        // Skip this pattern entirely if it can't be compiled
                        continue;
                    }
                }
            }

            // Setup graceful shutdown
            Console.CancelKeyPress += (sender, e) =>
            {
                e.Cancel = true;
                _cancellationTokenSource?.Cancel();
                Console.WriteLine("\nGraceful shutdown initiated...");
            };
        }

        private static async Task Main()
        {
            _cancellationTokenSource = new CancellationTokenSource();

            // Load configuration
            _configuration = new ConfigurationBuilder()
                .AddJsonFile("appsettings.json", optional: true, reloadOnChange: true)
                .AddJsonFile($"appsettings.{Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT") ?? "Production"}.json", optional: true)
                .AddEnvironmentVariables()
                .Build();

            var serviceCollection = new ServiceCollection();
            ConfigureServices(serviceCollection);
            _serviceProvider = serviceCollection.BuildServiceProvider();

            _httpClientFactory = _serviceProvider.GetRequiredService<IHttpClientFactory>();
            var loggerFactory = _serviceProvider.GetService<ILoggerFactory>();
            _logger = loggerFactory?.CreateLogger("Verifier_Program");
            _circuitBreakers = new ProviderCircuitBreakers(_logger);
            _resourceMonitor = new ResourceMonitor(_logger);
            _keyCache = new CacheManager<string, (ValidationResult, ApiTypeEnum)>(MaxCacheSize, CacheExpiry, _logger);

            _logger?.LogInformation("========================================");
            _logger?.LogInformation(" API Key Verifier (Enhanced Version)   ");
            _logger?.LogInformation("========================================");
            _logger?.LogInformation("System Info - CPU Cores: {CpuCores}, DB Parallelism: {DbParallelism}, API Parallelism: {ApiParallelism}",
                Environment.ProcessorCount, MaxDegreeOfDbParallelism, MaxDegreeOfApiParallelism);
            Console.WriteLine("========================================");
            Console.WriteLine(" API Key Verifier (Enhanced Version)   ");
            Console.WriteLine("========================================");
            Console.WriteLine($"System Info - CPU Cores: {Environment.ProcessorCount}, DB Parallelism: {MaxDegreeOfDbParallelism}, API Parallelism: {MaxDegreeOfApiParallelism}");

            try
            {
                // Clean up any stuck batches from previous runs
                using (var scope = _serviceProvider.CreateScope())
                {
                    var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();
                    var batchLockingService = scope.ServiceProvider.GetRequiredService<BatchLockingService>();
                    
                    _logger?.LogInformation("Checking for stuck batches from previous runs...");
                    Console.WriteLine("Checking for stuck batches from previous runs...");
                    
                    int cleanedCount = await batchLockingService.ForceCleanupAllStuckBatchesAsync(
                        dbContext, 
                        TimeSpan.FromHours(2), // Clean up batches older than 2 hours
                        _cancellationTokenSource.Token);
                    
                    if (cleanedCount > 0)
                    {
                        _logger?.LogInformation("Cleaned up {Count} stuck batches from previous runs", cleanedCount);
                        Console.WriteLine($"Cleaned up {cleanedCount} stuck batches from previous runs");
                    }
                    else
                    {
                        _logger?.LogInformation("No stuck batches found");
                        Console.WriteLine("No stuck batches found");
                    }
                }

                // Check for continuous mode setting
                bool continuousMode;
                using (var scope = _serviceProvider.CreateScope())
                {
                    var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();
                    continuousMode = bool.Parse((await dbContext.ApplicationSettings
                        .SingleOrDefaultAsync(x => x.Key == "VerifierContinuousMode", _cancellationTokenSource.Token))?.Value ?? "false");
                }

                if (continuousMode)
                {
                    _logger?.LogInformation("Running in CONTINUOUS MODE - will loop every 5 minutes");
                    Console.WriteLine("Running in CONTINUOUS MODE - will loop every 5 minutes");
                }
                else
                {
                    _logger?.LogInformation("Running in SINGLE RUN MODE - will exit after completion");
                    Console.WriteLine("Running in SINGLE RUN MODE - will exit after completion");
                }

                do
                {
                    try
                    {
                        await RunVerificationCycle();

                        // After API verification, run issue verification
                        await RunIssueVerificationCycleAsync();

                        if (_cancellationTokenSource.Token.IsCancellationRequested)
                            break;
                    }
                    catch (OperationCanceledException)
                    {
                        _logger?.LogInformation("Verification cycle cancelled by user request");
                        break;
                    }
                    catch (Exception ex)
                    {
                        _logger?.LogError(ex, "Error during verification cycle. Will retry next cycle if in continuous mode.");
                        Console.WriteLine($"Error during verification cycle: {ex.Message}");
                        await LogExceptionAsync(ex, "Main", "VerificationCycle", "Cycle Execution Error");
                    }

                    if (continuousMode && !_cancellationTokenSource.Token.IsCancellationRequested)
                    {
                        _logger?.LogInformation("Sleeping for 5 minutes before next verification cycle...");
                        Console.WriteLine("Sleeping for 5 minutes before next verification cycle...");

                        try
                        {
                            await Task.Delay(TimeSpan.FromMinutes(5), _cancellationTokenSource.Token);
                        }
                        catch (OperationCanceledException)
                        {
                            break;
                        }

                        // Clear expired cache entries
                        _keyCache?.ClearExpired();
                        _logger?.LogInformation("Cache cleaned. Starting next verification cycle...");
                        Console.WriteLine("Cache cleaned. Starting next verification cycle...");
                    }

                } while (continuousMode && !_cancellationTokenSource.Token.IsCancellationRequested);
            }
            finally
            {
                _resourceMonitor?.Dispose();
                if (_serviceProvider is IAsyncDisposable asyncDisposable)
                    await asyncDisposable.DisposeAsync();
                else if (_serviceProvider is IDisposable disposable)
                    disposable.Dispose();
            }

            _logger?.LogInformation("Verifier shutdown completed");
            Console.WriteLine("Verifier shutdown completed");
        }

        private static async Task RunVerificationCycle()
        {
            var stopwatch = Stopwatch.StartNew();
            WebProxy? webProxy = null;

            using (var scope = _serviceProvider!.CreateScope())
            {
                var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();
                var batchLockingService = scope.ServiceProvider.GetRequiredService<BatchLockingService>();

                bool canRun = bool.Parse((await dbContext.ApplicationSettings
                    .SingleOrDefaultAsync(x => x.Key == "AllowVerifier", _cancellationTokenSource!.Token))?.Value ?? "false");

                if (!canRun)
                {
                    _logger?.LogInformation("Verifier is disabled in application settings. Skipping cycle.");
                    Console.WriteLine("Verifier is disabled in application settings. Skipping cycle.");
                    return;
                }

                //bool useProxy = await GetUseProxySettingAsync(dbContext, _logger);
                bool useProxy = false;
                if (useProxy && _httpClientFactory != null)
                {
                    _logger?.LogInformation("Proxy usage is enabled. Attempting to find a working proxy.");
                    webProxy = await GetWorkingProxyAsync(dbContext, _httpClientFactory, _logger);
                    if (webProxy != null)
                    {
                        _logger?.LogInformation("Using proxy: {WebProxyAddress}", webProxy.Address);
                    }
                    else
                    {
                        _logger?.LogWarning("Failed to obtain a working proxy. Proceeding with direct connection.");
                    }
                }
                else
                {
                    _logger?.LogInformation("Proxy usage is disabled or HttpClientFactory not available.");
                }

                // Acquire a batch lock
                var batch = await batchLockingService.AcquireBatchLockAsync(dbContext, 1000, _cancellationTokenSource.Token);
                
                if (batch == null)
                {
                    _logger?.LogInformation("No available keys to lock for verification. Another instance may be processing all available keys.");
                    Console.WriteLine("Info: No available keys to lock for verification.");
                    return;
                }

                long batchId = batch.Id;
                int errorKeys = 0;

                try
                {
                    // Update batch status to processing
                    await batchLockingService.UpdateBatchStatusAsync(dbContext, batchId, VerificationBatchStatus.Processing, _cancellationTokenSource.Token);

                    // Get the actual keys for this batch
                    // IMPORTANT: Apply the same ordering and limit as was used when creating the batch
                    // to ensure we only process the originally selected keys, not all keys in the range
                    var keysToProcessQuery = dbContext.APIKeys
                        .Where(k => k.Id >= batch.StartKeyId && k.Id <= batch.EndKeyId);
                    
                    var keysToProcess = await keysToProcessQuery
                        .OrderBy(k => k.LastCheckedUTC ?? DateTime.MinValue)
                        .ThenBy(k => k.Status == ApiStatusEnum.ValidNoCredits ? 2 : k.Status == ApiStatusEnum.Valid ? 1 : 0)
                        .Take(batch.KeyCount)  // Limit to the original batch size
                        .ToListAsync(_cancellationTokenSource.Token);
                    
                    _logger?.LogInformation("Batch {BatchId} loaded {KeyCount} keys from range {StartKeyId}-{EndKeyId} (originally selected {OriginalCount} keys)", 
                        batchId, keysToProcess.Count, batch.StartKeyId, batch.EndKeyId, batch.KeyCount);

                    int total = keysToProcess.Count;
                    if (total == 0)
                    {
                        _logger?.LogInformation("No keys found in locked batch {BatchId}.", batchId);
                        Console.WriteLine($"Info: No keys found in locked batch {batchId}.");
                        await batchLockingService.ReleaseBatchLockAsync(dbContext, batchId, true, null, _cancellationTokenSource.Token);
                        return;
                    }

                    _logger?.LogInformation("Processing batch {BatchId} with {Total} keys to verify.", batchId, total);
                    Console.WriteLine($"Info: Processing batch {batchId} with {total} keys to verify.");

                    var groupedKeys = GroupKeysByPattern(keysToProcess);
                    _logger?.LogInformation("Grouped keys into {GroupedKeysCount} pattern groups.", groupedKeys.Count);
                    Console.WriteLine($"Grouped keys into {groupedKeys.Count} pattern groups");

                    var counters = new CounterState();

                    // Process keys in batches
                    foreach (var group in groupedKeys)
                    {
                        if (_cancellationTokenSource.Token.IsCancellationRequested)
                            break;

                        string pattern = group.Key;
                        List<APIKey> keysInGroup = group.Value;
                        IApiKeyProvider? targetProvider = null;

                        if (_patternToProviderMap.TryGetValue(pattern, out targetProvider) && targetProvider != null)
                        {
                            _logger?.LogInformation("Processing {Count} keys matching pattern for provider: {TargetProviderProviderName}",
                                keysInGroup.Count, targetProvider.ProviderName);
                            Console.WriteLine($"Processing {keysInGroup.Count} keys matching pattern for provider: {targetProvider.ProviderName}");
                            await ProcessKeyGroupBatchedAsync(keysInGroup, targetProvider, counters, total, webProxy);
                        }
                        else
                        {
                            _logger?.LogInformation("Processing {Count} keys with unknown pattern ('{Pattern}') or provider not found. Will try all providers.",
                                keysInGroup.Count, pattern);
                            Console.WriteLine($"Processing {keysInGroup.Count} keys with unknown pattern or provider not found for pattern '{pattern}'");
                            await ProcessKeyGroupBatchedAsync(keysInGroup, null, counters, total, webProxy);
                        }
                    }

                    stopwatch.Stop();

                    // Update circuit breaker tripped count
                    counters.CircuitBreakerTrippedCount = _circuitBreakers?.GetTrippedCount() ?? 0;

                    // Calculate error keys
                    errorKeys = total - (counters.ValidCount + counters.InvalidCount + counters.SkippedCount);

                    // Update batch results
                    await batchLockingService.UpdateBatchResultsAsync(dbContext, batchId, 
                        counters.ValidCount, counters.InvalidCount, counters.SkippedCount, errorKeys, _cancellationTokenSource.Token);

                    _logger?.LogInformation("Batch {BatchId} completed in {ElapsedTotalMinutes:F2} minutes. Valid: {CountersValidCount}, Invalid/Errored: {CountersInvalidCount}, Skipped: {CountersSkippedCount}, Circuit Breakers Tripped: {CountersCircuitBreakerTrippedCount}",
                        batchId, stopwatch.Elapsed.TotalMinutes, counters.ValidCount, counters.InvalidCount, counters.SkippedCount, counters.CircuitBreakerTrippedCount);
                    Console.WriteLine($"\nBatch {batchId} completed in {stopwatch.Elapsed.TotalMinutes:F2} minutes");
                    Console.WriteLine($"Valid Keys: {counters.ValidCount} | Invalid/Errored Keys: {counters.InvalidCount} | Skipped (Deleted/Format): {counters.SkippedCount} | Circuit Breakers Tripped: {counters.CircuitBreakerTrippedCount}");
                    Console.WriteLine($"Check '{ErrorLogFile}' for details on errors and valid keys found.");

                    // Also record in the old VerificationBatchResult table for backwards compatibility
                    await dbContext.VerificationBatchResults.AddAsync(new VerificationBatchResult()
                    {
                        BatchSize = keysToProcess.Count,
                        InvalidKeys = counters.InvalidCount,
                        ValidKeys = counters.ValidCount,
                        SkippedKeys = counters.SkippedCount,
                        TimeTakenInMinutes = stopwatch.Elapsed.TotalMinutes,
                        VerificationDateUTC = DateTime.UtcNow
                    }, _cancellationTokenSource.Token);

                    await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);

                    // Release the batch lock successfully
                    await batchLockingService.ReleaseBatchLockAsync(dbContext, batchId, true, null, _cancellationTokenSource.Token);
                }
                catch (Exception ex)
                {
                    _logger?.LogError(ex, "Error processing batch {BatchId}", batchId);
                    
                    // Try to release the lock with error status
                    try
                    {
                        await batchLockingService.ReleaseBatchLockAsync(dbContext, batchId, false, ex.Message, _cancellationTokenSource.Token);
                    }
                    catch (Exception releaseEx)
                    {
                        _logger?.LogError(releaseEx, "Failed to release batch lock for batch {BatchId}", batchId);
                    }
                    
                    throw;
                }
            }
        }

        // Thread-safe batch update collection
        private static readonly ConcurrentBag<(long keyId, ApiStatusEnum status, ApiTypeEnum type, DateTime lastChecked, List<ModelInfo>? models)> _pendingUpdates = new();

        private static async Task ProcessKeyGroupBatchedAsync(
            List<APIKey> keysInGroup,
            IApiKeyProvider? targetProvider,
            CounterState counters,
            int totalKeys,
            WebProxy? webProxy)
        {
            // Process in batches to avoid overwhelming the database
            const int batchSize = 50;
            var batches = keysInGroup.Chunk(batchSize);

            foreach (var batch in batches)
            {
                if (_cancellationTokenSource!.Token.IsCancellationRequested)
                    break;

                // Get adaptive parallelism based on current system resources
                var currentParallelism = _resourceMonitor?.GetAdaptiveParallelism(
                    targetProvider != null ? MaxDegreeOfApiParallelism : MaxDegreeOfDbParallelism)
                    ?? MaxDegreeOfApiParallelism;

                // Use Parallel.ForEachAsync for better control
                var parallelOptions = new ParallelOptions
                {
                    MaxDegreeOfParallelism = currentParallelism,
                    CancellationToken = _cancellationTokenSource.Token
                };

                await Parallel.ForEachAsync(batch, parallelOptions, async (apiEntry, ct) =>
                {
                    await ProcessSingleKeyAsync(apiEntry, targetProvider, counters, totalKeys, webProxy);
                });

                // Batch update the database with invalidation tracking
                await BatchUpdateKeyStatusesWithInvalidationTrackingAsync(batch.Select(k => k.Id).ToList());
            }
        }

        private static async Task ProcessSingleKeyAsync(
            APIKey apiEntry,
            IApiKeyProvider? targetProvider,
            CounterState counters,
            int totalKeys,
            WebProxy? webProxy)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(apiEntry.ApiKey) || apiEntry.ApiKey.Length < 10)
                {
                    await LogMessageAsync($"Warning: Key with ID {apiEntry.Id} has invalid format, skipping.");
                    _pendingUpdates.Add((apiEntry.Id, ApiStatusEnum.Invalid, ApiTypeEnum.Unknown, DateTime.UtcNow, null));
                    Interlocked.Increment(ref counters.InvalidCount);
                    Interlocked.Increment(ref counters.ProcessedCount);
                    LogProgress(counters.ProcessedCount, totalKeys);
                    return;
                }

                // Check cache
                if (_keyCache!.TryGetValue(apiEntry.ApiKey, out var cachedTuple))
                {
                    var cachedValidationResult = cachedTuple.validationResult;
                    var cachedApiType = cachedTuple.Type;
                    ApiStatusEnum statusToSetFromCache = ConvertValidationAttemptStatusToApiStatus(cachedValidationResult.Status);
                    _pendingUpdates.Add((apiEntry.Id, statusToSetFromCache, cachedApiType, DateTime.UtcNow, cachedValidationResult.AvailableModels));

                    if (statusToSetFromCache == ApiStatusEnum.Valid) Interlocked.Increment(ref counters.ValidCount);
                    else if (statusToSetFromCache == ApiStatusEnum.Invalid) Interlocked.Increment(ref counters.InvalidCount);
                    else Interlocked.Increment(ref counters.SkippedCount);

                    Interlocked.Increment(ref counters.ProcessedCount);
                    LogProgress(counters.ProcessedCount, totalKeys);
                    return;
                }

                ValidationResult finalValidationResult;
                ApiTypeEnum finalApiType = ApiTypeEnum.Unknown;

                if (targetProvider != null)
                {
                    ValidationResult initialResult = await ValidateWithProviderAsync(apiEntry.ApiKey, targetProvider, webProxy);
                    if (initialResult.Status == ValidationAttemptStatus.Valid)
                    {
                        finalValidationResult = initialResult;
                        finalApiType = targetProvider.ApiType;
                    }
                    else
                    {
                        // Store the initial status to check if it was Unauthorized or Error
                        bool isInitialResultUnauthorized = initialResult.Status == ValidationAttemptStatus.Unauthorized;
                        bool isInitialResultError = initialResult.Status == ValidationAttemptStatus.HttpError || 
                                                    initialResult.Status == ValidationAttemptStatus.NetworkError || 
                                                    initialResult.Status == ValidationAttemptStatus.ProviderSpecificError;

                        var keySnippetForLog = apiEntry.ApiKey.Length > 8 ?
                            $"{apiEntry.ApiKey.Substring(0, 4)}...{apiEntry.ApiKey.Substring(apiEntry.ApiKey.Length - 4)}" :
                            apiEntry.ApiKey;

                        string initialErrorDetail = initialResult.Detail ?? $"status {initialResult.Status}";
                        if (initialResult.HttpStatusCode.HasValue)
                            initialErrorDetail = $"status {initialResult.HttpStatusCode.Value} ({initialResult.Status}) - {initialResult.Detail}";

                        _logger?.LogInformation("Key {ApiEntryId} ({KeySnippetForLog}) failed with {TargetProviderProviderName} ({InitialErrorDetail}). Trying all providers...",
                            apiEntry.Id, keySnippetForLog, targetProvider.ProviderName, initialErrorDetail);

                        var fallbackOutcome = await ValidateWithAllProvidersAsync(apiEntry.ApiKey, webProxy, targetProvider);

                        // Only override if we found a valid key with another provider
                        if (fallbackOutcome.validationResult.Status == ValidationAttemptStatus.Valid)
                        {
                            finalValidationResult = fallbackOutcome.validationResult;
                            finalApiType = fallbackOutcome.Type;
                            await LogMessageAsync($"Info: Key {apiEntry.Id} ({keySnippetForLog}) failed with {targetProvider.ProviderName} but validated with {_providers.FirstOrDefault(p => p.ApiType == fallbackOutcome.Type)?.ProviderName ?? "Unknown"}.");
                        }
                        else if (isInitialResultError)
                        {
                            // If initial result was an infrastructure/service error, preserve that status
                            // Don't mark as invalid just because other providers also failed
                            finalValidationResult = initialResult;
                            finalApiType = targetProvider.ApiType;
                            await LogMessageAsync($"Info: Key {apiEntry.Id} ({keySnippetForLog}) had service error with {targetProvider.ProviderName}. Preserving Error status.");
                        }
                        else if (isInitialResultUnauthorized)
                        {
                            // If initial was Unauthorized and fallback didn't find Valid, keep the initial Unauthorized
                            finalValidationResult = initialResult;
                            finalApiType = targetProvider.ApiType;
                            await LogMessageAsync($"Info: Key {apiEntry.Id} ({keySnippetForLog}) was Unauthorized with {targetProvider.ProviderName} and not valid with any other provider. Marking as Invalid.");
                        }
                        else
                        {
                            // Otherwise use the fallback result
                            finalValidationResult = fallbackOutcome.validationResult;
                            finalApiType = fallbackOutcome.Type;
                        }
                    }
                }
                else
                {
                    var fallbackOutcome = await ValidateWithAllProvidersAsync(apiEntry.ApiKey, webProxy);
                    finalValidationResult = fallbackOutcome.validationResult;
                    finalApiType = fallbackOutcome.Type;
                }

                _keyCache.Set(apiEntry.ApiKey, (finalValidationResult, finalApiType));
                ApiStatusEnum statusToSet = ConvertValidationAttemptStatusToApiStatus(finalValidationResult.Status);
                
                // Handle error tracking - note: actual increment/reset happens in BatchUpdateKeyStatusesWithInvalidationTrackingAsync
                if (statusToSet == ApiStatusEnum.Error)
                {
                    // Get max errors from database settings
                    int maxErrors = 10; // default
                    using (var scope = _serviceProvider!.CreateScope())
                    {
                        var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();
                        var setting = await dbContext.ApplicationSettings
                            .AsNoTracking()
                            .SingleOrDefaultAsync(x => x.Key == "VerifierMaxErrorsBeforeInvalid", _cancellationTokenSource!.Token);
                        if (setting != null && int.TryParse(setting.Value, out int parsedValue))
                        {
                            maxErrors = parsedValue;
                        }
                    }
                    
                    // Check if this key has exceeded max errors (current count + 1)
                    if ((apiEntry.ErrorCount + 1) >= maxErrors)
                    {
                        statusToSet = ApiStatusEnum.Invalid;
                        _logger?.LogWarning("Key {KeyId} will exceed max error threshold ({CurrentCount} + 1 >= {MaxErrors}). Marking as Invalid.", 
                            apiEntry.Id, apiEntry.ErrorCount, maxErrors);
                        await LogMessageAsync($"Warning: Key {apiEntry.Id} exceeded max error threshold ({apiEntry.ErrorCount + 1} errors). Marking as Invalid.");
                    }
                }
                
                _pendingUpdates.Add((apiEntry.Id, statusToSet, finalApiType, DateTime.UtcNow, finalValidationResult.AvailableModels));

                if (statusToSet == ApiStatusEnum.Valid)
                {
                    Interlocked.Increment(ref counters.ValidCount);
                    var keySnippet = apiEntry.ApiKey.Length > 8 ?
                        $"{apiEntry.ApiKey.Substring(0, 4)}...{apiEntry.ApiKey.Substring(apiEntry.ApiKey.Length - 4)}" :
                        apiEntry.ApiKey;
                    string providerNameForResult = _providers.FirstOrDefault(p => p.ApiType == finalApiType)?.ProviderName ?? "Unknown Provider";
                    var logMsg = $"Info: Key {apiEntry.Id} ({keySnippet}) validated for {providerNameForResult}.";
                    _logger?.LogInformation(logMsg);
                    Console.WriteLine(logMsg);
                    await LogMessageAsync(logMsg);
                }
                else if (statusToSet == ApiStatusEnum.Invalid)
                {
                    Interlocked.Increment(ref counters.InvalidCount);
                    await LogMessageAsync($"Warning: Key {apiEntry.Id} marked as invalid. Detail: {finalValidationResult.Detail} HTTP: {finalValidationResult.HttpStatusCode?.ToString() ?? "N/A"}");
                }
                else if (statusToSet == ApiStatusEnum.Error)
                {
                    // Don't count errors as invalid - they're infrastructure/service issues
                    Interlocked.Increment(ref counters.SkippedCount);
                    _logger?.LogDebug("Key {KeyId} skipped due to service error, not marking as invalid", apiEntry.Id);
                    await LogMessageAsync($"Info: Key {apiEntry.Id} skipped due to service error. Detail: {finalValidationResult.Detail}");
                }
            }
            catch (Exception ex)
            {
                await LogExceptionAsync(ex, "ProcessSingleKey", apiEntry.Id.ToString(), "Task Execution Error");
                Interlocked.Increment(ref counters.InvalidCount);
            }
            finally
            {
                Interlocked.Increment(ref counters.ProcessedCount);
                LogProgress(counters.ProcessedCount, totalKeys);
            }
        }

        private static async Task BatchUpdateKeyStatusesWithInvalidationTrackingAsync(List<long> keyIds)
        {
            if (!keyIds.Any()) return;

            using var scope = _serviceProvider!.CreateScope();
            var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();

            // Get all pending updates for these key IDs
            var updates = new List<(long Id, ApiStatusEnum status, ApiTypeEnum type, DateTime lastChecked, List<ModelInfo>? models)>();
            var tempList = _pendingUpdates.ToList(); // Snapshot

            foreach (var keyId in keyIds)
            {
                var update = tempList.FirstOrDefault(u => u.keyId == keyId);
                if (update != default)
                {
                    updates.Add((update.keyId, update.status, update.type, update.lastChecked, update.models));
                    // Note: We'll clear _pendingUpdates after successful save
                }
            }

            if (!updates.Any()) 
            {
                _logger?.LogWarning("No pending updates found for key IDs: {KeyIds}", string.Join(", ", keyIds));
                return;
            }

            _logger?.LogInformation("Processing batch update for {Count} keys: {KeyIds}", 
                updates.Count, string.Join(", ", updates.Select(u => u.Id)));

            // Use execution strategy to handle transactions properly with retry logic
            var executionStrategy = dbContext.Database.CreateExecutionStrategy();
            await executionStrategy.ExecuteAsync(async () =>
            {
                // Use Serializable isolation to prevent concurrent updates
                using var transaction = await dbContext.Database.BeginTransactionAsync(System.Data.IsolationLevel.Serializable, _cancellationTokenSource!.Token);
                try
                {
                    // Batch fetch keys with locking to prevent concurrent updates
                    const int chunkSize = 1000;
                    var keyChunks = keyIds.Chunk(chunkSize);
                    var allKeys = new List<APIKey>();

                    foreach (var chunk in keyChunks)
                    {
                        // First get the keys normally - MUST use AsTracking to enable updates
                        var keys = await dbContext.APIKeys
                            .AsTracking()  // Enable change tracking for updates
                            .Where(k => chunk.Contains(k.Id))
                            .Include(k => k.References)
                            .ToListAsync(_cancellationTokenSource.Token);
                        
                        // The RepeatableRead isolation level should provide sufficient protection
                        // against concurrent updates without needing explicit row locking
                        
                        allKeys.AddRange(keys);
                    }

                    var invalidations = new List<KeyInvalidation>();
                    var patternUpdates = new Dictionary<(string pattern, string provider), (int valid, int invalid)>();

                    foreach (var key in allKeys)
                    {
                        var update = updates.FirstOrDefault(u => u.Id == key.Id);
                        if (update != default)
                        {
                            var previousStatus = key.Status;
                            var previousType = key.ApiType;

                            key.LastCheckedUTC = update.lastChecked;

                            // Track pattern effectiveness
                            foreach (var pattern in _compiledRegexPatterns)
                            {
                                if (pattern.Value.IsMatch(key.ApiKey))
                                {
                                    var provider = _patternToProviderMap.ContainsKey(pattern.Key) ?
                                        _patternToProviderMap[pattern.Key].ProviderName : "Unknown";

                                    var patternKey = (pattern.Key, provider);
                                    if (!patternUpdates.ContainsKey(patternKey))
                                        patternUpdates[patternKey] = (0, 0);

                                    if (update.status == ApiStatusEnum.Valid)
                                    {
                                        var current = patternUpdates[patternKey];
                                        patternUpdates[patternKey] = (current.valid + 1, current.invalid);
                                    }
                                    else if (update.status == ApiStatusEnum.Invalid)
                                    {
                                        var current = patternUpdates[patternKey];
                                        patternUpdates[patternKey] = (current.valid, current.invalid + 1);
                                    }
                                    break;
                                }
                            }

                            // Log the update details for debugging
                            _logger?.LogDebug("Updating key {KeyId}: Status {OldStatus} -> {NewStatus}, Type {OldType} -> {NewType}", 
                                key.Id, previousStatus, update.status, previousType, update.type);

                            // Always update status
                            key.Status = update.status;

                            // Handle error count tracking
                            if (update.status == ApiStatusEnum.Error)
                            {
                                // Increment error count
                                key.ErrorCount++;
                                _logger?.LogDebug("Key {KeyId} error count incremented to {ErrorCount}", key.Id, key.ErrorCount);
                            }
                            else if (update.status == ApiStatusEnum.Valid)
                            {
                                // Reset error count on successful validation
                                if (key.ErrorCount > 0)
                                {
                                    _logger?.LogDebug("Key {KeyId} validated successfully, resetting error count from {ErrorCount} to 0", key.Id, key.ErrorCount);
                                    key.ErrorCount = 0;
                                }
                            }

                            if (update.status == ApiStatusEnum.Valid)
                            {
                                key.ApiType = update.type;
                            }
                            else if (update.type != ApiTypeEnum.Unknown)
                            {
                                key.ApiType = update.type;
                            }

                            // Track invalidations - key went from Valid to Invalid
                            if (previousStatus == ApiStatusEnum.Valid &&
                                (update.status == ApiStatusEnum.Invalid || update.status == ApiStatusEnum.Error))
                            {
                                var invalidation = new KeyInvalidation
                                {
                                    ApiKeyId = key.Id,
                                    InvalidatedAt = DateTime.UtcNow,
                                    WasValid = true,
                                    DaysActive = (DateTime.UtcNow - key.FirstFoundUTC).Days,
                                    InvalidationReason = update.status == ApiStatusEnum.Invalid ?
                                        "Key became unauthorized" : "Key validation error",
                                    PreviousStatus = previousStatus.ToString(),
                                    HttpStatusCode = null, // We'd need to pass this through from validation
                                    ConfirmedFixed = false,
                                    FixedAt = null
                                };

                                invalidations.Add(invalidation);

                                _logger?.LogInformation("Key {KeyId} invalidated after {DaysActive} days. Previous type: {PreviousType}",
                                    key.Id, invalidation.DaysActive, previousType);
                            }

                            // Track potential key rotations
                            if (previousStatus == ApiStatusEnum.Invalid && update.status == ApiStatusEnum.Valid)
                            {
                                // This might be a new key in the same location
                                await CheckForKeyRotationAsync(dbContext, key);
                            }

                            // Process model information if available
                            if (update.status == ApiStatusEnum.Valid && update.models != null && update.models.Any())
                            {
                                await ProcessKeyModelsAsync(dbContext, key.Id, update.type, update.models);
                            }
                        }
                    }

                    // Save API keys
                    await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);

                    // Save invalidations
                    if (invalidations.Any())
                    {
                        dbContext.KeyInvalidations.AddRange(invalidations);
                        await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                        _logger?.LogInformation("Tracked {Count} key invalidations", invalidations.Count);
                    }

                    // Update pattern effectiveness
                    await UpdatePatternEffectivenessAsync(dbContext, patternUpdates);

                    await transaction.CommitAsync(_cancellationTokenSource.Token);

                    _logger?.LogDebug("Batch updated {Count} keys with invalidation tracking", updates.Count);
                }
                catch (Exception ex)
                {
                    await transaction.RollbackAsync(_cancellationTokenSource.Token);
                    _logger?.LogError(ex, "Failed to batch update key statuses with tracking");
                    await LogExceptionAsync(ex, "BatchUpdateKeyStatusesWithInvalidationTracking", string.Join(",", keyIds), "Database Update Error");
                    throw; // Re-throw to handle at higher level
                }
            });

            // Clear processed updates from the bag - only after successful execution
            var processedIds = updates.Select(u => u.Id).ToHashSet();
            var remainingUpdates = _pendingUpdates.Where(u => !processedIds.Contains(u.keyId)).ToList();
            _pendingUpdates.Clear();
            foreach (var update in remainingUpdates)
            {
                _pendingUpdates.Add(update);
            }
        }

        private static async Task CheckForKeyRotationAsync(DBContext dbContext, APIKey currentKey)
        {
            // Look for other keys from the same repository that were recently invalidated
            var recentlyInvalidated = await dbContext.APIKeys
                .Where(k => k.Id != currentKey.Id &&
                           k.Status == ApiStatusEnum.Invalid &&
                           k.LastCheckedUTC > DateTime.UtcNow.AddDays(-7))
                .Include(k => k.References)
                .ToListAsync(_cancellationTokenSource!.Token);

            foreach (var oldKey in recentlyInvalidated)
            {
                // Check if they share any common repositories
                var currentRepos = currentKey.References.Select(r => r.RepoURL).ToHashSet();
                var oldRepos = oldKey.References.Select(r => r.RepoURL).ToHashSet();

                var commonRepos = currentRepos.Intersect(oldRepos).ToList();

                if (commonRepos.Any())
                {
                    // Potential key rotation detected
                    var rotation = new KeyRotation
                    {
                        OldKeyId = oldKey.Id,
                        NewKeyId = currentKey.Id,
                        RotatedAt = DateTime.UtcNow,
                        RepoUrl = commonRepos.First()!,
                        OldKeyDaysActive = ((oldKey.LastCheckedUTC ?? oldKey.FirstFoundUTC) - oldKey.FirstFoundUTC).Days
                    };

                    dbContext.KeyRotations.Add(rotation);
                    _logger?.LogInformation("Detected potential key rotation in {RepoUrl}: {OldKeyId} -> {NewKeyId}",
                        rotation.RepoUrl, oldKey.Id, currentKey.Id);
                }
            }
        }

        private static async Task UpdatePatternEffectivenessAsync(DBContext dbContext, Dictionary<(string pattern, string provider), (int valid, int invalid)> updates)
        {
            foreach (var update in updates)
            {
                var pattern = await dbContext.PatternEffectiveness
                    .FirstOrDefaultAsync(p => p.Pattern == update.Key.pattern && p.ProviderName == update.Key.provider, _cancellationTokenSource!.Token);

                if (pattern == null)
                {
                    pattern = new PatternEffectiveness
                    {
                        Pattern = update.Key.pattern,
                        ProviderName = update.Key.provider,
                        TotalMatches = update.Value.valid + update.Value.invalid,
                        ValidKeys = update.Value.valid,
                        InvalidKeys = update.Value.invalid,
                        FirstSeen = DateTime.UtcNow,
                        LastUpdated = DateTime.UtcNow
                    };
                    dbContext.PatternEffectiveness.Add(pattern);
                }
                else
                {
                    pattern.TotalMatches += update.Value.valid + update.Value.invalid;
                    pattern.ValidKeys += update.Value.valid;
                    pattern.InvalidKeys += update.Value.invalid;
                    pattern.LastUpdated = DateTime.UtcNow;
                }
            }

            try
            {
                await dbContext.SaveChangesAsync(_cancellationTokenSource!.Token);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Failed to update pattern effectiveness");
            }
        }

        private static async Task RunIssueVerificationCycleAsync()
        {
            var stopwatch = Stopwatch.StartNew();

            try
            {
                using var scope = _serviceProvider!.CreateScope();
                var dbContext = scope.ServiceProvider.GetRequiredService<DBContext>();

                // Check if issue verification is enabled
                bool canRunIssueVerification = bool.Parse((await dbContext.ApplicationSettings
                    .SingleOrDefaultAsync(x => x.Key == "AllowIssueVerification", _cancellationTokenSource!.Token))?.Value ?? "false");

                if (!canRunIssueVerification)
                {
                    _logger?.LogInformation("Issue verification is disabled in application settings. Skipping cycle.");
                    Console.WriteLine("Issue verification is disabled in application settings. Skipping cycle.");
                    return;
                }

                // Get GitHub token for API access
                var githubTokenRecord = await dbContext.SearchProviderTokens
                    .Where(t => t.IsEnabled && t.SearchProvider == SearchProviderEnum.GitHub)
                    .OrderBy(t => t.LastUsedUTC ?? DateTime.MinValue)
                    .FirstOrDefaultAsync(_cancellationTokenSource.Token);

                string? githubToken = null;
                if (githubTokenRecord != null)
                {
                    githubToken = githubTokenRecord.Token;
                    githubTokenRecord.LastUsedUTC = DateTime.UtcNow;
                    await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                }

                if (string.IsNullOrEmpty(githubToken))
                {
                    _logger?.LogWarning("GitHub token not configured. Issue verification will run without authentication.");
                }

                // Get unprocessed issue submissions (those not yet verified) - process in larger batches
                var unverifiedSubmissions = await dbContext.IssueSubmissionTrackings
                    .Where(ist => !dbContext.IssueVerifications
                        .Any(iv => iv.IssueSubmissionTrackingId == ist.Id))
                    .OrderBy(ist => ist.SubmittedAt)
                    .Take(100) // Increased batch size
                    .ToListAsync(_cancellationTokenSource.Token);

                if (!unverifiedSubmissions.Any())
                {
                    _logger?.LogInformation("No unverified issue submissions found.");
                    Console.WriteLine("Info: No unverified issue submissions found.");
                    return;
                }

                _logger?.LogInformation("Found {Count} unverified issue submissions to process.", unverifiedSubmissions.Count);
                Console.WriteLine($"Info: Found {unverifiedSubmissions.Count} unverified issue submissions to process.");

                // Create a single HttpClient for all GitHub requests
                var httpClient = _httpClientFactory!.CreateClient("github");
                httpClient.Timeout = TimeSpan.FromSeconds(30);

                var githubService = new GitHubIssueService(httpClient, _logger as ILogger<GitHubIssueService>, githubToken);
                var leaderboardService = new SnitchLeaderboardService(dbContext, _logger as ILogger<SnitchLeaderboardService>);

                int processedCount = 0;
                int foundIssuesCount = 0;
                int retryCount = 0;
                const int maxRetries = 3;

                // Process in smaller batches to avoid overwhelming GitHub API
                const int batchSize = 10;
                var batches = unverifiedSubmissions.Chunk(batchSize);

                foreach (var batch in batches)
                {
                    if (_cancellationTokenSource.Token.IsCancellationRequested)
                        break;

                    var verificationResults = new List<IssueVerification>();

                    foreach (var submission in batch)
                    {
                        IssueVerificationResult? verificationResult = null;
                        retryCount = 0;

                        while (retryCount < maxRetries)
                        {
                            try
                            {
                                // Create API key snippet for searching (last 4 characters)
                                var apiKey = await dbContext.APIKeys.FindAsync([submission.ApiKeyId], _cancellationTokenSource.Token);
                                var keySnippet = string.Empty;
                                var apiKeyValue = apiKey?.ApiKey;
                                if (!string.IsNullOrEmpty(apiKeyValue) && apiKeyValue.Length > 4)
                                {
                                    keySnippet = apiKeyValue.Substring(apiKeyValue.Length - 4);
                                }

                                _logger?.LogDebug("Checking for issues in {RepoUrl} for {ApiType} key",
                                    submission.RepoUrl, submission.ApiType);

                                // Check GitHub for related issues with exponential backoff
                                verificationResult = await githubService.CheckForIssueAsync(
                                    submission.RepoUrl,
                                    keySnippet,
                                    submission.ApiType);

                                if (verificationResult.Status != IssueVerificationStatus.VerificationError)
                                    break; // Success

                                retryCount++;
                                if (retryCount < maxRetries)
                                {
                                    var delay = TimeSpan.FromSeconds(Math.Pow(2, retryCount));
                                    _logger?.LogDebug("Retry {RetryCount} for GitHub issue check after {Delay}s",
                                        retryCount, delay.TotalSeconds);
                                    await Task.Delay(delay, _cancellationTokenSource.Token);
                                }
                            }
                            catch (Exception ex)
                            {
                                _logger?.LogError(ex, "Error processing issue submission {SubmissionId} (attempt {Attempt})",
                                    submission.Id, retryCount + 1);

                                retryCount++;
                                if (retryCount >= maxRetries)
                                {
                                    await LogExceptionAsync(ex, "RunIssueVerificationCycle", submission.Id.ToString(), "Issue Verification Error");
                                    verificationResult = new IssueVerificationResult
                                    {
                                        Status = IssueVerificationStatus.VerificationError,
                                        ErrorMessage = ex.Message
                                    };
                                }
                                else
                                {
                                    await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, retryCount)), _cancellationTokenSource.Token);
                                }
                            }
                        }

                        if (verificationResult != null)
                        {
                            // Create verification record
                            var issueVerification = new IssueVerification
                            {
                                IssueSubmissionTrackingId = submission.Id,
                                RepoUrl = submission.RepoUrl,
                                Status = verificationResult.Status,
                                IssueTitle = verificationResult.IssueTitle,
                                GitHubIssueNumber = verificationResult.IssueNumber,
                                GitHubIssueUrl = verificationResult.IssueUrl,
                                IssueCreatedAt = verificationResult.CreatedAt,
                                IssueClosedAt = verificationResult.ClosedAt,
                                FirstCheckedAt = DateTime.UtcNow,
                                LastCheckedAt = DateTime.UtcNow,
                                SubmitterIP = submission.UserIP,
                                GitHubUsername = verificationResult.GitHubUsername,
                                GitHubAvatarUrl = verificationResult.GitHubAvatarUrl,
                                GitHubUserId = verificationResult.GitHubUserId,
                                GitHubDisplayName = verificationResult.GitHubDisplayName
                            };

                            verificationResults.Add(issueVerification);

                            // Update leaderboard if an issue was found
                            if (verificationResult.Status == IssueVerificationStatus.Open ||
                                verificationResult.Status == IssueVerificationStatus.Closed)
                            {
                                foundIssuesCount++;

                                _logger?.LogInformation("Found issue for submission {SubmissionId}: {IssueUrl} (Status: {Status})",
                                    submission.Id, verificationResult.IssueUrl, verificationResult.Status);
                            }

                            processedCount++;
                        }
                    }

                    // Batch save verifications
                    if (verificationResults.Any())
                    {
                        dbContext.IssueVerifications.AddRange(verificationResults);
                        await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);

                        // Process leaderboard updates
                        foreach (var verification in verificationResults.Where(v =>
                            v.Status == IssueVerificationStatus.Open || v.Status == IssueVerificationStatus.Closed))
                        {
                            await leaderboardService.ProcessIssueVerificationAsync(verification);
                        }
                    }

                    // Adaptive delay based on remaining rate limit
                    await Task.Delay(TimeSpan.FromSeconds(2), _cancellationTokenSource.Token);
                }

                stopwatch.Stop();
                _logger?.LogInformation("Issue verification cycle completed in {ElapsedMinutes:F2} minutes. Processed: {ProcessedCount}, Found Issues: {FoundIssuesCount}",
                    stopwatch.Elapsed.TotalMinutes, processedCount, foundIssuesCount);
                Console.WriteLine($"\nIssue verification cycle completed in {stopwatch.Elapsed.TotalMinutes:F2} minutes");
                Console.WriteLine($"Processed: {processedCount} submissions | Found Issues: {foundIssuesCount}");
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Error during issue verification cycle");
                Console.WriteLine($"Error during issue verification cycle: {ex.Message}");
                await LogExceptionAsync(ex, "RunIssueVerificationCycle", "General", "Cycle Execution Error");
            }
        }

        private static void ConfigureServices(IServiceCollection services)
        {
            // Add configuration
            services.AddSingleton(_configuration!);

            services.AddLogging(builder =>
            {
                builder
                    .SetMinimumLevel(LogLevel.Information)
                    .AddFilter("Microsoft", LogLevel.Warning)
                    .AddFilter("System", LogLevel.Warning)
                    .AddFilter("UnsecuredAPIKeys.Bots.Verifier.Verifier_Program", LogLevel.Debug)
                    .AddConsole();
            });

            // Error tracking is now handled via database ErrorCount field
            // Configuration for max errors is read directly when needed

            // Configure HttpClientFactory with proper settings
            services.AddHttpClient("default", client =>
            {
                client.Timeout = TimeSpan.FromSeconds(30);
                client.DefaultRequestHeaders.Add("User-Agent", "UnsecuredAPIKeys-Verifier/1.0");
            })
            .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
            {
                AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate,
                MaxConnectionsPerServer = 100,
                UseProxy = false
            });

            services.AddHttpClient("github", client =>
            {
                client.BaseAddress = new Uri("https://api.github.com/");
                client.Timeout = TimeSpan.FromSeconds(30);
                client.DefaultRequestHeaders.Accept.Add(new System.Net.Http.Headers.MediaTypeWithQualityHeaderValue("application/vnd.github.v3+json"));
                client.DefaultRequestHeaders.UserAgent.Add(new System.Net.Http.Headers.ProductInfoHeaderValue("UnsecuredAPIKeys-Bot", "1.0"));
            })
            .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
            {
                AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate,
                MaxConnectionsPerServer = 10
            });

            // Configure HttpClient for proxy testing
            services.AddHttpClient("proxy-test", client =>
            {
                client.Timeout = TimeSpan.FromSeconds(10);
                client.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
            });

            services.AddHttpClient(); // Generic factory for providers

            services.AddDbContext<DBContext>(options =>
            {
                options.UseNpgsql("Host=localhost;Database=UnsecuredAPIKeys;Username=postgres;Password=your_password;Port=5432", npgsqlOptions =>
                {
                    npgsqlOptions.UseQuerySplittingBehavior(QuerySplittingBehavior.SplitQuery);
                    npgsqlOptions.EnableRetryOnFailure(3);
                });
                options.EnableServiceProviderCaching();
                options.UseQueryTrackingBehavior(QueryTrackingBehavior.NoTracking);
            });

            services.AddScoped<GitHubIssueService>();
            services.AddScoped<SnitchLeaderboardService>();
            services.AddScoped<BatchLockingService>();
        }

        private static async Task<bool> GetUseProxySettingAsync(DBContext dbContext, ILogger? logger)
        {
            try
            {
                var setting = await dbContext.ApplicationSettings
                    .AsNoTracking()
                    .SingleOrDefaultAsync(x => x.Key == "UseProxy", _cancellationTokenSource!.Token);
                if (setting != null && bool.TryParse(setting.Value, out bool useProxy))
                {
                    return useProxy;
                }
                logger?.LogWarning("UseProxy setting not found or invalid in ApplicationSettings. Defaulting to false.");
            }
            catch (Exception ex)
            {
                logger?.LogError(ex, "Error fetching UseProxy setting. Defaulting to false.");
            }
            return false;
        }

        private static async Task<bool> TestProxyAsync(Proxy proxyToTest, IHttpClientFactory httpClientFactory, ILogger? logger)
        {
            if (string.IsNullOrWhiteSpace(proxyToTest.ProxyUrl))
            {
                logger?.LogWarning("Proxy URL for ID {Id} is null or empty. Test failed.", proxyToTest.Id);
                return false;
            }

            try
            {
                var httpClient = httpClientFactory.CreateClient("proxy-test");
                var proxyUri = new Uri(proxyToTest.ProxyUrl);

                using var handler = new HttpClientHandler
                {
                    Proxy = new WebProxy(proxyUri),
                    UseProxy = true,
                };

                using var client = new HttpClient(handler);
                client.Timeout = TimeSpan.FromSeconds(10);

                using var request = new HttpRequestMessage(HttpMethod.Get, "http://detectportal.firefox.com/success.txt");
                request.Headers.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");

                logger?.LogDebug("Testing proxy {ProxyUrl} (ID: {Id})", proxyToTest.ProxyUrl, proxyToTest.Id);

                var response = await client.SendAsync(request, _cancellationTokenSource!.Token);
                if (response.IsSuccessStatusCode)
                {
                    logger?.LogInformation("Proxy {ProxyUrl} (ID: {Id}) test successful.", proxyToTest.ProxyUrl, proxyToTest.Id);
                    return true;
                }

                logger?.LogWarning("Proxy {ProxyUrl} (ID: {Id}) test failed (Status: {ResponseStatusCode}).",
                    proxyToTest.ProxyUrl, proxyToTest.Id, response.StatusCode);
                return false;
            }
            catch (Exception ex)
            {
                logger?.LogError(ex, "Error testing proxy {ProxyUrl} (ID: {Id}). Test failed.", proxyToTest.ProxyUrl, proxyToTest.Id);
                return false;
            }
        }

        private static async Task<WebProxy?> GetWorkingProxyAsync(DBContext dbContext, IHttpClientFactory httpClientFactory, ILogger? logger)
        {
            const int maxProxyAttempts = 5;

            for (int attempt = 1; attempt <= maxProxyAttempts; attempt++)
            {
                var proxyToTry = await dbContext.Proxies
                    .OrderBy(p => p.LastUsedUTC)
                    .FirstOrDefaultAsync(_cancellationTokenSource!.Token);

                if (proxyToTry == null)
                {
                    logger?.LogInformation("No more proxies available in the database to test.");
                    break;
                }

                logger?.LogInformation("Proxy attempt {Attempt}/{MaxProxyAttempts}: Testing proxy {ProxyUrl} (ID: {Id}).",
                    attempt, maxProxyAttempts, proxyToTry.ProxyUrl, proxyToTry.Id);

                if (await TestProxyAsync(proxyToTry, httpClientFactory, logger))
                {
                    proxyToTry.LastUsedUTC = DateTime.UtcNow;
                    try
                    {
                        await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                        logger?.LogInformation("Proxy {ProxyUrl} (ID: {Id}) is working. LastUsedUTC updated.",
                            proxyToTry.ProxyUrl, proxyToTry.Id);
                        return new WebProxy(new Uri(proxyToTry.ProxyUrl));
                    }
                    catch (Exception ex)
                    {
                        logger?.LogError(ex, "Failed to update LastUsedUTC for working proxy {ProxyUrl} (ID: {Id}). Using it anyway.",
                            proxyToTry.ProxyUrl, proxyToTry.Id);
                        return new WebProxy(new Uri(proxyToTry.ProxyUrl));
                    }
                }
                else
                {
                    logger?.LogWarning("Proxy {ProxyUrl} (ID: {Id}) failed test.", proxyToTry.ProxyUrl, proxyToTry.Id);
                    proxyToTry.LastUsedUTC = DateTime.UtcNow; // Move to back of queue

                    try
                    {
                        await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                    }
                    catch (Exception ex)
                    {
                        logger?.LogError(ex, "Failed to update LastUsedUTC for failed proxy {ProxyUrl} (ID: {Id}).",
                            proxyToTry.ProxyUrl, proxyToTry.Id);
                    }

                    if (attempt == maxProxyAttempts)
                    {
                        logger?.LogWarning("Max proxy attempts ({MaxProxyAttempts}) reached. Deleting last failed proxy: {ProxyUrl} (ID: {Id}).",
                            maxProxyAttempts, proxyToTry.ProxyUrl, proxyToTry.Id);
                        dbContext.Proxies.Remove(proxyToTry);

                        try
                        {
                            await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                            logger?.LogInformation("Successfully deleted proxy {ProxyUrl} (ID: {Id}).",
                                proxyToTry.ProxyUrl, proxyToTry.Id);
                        }
                        catch (Exception ex)
                        {
                            logger?.LogError(ex, "Failed to delete proxy {ProxyUrl} (ID: {Id}).",
                                proxyToTry.ProxyUrl, proxyToTry.Id);
                        }
                    }
                }
            }

            logger?.LogInformation("Could not find a working proxy after {MaxProxyAttempts} attempts. Proceeding with direct connection.",
                maxProxyAttempts);
            return null;
        }

        private static Dictionary<string, List<APIKey>> GroupKeysByPattern(List<APIKey> keys)
        {
            var result = new Dictionary<string, List<APIKey>>();
            var unmatched = new List<APIKey>();

            foreach (var key in keys)
            {
                if (string.IsNullOrWhiteSpace(key.ApiKey) || key.ApiKey.Length < 10)
                {
                    if (!result.ContainsKey("invalid_format"))
                        result["invalid_format"] = [];
                    result["invalid_format"].Add(key);
                    continue;
                }

                bool matched = false;
                foreach (var patternEntry in _compiledRegexPatterns)
                {
                    if (patternEntry.Value.IsMatch(key.ApiKey))
                    {
                        if (!result.ContainsKey(patternEntry.Key))
                            result[patternEntry.Key] = [];
                        result[patternEntry.Key].Add(key);
                        matched = true;
                        break;
                    }
                }

                if (!matched)
                    unmatched.Add(key);
            }

            if (unmatched.Count > 0)
                result["unmatched"] = unmatched;

            return result.OrderBy(x => x.Value.Count).ToDictionary(x => x.Key, x => x.Value);
        }

        private static ApiStatusEnum ConvertValidationAttemptStatusToApiStatus(ValidationAttemptStatus attemptStatus)
        {
            return attemptStatus switch
            {
                ValidationAttemptStatus.Valid => ApiStatusEnum.Valid,
                ValidationAttemptStatus.Unauthorized => ApiStatusEnum.Invalid,  // ONLY this means bad key!
                ValidationAttemptStatus.HttpError => ApiStatusEnum.Error,       // Service issues - don't mark as invalid
                ValidationAttemptStatus.NetworkError => ApiStatusEnum.Error,    // Infrastructure issues - don't mark as invalid
                ValidationAttemptStatus.ProviderSpecificError => ApiStatusEnum.Error,  // Provider issues - don't mark as invalid
                _ => ApiStatusEnum.Error
            };
        }

        private static async Task<ValidationResult> ValidateWithProviderAsync(string apiKey, IApiKeyProvider provider, WebProxy? webProxy)
        {
            try
            {
                var breaker = _circuitBreakers!.GetBreaker(provider.ProviderName);

                // Check if circuit breaker is open
                if (breaker.IsOpen)
                {
                    await LogMessageAsync($"Circuit breaker is open for provider {provider.ProviderName}. Skipping validation attempt.");
                    return ValidationResult.HasProviderSpecificError("Circuit breaker is open - too many recent failures");
                }

                if (_httpClientFactory == null)
                {
                    await LogMessageAsync($"Error: HttpClientFactory is null in ValidateWithProviderAsync for {provider.ProviderName}.");
                    return ValidationResult.HasProviderSpecificError("HttpClientFactory not available.");
                }

                var result = await provider.ValidateKeyAsync(apiKey, _httpClientFactory, webProxy);

                // Update circuit breaker based on result
                if (result.Status == ValidationAttemptStatus.Valid)
                {
                    breaker.RecordSuccess();
                }
                else if (result.Status == ValidationAttemptStatus.NetworkError ||
                         result.Status == ValidationAttemptStatus.HttpError)
                {
                    breaker.RecordFailure();
                }

                return result;
            }
            catch (Exception ex)
            {
                await LogExceptionAsync(ex, provider.ProviderName, apiKey, "Provider ValidateKeyAsync Call Error");
                _circuitBreakers?.GetBreaker(provider.ProviderName).RecordFailure();
                return ValidationResult.HasProviderSpecificError($"Exception during {provider.ProviderName}.ValidateKeyAsync: {ex.Message}");
            }
        }

        // Get all providers whose patterns match the given API key
        private static List<IApiKeyProvider> GetMatchingProviders(string apiKey)
        {
            var matchingProviders = new List<IApiKeyProvider>();

            foreach (var provider in _providers)
            {
                foreach (var pattern in provider.RegexPatterns)
                {
                    if (_compiledRegexPatterns.TryGetValue(pattern, out var regex) && regex.IsMatch(apiKey))
                    {
                        matchingProviders.Add(provider);
                        break; // Found a match for this provider, no need to check other patterns
                    }
                }
            }

            return matchingProviders;
        }

        private static async Task<(ValidationResult validationResult, ApiTypeEnum Type)> ValidateWithAllProvidersAsync(string apiKey, WebProxy? webProxy, IApiKeyProvider? excludeProvider = null)
        {
            if (_httpClientFactory == null)
            {
                await LogMessageAsync($"Error: HttpClientFactory is null in ValidateWithAllProvidersAsync.");
                return (ValidationResult.HasProviderSpecificError("HttpClientFactory not available."), ApiTypeEnum.Unknown);
            }

            // OPTIMIZATION: Only try providers whose patterns match the key
            var matchingProviders = GetMatchingProviders(apiKey);
            
            // Exclude the already-tried provider if specified
            if (excludeProvider != null)
            {
                matchingProviders = matchingProviders.Where(p => p.ProviderName != excludeProvider.ProviderName).ToList();
            }

            if (!matchingProviders.Any())
            {
                var keySnippet = apiKey.Length > 8 ?
                    $"{apiKey.Substring(0, 4)}...{apiKey.Substring(apiKey.Length - 4)}" :
                    apiKey;
                _logger?.LogDebug("No providers have patterns matching key: {KeySnippet}", keySnippet);
                await LogMessageAsync($"No providers have patterns matching key: {keySnippet}");
                return (ValidationResult.HasProviderSpecificError("No providers match this key pattern"), ApiTypeEnum.Unknown);
            }

            _logger?.LogDebug("Found {Count} providers with matching patterns for key validation", matchingProviders.Count);

            foreach (var provider in matchingProviders)
            {
                try
                {
                    // Skip if circuit breaker is open
                    var breaker = _circuitBreakers!.GetBreaker(provider.ProviderName);
                    if (breaker.IsOpen)
                    {
                        _logger?.LogDebug("Skipping {Provider} - circuit breaker is open", provider.ProviderName);
                        continue;
                    }

                    _logger?.LogDebug("Attempting validation with {Provider}", provider.ProviderName);
                    var result = await ValidateWithProviderAsync(apiKey, provider, webProxy);

                    if (result.Status == ValidationAttemptStatus.Valid)
                    {
                        return (result, provider.ApiType);
                    }
                }
                catch (Exception ex)
                {
                    await LogExceptionAsync(ex, provider.ProviderName, apiKey, "Provider ValidateKeyAsync Call Error in AllProviders");
                }
            }

            return (ValidationResult.HasProviderSpecificError("Key was not validated by any pattern-matching provider"), ApiTypeEnum.Unknown);
        }

        private static void LogProgress(int processed, int total)
        {
            if (processed % 100 == 0 || processed == total)
            {
                _logger?.LogInformation("Progress: Verified {Processed}/{Total} keys... ({D:F1}%)",
                    processed, total, processed * 100.0 / total);
                Console.WriteLine($"Progress: Verified {processed}/{total} keys... ({processed * 100.0 / total:F1}%)");
            }
        }

        private static async Task LogExceptionAsync(Exception ex, string providerOrContext, string apiKeyOrId, string errorType = "General Error")
        {
            string keyIdentifier = apiKeyOrId ?? "N/A";
            var logLine = $"[{DateTime.UtcNow:O}] Type: {errorType}, Context: {providerOrContext}, Key/ID: {keyIdentifier}\nException: {ex.GetType().Name}: {ex.Message}\nStackTrace: {ex.StackTrace}\n\n";
            await _logSemaphore.WaitAsync();
            try
            {
                await File.AppendAllTextAsync(ErrorLogFile, logLine);
            }
            catch (Exception logEx)
            {
                Console.WriteLine($"FATAL: Failed to write to log file {ErrorLogFile}. Error: {logEx.Message}\nOriginal Error: {logLine}");
            }
            finally
            {
                _logSemaphore.Release();
            }
        }

        private static async Task LogMessageAsync(string message)
        {
            var logLine = $"[{DateTime.UtcNow:O}] INFO: {message}\n\n";
            await _logSemaphore.WaitAsync();
            try
            {
                await File.AppendAllTextAsync(ErrorLogFile, logLine);
            }
            catch (Exception logEx)
            {
                Console.WriteLine($"FATAL: Failed to write to log file {ErrorLogFile}. Error: {logEx.Message}\nOriginal Message: {logLine}");
            }
            finally
            {
                _logSemaphore.Release();
            }
        }

        private static async Task ProcessKeyModelsAsync(DBContext dbContext, long apiKeyId, ApiTypeEnum apiType, List<ModelInfo>? models)
        {
            if (models == null || !models.Any()) return;

            try
            {
                foreach (var modelInfo in models)
                {
                    // Check if this model already exists
                    var existingModel = await dbContext.ProviderModels
                        .FirstOrDefaultAsync(pm => pm.ApiType == apiType && pm.ModelId == modelInfo.ModelId, _cancellationTokenSource!.Token);

                    if (existingModel == null)
                    {
                        // Create new model
                        existingModel = new ProviderModel
                        {
                            ApiType = apiType,
                            ModelId = modelInfo.ModelId,
                            DisplayName = modelInfo.DisplayName,
                            Description = modelInfo.Description,
                            Version = modelInfo.Version,
                            InputTokenLimit = modelInfo.InputTokenLimit,
                            OutputTokenLimit = modelInfo.OutputTokenLimit,
                            Temperature = modelInfo.Temperature,
                            TopP = modelInfo.TopP,
                            TopK = modelInfo.TopK,
                            MaxTemperature = modelInfo.MaxTemperature,
                            ModelGroup = modelInfo.ModelGroup,
                            SupportedMethods = modelInfo.SupportedMethods != null ? 
                                System.Text.Json.JsonSerializer.Serialize(modelInfo.SupportedMethods) : null,
                            IsActive = true,
                            IsDeprecated = modelInfo.Description?.Contains("deprecated", StringComparison.OrdinalIgnoreCase) ?? false,
                            FirstSeenUTC = DateTime.UtcNow,
                            LastSeenUTC = DateTime.UtcNow
                        };

                        dbContext.ProviderModels.Add(existingModel);
                        await dbContext.SaveChangesAsync(_cancellationTokenSource.Token);
                    }
                    else
                    {
                        // Update last seen
                        existingModel.LastSeenUTC = DateTime.UtcNow;
                        
                        // Update any changed properties
                        if (existingModel.DisplayName != modelInfo.DisplayName)
                            existingModel.DisplayName = modelInfo.DisplayName;
                        if (existingModel.Description != modelInfo.Description)
                            existingModel.Description = modelInfo.Description;
                        if (existingModel.InputTokenLimit != modelInfo.InputTokenLimit)
                            existingModel.InputTokenLimit = modelInfo.InputTokenLimit;
                        if (existingModel.OutputTokenLimit != modelInfo.OutputTokenLimit)
                            existingModel.OutputTokenLimit = modelInfo.OutputTokenLimit;
                        
                        // Check if it's now deprecated
                        if (!existingModel.IsDeprecated && (modelInfo.Description?.Contains("deprecated", StringComparison.OrdinalIgnoreCase) ?? false))
                        {
                            existingModel.IsDeprecated = true;
                            existingModel.DeprecatedDate = DateTime.UtcNow;
                        }
                    }

                    // Check if API key already has access to this model
                    var existingAccess = await dbContext.ApiKeyModels
                        .FirstOrDefaultAsync(akm => akm.ApiKeyId == apiKeyId && akm.ProviderModelId == existingModel.Id, _cancellationTokenSource.Token);

                    if (existingAccess == null)
                    {
                        // Create new access record
                        var apiKeyModel = new ApiKeyModel
                        {
                            ApiKeyId = apiKeyId,
                            ProviderModelId = existingModel.Id,
                            DiscoveredUTC = DateTime.UtcNow,
                            LastVerifiedUTC = DateTime.UtcNow,
                            HasAccess = true
                        };

                        dbContext.ApiKeyModels.Add(apiKeyModel);
                        _logger?.LogInformation("API Key {ApiKeyId} discovered to have access to model {ModelId}", apiKeyId, modelInfo.ModelId);
                    }
                    else
                    {
                        // Update last verified
                        existingAccess.LastVerifiedUTC = DateTime.UtcNow;
                        existingAccess.HasAccess = true;
                    }
                }

                await dbContext.SaveChangesAsync(_cancellationTokenSource?.Token ?? CancellationToken.None);
                var modelCount = models?.Count ?? 0; // Explicit null check
                _logger?.LogInformation("Processed {Count} models for API Key {ApiKeyId}", modelCount, apiKeyId);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Error processing models for API Key {ApiKeyId}", apiKeyId);
                // Don't throw - model processing failures shouldn't fail the entire verification
            }
        }
    }
}
