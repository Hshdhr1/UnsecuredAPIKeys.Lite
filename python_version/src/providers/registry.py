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
from .generic_archive import GenericArchiveSearchProvider

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

    _search_providers: List = [
        GitHubSearchProvider(),
        GitLabSearchProvider(),
        SourceGraphSearchProvider(),
        PastebinSearchProvider(),
        TermbinSearchProvider(),
        GenericArchiveSearchProvider("Gist", "https://gist.github.com", "discover", r'href="/[^/]+/([a-f0-9]{20,})"', "https://gist.githubusercontent.com/raw/{id}"),
        GenericArchiveSearchProvider("Ghostbin", "https://ghostbin.co", "archive", r'href="/paste/([a-z0-9]+)"', "https://ghostbin.co/paste/{id}/raw"),
        GenericArchiveSearchProvider("Hastebin", "https://hastebin.com", "", r'"key":"([a-z0-9]+)"', "https://hastebin.com/raw/{id}"),
        GenericArchiveSearchProvider("Bitbucket", "https://bitbucket.org", "repo/all", r'href="/([^/]+/[^/]+)/"', "https://bitbucket.org/{id}/raw/master/README.md"), # Example
        GenericArchiveSearchProvider("Gitee", "https://gitee.com", "explore/all", r'href="/([^/]+/[^/]+)"', "https://gitee.com/{id}/raw/master/README.md"),
        GenericArchiveSearchProvider("Codeberg", "https://codeberg.org", "explore/repos", r'href="/([^/]+/[^/]+)"', "https://codeberg.org/{id}/raw/branch/main/README.md"),
        GenericArchiveSearchProvider("NPM", "https://www.npmjs.com", "advisories", r'href="/advisory/(\d+)"', "https://registry.npmjs.org/{id}"),
        GenericArchiveSearchProvider("PyPI", "https://pypi.org", "", r'href="/project/([^/]+)/"', "https://pypi.org/pypi/{id}/json"),
        GenericArchiveSearchProvider("DockerHub", "https://hub.docker.com", "search", r'/r/([^/]+/[^/]+)', "https://hub.docker.com/v2/repositories/{id}"),
        GenericArchiveSearchProvider("SourceForge", "https://sourceforge.net", "directory", r'href="/projects/([^/]+)/"', "https://sourceforge.net/projects/{id}/files/"),
        GenericArchiveSearchProvider("Launchpad", "https://launchpad.net", "+projects", r'href="/\+projects/([^/]+)"', "https://launchpad.net/{id}"),
        GenericArchiveSearchProvider("PublicWWW", "https://publicwww.com", "", r'domain:([^ ]+)', "https://publicwww.com/oss/{id}"),
        GenericArchiveSearchProvider("GoogleDork", "https://www.google.com", "search?q=site:github.com+sk-", r'url\?q=([^&]+)', "{id}"),
        GenericArchiveSearchProvider("BingDork", "https://www.bing.com", "search?q=site:github.com+sk-", r'href="([^"]+)"', "{id}"),
        GenericArchiveSearchProvider("IntelligenceX", "https://intelx.io", "", r'([a-f0-9-]{36})', "https://intelx.io/?did={id}")
    ]

    @classmethod
    def get_all_providers(cls) -> List[BaseApiKeyProvider]:
        return [p() for p in cls._providers]

    @classmethod
    def get_all_search_providers(cls) -> List:
        return cls._search_providers

    @classmethod
    def get_provider_by_name(cls, name: str) -> Optional[BaseApiKeyProvider]:
        for p_cls in cls._providers:
            p = p_cls()
            if p.provider_name.lower() == name.lower():
                return p
        return None
