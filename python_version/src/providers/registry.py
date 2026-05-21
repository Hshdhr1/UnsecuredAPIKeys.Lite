from typing import List, Type, Optional
from .base import BaseApiKeyProvider
from .anthropic import AnthropicProvider
from .moonshot import MoonshotAIProvider
from .openai import OpenAIProvider
from .google import GoogleProvider
from .mistral import MistralAIProvider
from .openrouter import OpenRouterProvider
from .deepseek import DeepSeekProvider
from .perplexity import PerplexityAIProvider
from .groq import GroqProvider
from .xai import XAIProvider
from .zhipu import ZhipuAIProvider
from .stripe import StripeProvider
from .huggingface import HuggingFaceProvider
from .github_search import GitHubSearchProvider
from .gitlab_search import GitLabSearchProvider
from .sourcegraph_search import SourceGraphSearchProvider
from .pastebin_search import PastebinSearchProvider
from .termbin_search import TermbinSearchProvider

class ApiProviderRegistry:
    _providers: List[Type[BaseApiKeyProvider]] = [
        AnthropicProvider,
        MoonshotAIProvider,
        OpenAIProvider,
        GoogleProvider,
        MistralAIProvider,
        OpenRouterProvider,
        DeepSeekProvider,
        PerplexityAIProvider,
        GroqProvider,
        XAIProvider,
        ZhipuAIProvider,
        StripeProvider,
        HuggingFaceProvider
    ]

    _search_providers: List[Type] = [
        GitHubSearchProvider,
        GitLabSearchProvider,
        SourceGraphSearchProvider,
        PastebinSearchProvider,
        TermbinSearchProvider
    ]

    @classmethod
    def get_all_providers(cls) -> List[BaseApiKeyProvider]:
        return [p() for p in cls._providers]

    @classmethod
    def get_all_search_providers(cls) -> List:
        return [p() for p in cls._search_providers]

    @classmethod
    def get_provider_by_name(cls, name: str) -> Optional[BaseApiKeyProvider]:
        for p_cls in cls._providers:
            p = p_cls()
            if p.provider_name.lower() == name.lower():
                return p
        return None
