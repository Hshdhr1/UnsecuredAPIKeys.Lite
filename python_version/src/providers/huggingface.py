import httpx
from typing import Iterable
from .base import BaseApiKeyProvider, ValidationResult


class HuggingFaceProvider(BaseApiKeyProvider):
    @property
    def provider_name(self) -> str:
        return "HuggingFace"

    @property
    def api_type(self) -> str:
        return "HuggingFace"

    @property
    def regex_patterns(self) -> Iterable[str]:
        return [
            r"hf_[a-zA-Z]{30,}"
        ]

    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = await client.get("https://huggingface.co/api/whoami-v2", headers=headers)
            return self.interpret_response(response.status_code, response.text)
        except Exception as e:
            return ValidationResult.has_network_error(str(e))

    def interpret_response(self, status_code: int, response_body: str) -> ValidationResult:
        if 200 <= status_code < 300:
            return ValidationResult.success(status_code)
        if status_code == 401:
            return ValidationResult.is_unauthorized(status_code)
        return ValidationResult.has_http_error(status_code, response_body)
