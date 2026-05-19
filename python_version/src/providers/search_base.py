from abc import ABC, abstractmethod
from typing import Iterable, List, Optional
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class BaseSearchProvider(ABC):
    def __init__(self, logger=None):
        import logging
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        pass
