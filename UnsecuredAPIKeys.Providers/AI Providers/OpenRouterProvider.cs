using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Providers._Base;
using UnsecuredAPIKeys.Providers.Common;

namespace UnsecuredAPIKeys.Providers.AI_Providers
{
    /// <summary>
    /// Provider implementation for handling OpenRouter API keys.
    /// </summary>
    [ApiProvider]
    public class OpenRouterProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "OpenRouter";
        public override ApiTypeEnum ApiType => ApiTypeEnum.OpenRouter;

        public override IEnumerable<string> RegexPatterns =>
        [
            @"sk-or-v1-[a-zA-Z0-9]{40,128}",
            @"sk-or-[a-zA-Z0-9]{24,48}"
        ];

        private class OpenRouterCreditsResponse
        {
            public OpenRouterCreditsData Data { get; set; } = null!;
        }

        private class OpenRouterCreditsData
        {
            public decimal Total_Credits { get; set; }
            public decimal Total_Usage { get; set; }
        }

        public OpenRouterProvider() : base()
        {
        }

        public OpenRouterProvider(ILogger<OpenRouterProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            // Use ONLY the lightweight credits endpoint for validation
            using var request = new HttpRequestMessage(HttpMethod.Get, "https://openrouter.ai/api/v1/credits");
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            request.Headers.Referrer = new Uri("https://unsecuredapikeys.com");
            
            var response = await httpClient.SendAsync(request);
            var responseBody = await response.Content.ReadAsStringAsync();

            _logger?.LogDebug("OpenRouter credits API response: Status={StatusCode}, Body={Body}",
                response.StatusCode, TruncateResponse(responseBody));

            if (IsSuccessStatusCode(response.StatusCode))
            {
                try
                {
                    var creditsData = JsonSerializer.Deserialize<OpenRouterCreditsResponse>(responseBody);
                    // If we can parse credits data, the key is valid regardless of credit amount
                    // Credit amount doesn't determine key validity, just usability
                    return ValidationResult.Success(response.StatusCode);
                }
                catch (JsonException ex)
                {
                    _logger?.LogWarning(ex, "Failed to parse OpenRouter credits response, but HTTP status was success");
                    // If response is successful but unparseable, still consider key valid
                    return ValidationResult.Success(response.StatusCode);
                }
            }
            else if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                return ValidationResult.IsUnauthorized(response.StatusCode);
            }
            else if ((int)response.StatusCode == 429)
            {
                // Rate limited means the key is valid
                return ValidationResult.Success(response.StatusCode);
            }
            else
            {
                // Check for quota/billing/permission issues
                if (ContainsAny(responseBody, QuotaIndicators) || 
                    ContainsAny(responseBody, PermissionIndicators) ||
                    responseBody.Contains("rate_limit_exceeded", StringComparison.OrdinalIgnoreCase) ||
                    responseBody.Contains("moderation", StringComparison.OrdinalIgnoreCase))
                {
                    return ValidationResult.Success(response.StatusCode);
                }
                
                return ValidationResult.HasHttpError(response.StatusCode, 
                    $"API request failed with status {response.StatusCode}. Response: {TruncateResponse(responseBody)}");
            }
        }

        protected override bool IsValidKeyFormat(string apiKey)
        {
            return !string.IsNullOrWhiteSpace(apiKey) && 
                   apiKey.StartsWith("sk-or-") && 
                   apiKey.Length >= 30; // sk-or- + at least 24 chars
        }
    }
}
