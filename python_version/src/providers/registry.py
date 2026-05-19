from typing import List, Type, Optional
from .base import BaseApiKeyProvider
from .anthropic import AnthropicProvider

class ApiProviderRegistry:
    _providers: List[Type[BaseApiKeyProvider]] = [
        AnthropicProvider
    ]

    @classmethod
    def get_all_providers(cls) -> List[BaseApiKeyProvider]:
        return [p() for p in cls._providers]

    @classmethod
    def get_provider_by_name(cls, name: str) -> Optional[BaseApiKeyProvider]:
        for p_cls in cls._providers:
            p = p_cls()
            if p.provider_name.lower() == name.lower():
                return p
        return None
