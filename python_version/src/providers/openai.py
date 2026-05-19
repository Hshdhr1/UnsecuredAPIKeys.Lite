import httpx
from typing import Iterable, List, Optional
from .base import BaseApiKeyProvider, ValidationResult, ModelInfo


class OpenAIProvider(BaseApiKeyProvider):
    API_ENDPOINT = "https://api.openai.com/v1/models"

    @property
    def provider_name(self) -> str:
        return "OpenAI"

    @property
    def api_type(self) -> str:
        return "OpenAI"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"sk-[A-Za-z0-9\-]{20,}",
            r"sk-proj-[A-Za-z0-9\-]{20,}",
            r"sk-svcacct-[A-Za-z0-9\-]{20,}",
            r"sk-[A-Za-z0-9]{48}",
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        try:
            response = await client.get(self.API_ENDPOINT, headers=headers)
            response_text = response.text
            self.logger.debug(f"OpenAI models API response: {response.status_code}")
            return self.interpret_response(response.status_code, response_text)
        except httpx.RequestError as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)

        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code, response_body)

        if status_code in (402, 429):
            return ValidationResult.success(status_code, "Valid but restricted/quota")

        body_lower = response_body.lower()
        if self.contains_any(body_lower, self.QUOTA_INDICATORS):
            return ValidationResult.success(status_code)

        return ValidationResult.has_http_error(status_code, f"Error {status_code}: {self.truncate_response(response_body)}")

    def is_valid_key_format(self, api_key: str) -> bool:
        return api_key.startswith("sk-") and len(api_key) >= 23
