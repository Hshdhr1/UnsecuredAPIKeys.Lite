import httpx
from typing import Iterable, Optional, Set
from .base import BaseApiKeyProvider, ValidationResult, ValidationAttemptStatus


class AnthropicProvider(BaseApiKeyProvider):
    API_ENDPOINT = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"
    DEFAULT_MODEL = "claude-3-5-sonnet-20240620"

    @property
    def provider_name(self) -> str:
        return "Anthropic"

    @property
    def api_type(self) -> str:
        return "AnthropicClaude"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"sk-ant-api\d{0,2}-[a-zA-Z0-9\-_]{40,120}",
            r"sk-ant-[a-zA-Z0-9\-_]{40,95}",
            r"sk-ant-v\d+-[a-zA-Z0-9\-_]{40,95}",
            r"sk-ant-[a-zA-Z0-9]+-[a-zA-Z0-9\-_]{20,120}",
            r"sk-ant-[a-zA-Z0-9]{40,64}",
            r"\bsk-ant-[a-zA-Z0-9\-_]{20,120}\b"
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }

        payload = {
            "model": self.DEFAULT_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "1"}],
            "temperature": 0,
        }

        try:
            response = await client.post(self.API_ENDPOINT, headers=headers, json=payload)
            response_text = response.text
            self.logger.debug(f"Anthropic API response: {response.status_code} - {self.truncate_response(response_text)}")
            return self.interpret_response(response.status_code, response_text)
        except httpx.RequestError as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)

        body_lower = response_body.lower()

        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code, response_body)

        if status_code == 403:
            if self.contains_any(body_lower, self.PERMISSION_INDICATORS):
                return ValidationResult.success(status_code, "Valid but restricted")
            return ValidationResult.has_http_error(status_code, f"Forbidden: {self.truncate_response(response_body)}")

        if status_code == 400:
            if self.contains_any(body_lower, self.QUOTA_INDICATORS):
                return ValidationResult.success(status_code, "Valid but no quota")
            return ValidationResult.has_http_error(status_code, f"Bad request: {self.truncate_response(response_body)}")

        if status_code in (402, 429):
            return ValidationResult.success(status_code, "Valid but rate limited/payment required")

        if status_code >= 500:
            return ValidationResult.has_network_error(f"Service error: {status_code}")

        if self.contains_any(body_lower, self.QUOTA_INDICATORS):
            return ValidationResult.success(status_code)

        return ValidationResult.has_http_error(status_code, f"Error {status_code}: {self.truncate_response(response_body)}")

    def is_valid_key_format(self, api_key: str) -> bool:
        if not api_key or len(api_key) < 20:
            return False
        if not api_key.startswith("sk-ant-"):
            return False
        return all(c.isalnum() or c in "-_" for c in api_key)
