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
    /// Provider implementation for handling Moonshot AI API keys.
    /// Kimi API is fully compatible with OpenAI’s API format.
    /// </summary>
    [ApiProvider]
    public class MoonshotAIProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "Moonshot AI";
        public override ApiTypeEnum ApiType => ApiTypeEnum.MoonshotAI;

        // Regex patterns for Moonshot AI keys
        // Example: sk-si6WPU0bbJIa5yALpRHfn96iZjWD1H0halJPmGZbS8eP4jqp
        public override IEnumerable<string> RegexPatterns =>
        [
            @"sk-[A-Za-z0-9]{48}"
        ];

        public MoonshotAIProvider() : base()
        {
        }

        public MoonshotAIProvider(ILogger<MoonshotAIProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            // Moonshot AI is OpenAI compatible
            using var modelRequest = new HttpRequestMessage(HttpMethod.Get, "https://api.moonshot.ai/v1/models");
            modelRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);

            var modelResponse = await httpClient.SendAsync(modelRequest);
            string responseBody = await modelResponse.Content.ReadAsStringAsync();

            _logger?.LogDebug("Moonshot AI models API response: Status={StatusCode}, Body={Body}",
                modelResponse.StatusCode, TruncateResponse(responseBody));

            if (IsSuccessStatusCode(modelResponse.StatusCode))
            {
                var models = ParseMoonshotModels(responseBody);
                return ValidationResult.Success(modelResponse.StatusCode, models);
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
                   apiKey.StartsWith("sk-") &&
                   apiKey.Length >= 23;
        }

        private List<ModelInfo>? ParseMoonshotModels(string jsonResponse)
        {
            try
            {
                using var doc = JsonDocument.Parse(jsonResponse);
                if (!doc.RootElement.TryGetProperty("data", out var dataArray))
                {
                    return null;
                }

                var models = new List<ModelInfo>();
                foreach (var modelElement in dataArray.EnumerateArray())
                {
                    var modelId = modelElement.GetProperty("id").GetString() ?? "";
                    var model = new ModelInfo
                    {
                        ModelId = modelId,
                        DisplayName = modelId,
                        ModelGroup = modelId.Contains("kimi") ? "Kimi" : "Other"
                    };

                    models.Add(model);
                }

                return models;
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Error parsing Moonshot AI models response");
                return null;
            }
        }
    }
}
