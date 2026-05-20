from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional, Set
import httpx
import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum


class ValidationAttemptStatus(str, Enum):
    VALID = "Valid"
    UNAUTHORIZED = "Unauthorized"
    HTTP_ERROR = "HttpError"
    NETWORK_ERROR = "NetworkError"
    PROVIDER_SPECIFIC_ERROR = "ProviderSpecificError"


@dataclass
class ModelInfo:
    model_id: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    input_token_limit: Optional[int] = None
    output_token_limit: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_temperature: Optional[float] = None
    model_group: Optional[str] = None
    supported_methods: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    status: ValidationAttemptStatus
    detail: str = ""
    http_status_code: Optional[int] = None
    available_models: List[ModelInfo] = field(default_factory=list)

    @classmethod
    def success(cls, status_code: int = 200, detail: str = "Success") -> "ValidationResult":
        return cls(status=ValidationAttemptStatus.VALID, http_status_code=status_code, detail=detail)

    @classmethod
    def is_unauthorized(cls, status_code: int = 401, detail: str = "Unauthorized") -> "ValidationResult":
        return cls(status=ValidationAttemptStatus.UNAUTHORIZED, http_status_code=status_code, detail=detail)

    @classmethod
    def has_http_error(cls, status_code: int, detail: str) -> "ValidationResult":
        return cls(status=ValidationAttemptStatus.HTTP_ERROR, http_status_code=status_code, detail=detail)

    @classmethod
    def has_network_error(cls, detail: str) -> "ValidationResult":
        return cls(status=ValidationAttemptStatus.NETWORK_ERROR, detail=detail)

    @classmethod
    def has_provider_specific_error(cls, detail: str) -> "ValidationResult":
        return cls(status=ValidationAttemptStatus.PROVIDER_SPECIFIC_ERROR, detail=detail)


class BaseApiKeyProvider(ABC):
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def api_type(self) -> str:
        pass

    @property
    @abstractmethod
    def regex_patterns(self) -> Iterable[str]:
        pass

    async def validate_key_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        if not api_key or not api_key.strip():
            return ValidationResult.has_provider_specific_error("API key is empty.")

        api_key = self.clean_api_key(api_key)

        if not self.is_valid_key_format(api_key):
            return ValidationResult.has_provider_specific_error("API key format is invalid.")

        last_exception = None
        for retry in range(self.get_max_retries()):
            if retry > 0:
                delay = 2 ** (retry - 1)
                self.logger.debug(f"Retrying {self.provider_name} validation after {delay}s")
                await asyncio.sleep(delay)

            try:
                result = await self.validate_key_with_client_async(api_key, client)
                if result.status != ValidationAttemptStatus.NETWORK_ERROR:
                    return result
                last_exception = Exception(result.detail)
            except httpx.HTTPStatusError as e:
                last_exception = e
                self.logger.warning(f"HTTP error on attempt {retry+1} for {self.provider_name}: {e}")
            except (httpx.RequestError, asyncio.TimeoutError) as e:
                last_exception = e
                self.logger.warning(f"Request/Timeout error on attempt {retry+1} for {self.provider_name}: {e}")
                if retry == self.get_max_retries() - 1:
                    return ValidationResult.has_network_error(str(e))
            except Exception as e:
                self.logger.error(f"Unexpected error during {self.provider_name} validation: {e}")
                return ValidationResult.has_provider_specific_error(str(e))

        return ValidationResult.has_network_error(
            f"Failed after {self.get_max_retries()} retries. Last error: {last_exception}"
        )

    @abstractmethod
    async def validate_key_with_client_async(
        self, api_key: str, client: httpx.AsyncClient
    ) -> ValidationResult:
        pass

    def clean_api_key(self, api_key: str) -> str:
        api_key = api_key.strip()
        if api_key.lower().startswith("bearer "):
            api_key = api_key[7:].strip()
        elif api_key.lower().startswith("x-api-key:"):
            api_key = api_key[10:].strip()
        return api_key

    def is_valid_key_format(self, api_key: str) -> bool:
        if len(api_key) < 10:
            return False

        # Heuristic to detect obviously fake keys
        # If more than 50% of the key consists of the same character
        if any(api_key.count(c) > len(api_key) * 0.5 for c in set(api_key)):
            return False

        # Detect common "nonsense" patterns
        nonsense = ["ebani", "huesos", "idinahui", "fake", "dummy", "example"]
        if any(n in api_key.lower() for n in nonsense):
            return False

        return True

    def get_max_retries(self) -> int:
        return self.DEFAULT_MAX_RETRIES

    def get_timeout_seconds(self) -> int:
        return self.DEFAULT_TIMEOUT_SECONDS

    @staticmethod
    def contains_any(text: str, indicators: Set[str]) -> bool:
        text_lower = text.lower()
        return any(indicator.lower() in text_lower for indicator in indicators)

    @staticmethod
    def truncate_response(response: str, max_length: int = 200) -> str:
        if not response:
            return ""
        return (response[:max_length] + "...") if len(response) > max_length else response

    QUOTA_INDICATORS = {
        "credit", "quota", "billing", "insufficient_funds", "payment", "exceeded", "balance", "limit",
        "insufficient_quota", "exceeded_quota", "rate_limit", "rate_limit_exceeded", "RESOURCE_EXHAUSTED"
    }

    UNAUTHORIZED_INDICATORS = {
        "invalid_api_key", "authentication_error", "unauthorized", "invalid x-api-key", "API_KEY_INVALID",
        "API key not valid", "API key expired", "invalid token", "authentication failed"
    }

    PERMISSION_INDICATORS = {
        "permission", "access", "not_authorized_for_model", "forbidden", "read-only", "Pro service"
    }
