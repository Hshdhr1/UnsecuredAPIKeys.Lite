using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using UnsecuredAPIKeys.Data;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Data.Models;

namespace UnsecuredAPIKeys.Bots.Verifier.Services
{
    public class BatchLockingService
    {
        private readonly ILogger<BatchLockingService> _logger;
        private readonly string _instanceId;
        private readonly TimeSpan _lockTimeout;

        public BatchLockingService(ILogger<BatchLockingService> logger)
        {
            _logger = logger;
            _instanceId = GenerateInstanceId();
            _lockTimeout = TimeSpan.FromMinutes(30); // Default lock timeout
            
            _logger.LogInformation("BatchLockingService initialized with InstanceId: {InstanceId}", _instanceId);
        }

        private static string GenerateInstanceId()
        {
            // Generate a unique instance ID using hostname and process ID
            var hostname = Environment.MachineName;
            var processId = Environment.ProcessId;
            var timestamp = DateTime.UtcNow.Ticks;
            
            // In Docker/K8s, also include container hostname if different
            var dockerHostname = Environment.GetEnvironmentVariable("HOSTNAME");
            if (!string.IsNullOrEmpty(dockerHostname) && dockerHostname != hostname)
            {
                return $"{dockerHostname}-{hostname}-{processId}-{timestamp}";
            }
            
            return $"{hostname}-{processId}-{timestamp}";
        }

        public async Task<VerificationBatch?> AcquireBatchLockAsync(DBContext dbContext, int batchSize, CancellationToken cancellationToken)
        {
            // Use execution strategy to handle retries with transactions
            var strategy = dbContext.Database.CreateExecutionStrategy();
            
            return await strategy.ExecuteAsync(async () =>
            {
                using var transaction = await dbContext.Database.BeginTransactionAsync(cancellationToken);
                
                try
                {
                    // First, clean up any expired locks
                    await CleanupExpiredLocksAsync(dbContext, cancellationToken);

                    // Find the next available key range not already locked
                    
                    // Get active batches to determine what ranges are locked
                    // Only consider batches that are truly active (not completed, failed, or expired)
                    var activeBatches = await dbContext.VerificationBatches
                        .Where(b => (b.Status == VerificationBatchStatus.Locked || 
                                    b.Status == VerificationBatchStatus.Processing) &&
                                   b.LockExpiresAtUTC > DateTime.UtcNow)
                        .ToListAsync(cancellationToken);
                    
                    _logger.LogDebug("Found {Count} active batches blocking key ranges", activeBatches.Count);

                    // Build a query for keys that need verification
                    // Exclude keys known to be removed/invalidated by owner
                    var keysQuery = dbContext.APIKeys
                        .Where(s => s.Status != ApiStatusEnum.Removed &&
                                   s.Status != ApiStatusEnum.FlaggedForRemoval);

                    // Exclude keys already in active batches
                    foreach (var activeBatch in activeBatches)
                    {
                        keysQuery = keysQuery.Where(k => k.Id < activeBatch.StartKeyId || k.Id > activeBatch.EndKeyId);
                    }

                    // Get the next batch of keys
                    var keysToProcess = await keysQuery
                        .OrderBy(k => k.LastCheckedUTC ?? DateTime.MinValue)
                        .ThenBy(k => k.Status == ApiStatusEnum.ValidNoCredits ? 2 : k.Status == ApiStatusEnum.Valid ? 1 : 0)
                        .Select(k => k.Id)
                        .Take(batchSize)
                        .ToListAsync(cancellationToken);

                    if (!keysToProcess.Any())
                    {
                        // Let's log more details about why no keys were found
                        var totalEligibleBeforeExclusion = await dbContext.APIKeys.CountAsync(cancellationToken);
                        
                        _logger.LogWarning("No unlocked keys available for processing. Total eligible before batch exclusion: {Count}. Active batches blocking ranges: {ActiveBatchCount}", 
                            totalEligibleBeforeExclusion, activeBatches.Count);
                        
                        if (activeBatches.Any())
                        {
                            foreach (var activeBatch in activeBatches.Take(5)) // Log first 5 blocking batches
                            {
                                _logger.LogDebug("Active batch {BatchId} blocking range {StartKey}-{EndKey} (expires {ExpiresAt})", 
                                    activeBatch.Id, activeBatch.StartKeyId, activeBatch.EndKeyId, activeBatch.LockExpiresAtUTC);
                            }
                        }
                        
                        await transaction.RollbackAsync(cancellationToken);
                        return null;
                    }

                    // Create a new batch lock
                    var batch = new VerificationBatch
                    {
                        InstanceId = _instanceId,
                        LockedAtUTC = DateTime.UtcNow,
                        LockExpiresAtUTC = DateTime.UtcNow.Add(_lockTimeout),
                        Status = VerificationBatchStatus.Locked,
                        StartKeyId = keysToProcess.Min(),
                        EndKeyId = keysToProcess.Max(),
                        KeyCount = keysToProcess.Count
                    };

                    dbContext.VerificationBatches.Add(batch);
                    await dbContext.SaveChangesAsync(cancellationToken);
                    await transaction.CommitAsync(cancellationToken);

                    _logger.LogInformation("Acquired lock for batch {BatchId} with {KeyCount} keys (ID range: {StartKeyId}-{EndKeyId})", 
                        batch.Id, batch.KeyCount, batch.StartKeyId, batch.EndKeyId);

                    return batch;
                }
                catch (Exception ex)
                {
                    await transaction.RollbackAsync(cancellationToken);
                    _logger.LogError(ex, "Failed to acquire batch lock");
                    throw;
                }
            });
        }

