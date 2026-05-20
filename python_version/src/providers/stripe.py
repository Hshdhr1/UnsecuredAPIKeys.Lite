import httpx
from typing import Iterable
from .base import BaseApiKeyProvider, ValidationResult


class StripeProvider(BaseApiKeyProvider):
    @property
    def provider_name(self) -> str:
        return "Stripe"

    @property
    def api_type(self) -> str:
        return "Stripe"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"sk_test_[a-zA-Z0-9]{24}"
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        # Simple balance check for validation
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await client.get("https://api.stripe.com/v1/balance", headers=headers)
            return self.interpret_response(response.status_code, response.text)
        except Exception as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)
        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code)
        return ValidationResult.has_http_error(status_code, response_body)
