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
    /// Provider implementation for handling OpenAI API keys.
    /// </summary>
    [ApiProvider]
    public class OpenAIProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "OpenAI";
        public override ApiTypeEnum ApiType => ApiTypeEnum.OpenAI;

        // Enhanced regex patterns for OpenAI keys
        public override IEnumerable<string> RegexPatterns =>
        [
            @"sk-[a-zA-Z0-9]{30,}"
        ];

        public OpenAIProvider() : base()
        {
        }

        public OpenAIProvider(ILogger<OpenAIProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            // First, try a lightweight model listing endpoint
            using var modelRequest = new HttpRequestMessage(HttpMethod.Get, "https://api.openai.com/v1/models");
            modelRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            
            var modelResponse = await httpClient.SendAsync(modelRequest);
            string responseBody = await modelResponse.Content.ReadAsStringAsync();

            _logger?.LogDebug("OpenAI models API response: Status={StatusCode}, Body={Body}",
                modelResponse.StatusCode, TruncateResponse(responseBody));

            if (IsSuccessStatusCode(modelResponse.StatusCode))
            {
                // Parse the models from the response
                var models = ParseOpenAIModels(responseBody);
                return ValidationResult.Success(modelResponse.StatusCode, models);
            }
            else if (modelResponse.StatusCode == HttpStatusCode.Unauthorized)
            {
                return ValidationResult.IsUnauthorized(modelResponse.StatusCode);
            }
            else if ((int)modelResponse.StatusCode == 429)
            {
                // Rate limited means the key is valid
                return ValidationResult.Success(modelResponse.StatusCode);
            }
            else if (modelResponse.StatusCode == HttpStatusCode.PaymentRequired)
            {
                // Payment required means valid key but no credits
                return ValidationResult.Success(modelResponse.StatusCode);
            }
            else
            {
                // Check response body for quota/billing issues
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
                   apiKey.Length >= 23; // sk- + at least 20 chars
        }

        private List<ModelInfo>? ParseOpenAIModels(string jsonResponse)
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
                    var model = new ModelInfo
                    {
                        ModelId = modelElement.GetProperty("id").GetString() ?? "",
                        DisplayName = modelElement.GetProperty("id").GetString() ?? "", // OpenAI uses id as display name
                        Description = modelElement.TryGetProperty("description", out var desc) ? desc.GetString() : null
                    };

                    // Extract model group from the ID
                    if (!string.IsNullOrEmpty(model.ModelId))
                    {
                        // Group models by family (e.g., "gpt-4", "gpt-3.5", "text-embedding")
                        if (model.ModelId.StartsWith("gpt-4"))
                        {
                            model.ModelGroup = "GPT-4";
                            
                            // Check for specific capabilities
                            if (model.ModelId.Contains("turbo"))
                            {
                                model.Description = "GPT-4 Turbo model with enhanced capabilities";
                            }
                            else if (model.ModelId.Contains("vision"))
                            {
                                model.Description = "GPT-4 model with vision capabilities";
                            }
                        }
                        else if (model.ModelId.StartsWith("gpt-3.5"))
                        {
                            model.ModelGroup = "GPT-3.5";
                        }
                        else if (model.ModelId.StartsWith("o1"))
                        {
                            model.ModelGroup = "O1";
                            model.Description = "OpenAI's reasoning model";
                        }
                        else if (model.ModelId.StartsWith("text-embedding"))
                        {
                            model.ModelGroup = "Embeddings";
                            model.Description = "Text embedding model";
                        }
                        else if (model.ModelId.StartsWith("dall-e"))
                        {
                            model.ModelGroup = "DALL-E";
                            model.Description = "Image generation model";
                        }
                        else if (model.ModelId.StartsWith("whisper"))
                        {
                            model.ModelGroup = "Whisper";
                            model.Description = "Speech recognition model";
                        }
                        else if (model.ModelId.StartsWith("tts"))
                        {
                            model.ModelGroup = "TTS";
                            model.Description = "Text-to-speech model";
                        }
                        else
                        {
                            model.ModelGroup = "Other";
                        }
                    }

                    models.Add(model);
                }

                return models;
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Error parsing OpenAI models response");
                return null;
            }
        }
    }
}