        public async Task UpdateBatchStatusAsync(DBContext dbContext, long batchId, VerificationBatchStatus status, CancellationToken cancellationToken)
        {
            var batch = await dbContext.VerificationBatches.FindAsync(new object[] { batchId }, cancellationToken);
            if (batch == null)
            {
                _logger.LogWarning("Batch {BatchId} not found for status update", batchId);
                return;
            }

            if (batch.InstanceId != _instanceId)
            {
                _logger.LogWarning("Attempted to update batch {BatchId} owned by different instance {OwnerInstance}", batchId, batch.InstanceId);
                return;
            }

            batch.Status = status;
            
            if (status == VerificationBatchStatus.Processing && !batch.ProcessingStartedAtUTC.HasValue)
            {
                batch.ProcessingStartedAtUTC = DateTime.UtcNow;
            }
            else if ((status == VerificationBatchStatus.Completed || status == VerificationBatchStatus.Failed) && !batch.ProcessingCompletedAtUTC.HasValue)
            {
                batch.ProcessingCompletedAtUTC = DateTime.UtcNow;
            }

            await dbContext.SaveChangesAsync(cancellationToken);
            _logger.LogInformation("Updated batch {BatchId} status to {Status}", batchId, status);
        }

        public async Task UpdateBatchResultsAsync(DBContext dbContext, long batchId, int validKeys, int invalidKeys, int skippedKeys, int errorKeys, CancellationToken cancellationToken)
        {
            var batch = await dbContext.VerificationBatches.FindAsync(new object[] { batchId }, cancellationToken);
            if (batch == null || batch.InstanceId != _instanceId)
            {
                _logger.LogWarning("Cannot update results for batch {BatchId} - not found or not owned by this instance", batchId);
                return;
            }

            batch.ValidKeys = validKeys;
            batch.InvalidKeys = invalidKeys;
            batch.SkippedKeys = skippedKeys;
            batch.ErrorKeys = errorKeys;

            await dbContext.SaveChangesAsync(cancellationToken);
            _logger.LogInformation("Updated batch {BatchId} results: Valid={Valid}, Invalid={Invalid}, Skipped={Skipped}, Error={Error}", 
                batchId, validKeys, invalidKeys, skippedKeys, errorKeys);
        }

        public async Task ReleaseBatchLockAsync(DBContext dbContext, long batchId, bool success, string? errorMessage, CancellationToken cancellationToken)
        {
            var batch = await dbContext.VerificationBatches.FindAsync(new object[] { batchId }, cancellationToken);
            if (batch == null || batch.InstanceId != _instanceId)
            {
                _logger.LogWarning("Cannot release batch {BatchId} - not found or not owned by this instance", batchId);
                return;
            }

            batch.Status = success ? VerificationBatchStatus.Completed : VerificationBatchStatus.Failed;
            batch.ProcessingCompletedAtUTC = DateTime.UtcNow;
            
            if (!success && !string.IsNullOrEmpty(errorMessage))
            {
                batch.ErrorMessage = errorMessage;
            }

            await dbContext.SaveChangesAsync(cancellationToken);
            _logger.LogInformation("Released batch {BatchId} lock with status {Status}", batchId, batch.Status);
        }

