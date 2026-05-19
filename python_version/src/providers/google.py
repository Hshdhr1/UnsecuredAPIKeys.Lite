import httpx
from typing import Iterable, List, Optional
from .base import BaseApiKeyProvider, ValidationResult


class GoogleProvider(BaseApiKeyProvider):
    API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def provider_name(self) -> str:
        return "Google"

    @property
    def api_type(self) -> str:
        return "GoogleAI"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"AIza[0-9A-Za-z\-_]{35,40}"
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        # Google uses query param or x-goog-api-key
        params = {"key": api_key}

        try:
            response = await client.get(self.API_ENDPOINT, params=params)
            response_text = response.text
            return self.interpret_response(response.status_code, response_text)
        except httpx.RequestError as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)

        if status_code in (401, 403):
            return ValidationResult.is_unauthorized(status_code, response_body)

        if status_code == 429:
            return ValidationResult.success(status_code, "Valid but rate limited")

        body_lower = response_body.lower()
        if self.contains_any(body_lower, self.QUOTA_INDICATORS):
            return ValidationResult.success(status_code)

        return ValidationResult.has_http_error(status_code, f"Error {status_code}: {self.truncate_response(response_body)}")

    def is_valid_key_format(self, api_key: str) -> bool:
        return api_key.startswith("AIza") and len(api_key) >= 39
