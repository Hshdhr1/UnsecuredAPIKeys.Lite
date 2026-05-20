import httpx
from typing import Iterable, List, Optional
from .base import BaseApiKeyProvider, ValidationResult


class OpenRouterProvider(BaseApiKeyProvider):
    API_ENDPOINT = "https://openrouter.ai/api/v1/credits"

    @property
    def provider_name(self) -> str:
        return "OpenRouter"

    @property
    def api_type(self) -> str:
        return "OpenRouter"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"sk-or-v1-[a-f0-9]{64}"
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Referer": "https://unsecuredapikeys.com"
        }

        try:
            response = await client.get(self.API_ENDPOINT, headers=headers)
            response_text = response.text
            return self.interpret_response(response.status_code, response_text)
        except httpx.RequestError as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)

        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code, response_body)

        if status_code == 429:
            return ValidationResult.success(status_code, "Valid but rate limited")

        body_lower = response_body.lower()
        if self.contains_any(body_lower, self.QUOTA_INDICATORS) or self.contains_any(body_lower, self.PERMISSION_INDICATORS):
            return ValidationResult.success(status_code)

        return ValidationResult.has_http_error(status_code, f"Error {status_code}: {self.truncate_response(response_body)}")

    def is_valid_key_format(self, api_key: str) -> bool:
        return api_key.startswith("sk-or-") and len(api_key) >= 30