        private async Task CleanupExpiredLocksAsync(DBContext dbContext, CancellationToken cancellationToken)
        {
            var expiredBatches = await dbContext.VerificationBatches
                .Where(b => (b.Status == VerificationBatchStatus.Locked || b.Status == VerificationBatchStatus.Processing) &&
                           b.LockExpiresAtUTC < DateTime.UtcNow)
                .ToListAsync(cancellationToken);

            if (expiredBatches.Any())
            {
                foreach (var batch in expiredBatches)
                {
                    // Mark as failed instead of expired, and set completion time
                    batch.Status = VerificationBatchStatus.Failed;
                    batch.ProcessingCompletedAtUTC = DateTime.UtcNow;
                    batch.ErrorMessage = $"Lock expired at {batch.LockExpiresAtUTC:O} - Instance {batch.InstanceId} likely crashed";
                    
                    _logger.LogWarning("Marking batch {BatchId} as failed due to expired lock (was locked by {InstanceId}, expired {MinutesAgo:F1} minutes ago)", 
                        batch.Id, batch.InstanceId, (DateTime.UtcNow - batch.LockExpiresAtUTC).TotalMinutes);
                }

                await dbContext.SaveChangesAsync(cancellationToken);
                _logger.LogInformation("Cleaned up {Count} expired batch locks", expiredBatches.Count);
            }
        }

        public async Task<List<long>> GetKeysForBatchAsync(DBContext dbContext, long batchId, CancellationToken cancellationToken)
        {
            var batch = await dbContext.VerificationBatches.FindAsync(new object[] { batchId }, cancellationToken);
            if (batch == null)
            {
                return new List<long>();
            }

            return await dbContext.APIKeys
                .Where(k => k.Id >= batch.StartKeyId && k.Id <= batch.EndKeyId)
                .Select(k => k.Id)
                .ToListAsync(cancellationToken);
        }

        /// <summary>
        /// Force cleanup of ALL stuck batches (both expired and long-running)
        /// This is useful for manual intervention when instances crash
        /// </summary>
        public async Task<int> ForceCleanupAllStuckBatchesAsync(DBContext dbContext, TimeSpan? maxProcessingTime = null, CancellationToken cancellationToken = default)
        {
            // Default to 2 hours for max processing time
            var maxTime = maxProcessingTime ?? TimeSpan.FromHours(2);
            var cutoffTime = DateTime.UtcNow.Subtract(maxTime);

            var stuckBatches = await dbContext.VerificationBatches
                .Where(b => (b.Status == VerificationBatchStatus.Locked || b.Status == VerificationBatchStatus.Processing) &&
                           (b.LockExpiresAtUTC < DateTime.UtcNow || b.LockedAtUTC < cutoffTime))
                .ToListAsync(cancellationToken);

            if (stuckBatches.Any())
            {
                _logger.LogWarning("Found {Count} stuck batches to clean up", stuckBatches.Count);
                
                foreach (var batch in stuckBatches)
                {
                    var runningTime = DateTime.UtcNow - batch.LockedAtUTC;
                    
                    batch.Status = VerificationBatchStatus.Failed;
                    batch.ProcessingCompletedAtUTC = DateTime.UtcNow;
                    batch.ErrorMessage = $"Force cleaned - Instance {batch.InstanceId} was running for {runningTime.TotalMinutes:F1} minutes";
                    
                    _logger.LogWarning("Force cleaned batch {BatchId} (Instance: {InstanceId}, Running time: {RunningTime:F1} minutes)", 
                        batch.Id, batch.InstanceId, runningTime.TotalMinutes);
                }

                await dbContext.SaveChangesAsync(cancellationToken);
                _logger.LogInformation("Force cleaned {Count} stuck batches", stuckBatches.Count);
            }

            return stuckBatches.Count;
        }
    }
}
