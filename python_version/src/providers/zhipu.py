import httpx
from typing import Iterable
from .base import BaseApiKeyProvider, ValidationResult


class ZhipuAIProvider(BaseApiKeyProvider):
    API_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/models"

    @property
    def provider_name(self) -> str:
        return "Zhipu AI"

    @property
    def api_type(self) -> str:
        return "ZhipuAI"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"[a-zA-Z0-9]{32}\.[a-zA-Z0-9]{16}"
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
            return self.interpret_response(response.status_code, response_text)
        except httpx.RequestError as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)

        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code, response_body)

        if status_code in (402, 429):
            return ValidationResult.success(status_code, "Valid but quota/limit")

        body_lower = response_body.lower()
        if self.contains_any(body_lower, self.QUOTA_INDICATORS):
            return ValidationResult.success(status_code)

        return ValidationResult.has_http_error(status_code, f"Error {status_code}: {self.truncate_response(response_body)}")

    def is_valid_key_format(self, api_key: str) -> bool:
        parts = api_key.split('.')
        return len(parts) == 2 and len(parts[0]) == 32 and len(parts[1]) == 16
