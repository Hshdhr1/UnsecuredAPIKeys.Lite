using System.Net;
using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Providers._Base;
using UnsecuredAPIKeys.Providers.Common;

namespace UnsecuredAPIKeys.Providers.AI_Providers
{
    /// <summary>
    /// Provider implementation for handling Zhipu AI (GLM) API keys.
    /// </summary>
    [ApiProvider]
    public class ZhipuAIProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "Zhipu AI";
        public override ApiTypeEnum ApiType => ApiTypeEnum.ZhipuAI;

        // Regex patterns for Zhipu AI keys
        // Example format: api_key.id
        public override IEnumerable<string> RegexPatterns =>
        [
            @"[a-zA-Z0-9]{32}\.[a-zA-Z0-9]{16}"
        ];

        public ZhipuAIProvider() : base()
        {
        }

        public ZhipuAIProvider(ILogger<ZhipuAIProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            // Zhipu AI (BigModel) API
            using var modelRequest = new HttpRequestMessage(HttpMethod.Get, "https://open.bigmodel.cn/api/paas/v4/models");
            modelRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);

            var modelResponse = await httpClient.SendAsync(modelRequest);
            string responseBody = await modelResponse.Content.ReadAsStringAsync();

            _logger?.LogDebug("Zhipu AI models API response: Status={StatusCode}, Body={Body}",
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
            if (string.IsNullOrWhiteSpace(apiKey)) return false;
            var parts = apiKey.Split('.');
            return parts.Length == 2 && parts[0].Length == 32 && parts[1].Length == 16;
        }
    }
}
