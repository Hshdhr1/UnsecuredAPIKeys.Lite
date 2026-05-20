using System.Net;
using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Providers._Base;
using UnsecuredAPIKeys.Providers.Common;

namespace UnsecuredAPIKeys.Providers.AI_Providers
{
    /// <summary>
    /// Provider implementation for handling xAI (Grok) API keys.
    /// </summary>
    [ApiProvider]
    public class XAIProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "xAI";
        public override ApiTypeEnum ApiType => ApiTypeEnum.XAI;

        // Regex patterns for xAI keys
        public override IEnumerable<string> RegexPatterns =>
        [
            @"xai-[a-zA-Z0-9]{40,128}"
        ];

        public XAIProvider() : base()
        {
        }

        public XAIProvider(ILogger<XAIProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            // xAI is OpenAI compatible
            using var modelRequest = new HttpRequestMessage(HttpMethod.Get, "https://api.x.ai/v1/models");
            modelRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);

            var modelResponse = await httpClient.SendAsync(modelRequest);
            string responseBody = await modelResponse.Content.ReadAsStringAsync();

            _logger?.LogDebug("xAI models API response: Status={StatusCode}, Body={Body}",
                modelResponse.StatusCode, TruncateResponse(responseBody));

            if (IsSuccessStatusCode(modelResponse.StatusCode))
            {
                return ValidationResult.Success(modelResponse.StatusCode);
            }
            else if (modelResponse.StatusCode == HttpStatusCode.Unauthorized)
            {
                return ValidationResult.IsUnauthorized(modelResponse.StatusCode);
            }
            else if ((int)modelResponse.StatusCode == 429)
            {
                return ValidationResult.Success(modelResponse.StatusCode);
            }
            else
            {
                if (ContainsAny(responseBody, QuotaIndicators))
                {
                    return ValidationResult.Success(modelResponse.StatusCode);
                }

                return ValidationResult.HasHttpError(modelResponse.StatusCode,
                    $"API request failed with status {modelResponse.StatusCode}. Response: {TruncateResponse(responseBody)}");
            }
        }

        protected override bool IsValidKeyFormat(string apiKey)
        {
            return !string.IsNullOrWhiteSpace(apiKey) &&
                   apiKey.StartsWith("xai-") &&
                   apiKey.Length >= 20;
        }
    }
}
