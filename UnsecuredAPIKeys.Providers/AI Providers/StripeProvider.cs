using System.Net;
using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;
using UnsecuredAPIKeys.Data.Common;
using UnsecuredAPIKeys.Providers._Base;
using UnsecuredAPIKeys.Providers.Common;

namespace UnsecuredAPIKeys.Providers.AI_Providers
{
    /// <summary>
    /// Provider implementation for handling Stripe API keys.
    /// </summary>
    [ApiProvider]
    public class StripeProvider : BaseApiKeyProvider
    {
        public override string ProviderName => "Stripe";
        public override ApiTypeEnum ApiType => ApiTypeEnum.Stripe;

        public override IEnumerable<string> RegexPatterns =>
        [
            @"sk_test_[a-zA-Z0-9]{24}"
        ];

        public StripeProvider() : base()
        {
        }

        public StripeProvider(ILogger<StripeProvider>? logger) : base(logger)
        {
        }

        protected override async Task<ValidationResult> ValidateKeyWithHttpClientAsync(string apiKey, HttpClient httpClient)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, "https://api.stripe.com/v1/balance");
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);

            var response = await httpClient.SendAsync(request);
            string responseBody = await response.Content.ReadAsStringAsync();

            if (IsSuccessStatusCode(response.StatusCode))
            {
                return ValidationResult.Success(response.StatusCode);
            }
            else if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                return ValidationResult.IsUnauthorized(response.StatusCode);
            }
            else
            {
                return ValidationResult.HasHttpError(response.StatusCode, responseBody);
            }
        }

        protected override bool IsValidKeyFormat(string apiKey)
        {
            return !string.IsNullOrWhiteSpace(apiKey) && apiKey.StartsWith("sk_test_") && base.IsValidKeyFormat(apiKey);
        }
    }
}
