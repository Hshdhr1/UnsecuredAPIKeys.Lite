import asyncio
import httpx
from typing import List, Optional
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class TermbinSearchProvider(BaseSearchProvider):
    @property
    def provider_name(self) -> str:
        return "Termbin"

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        # Termbin doesn't have a search or archive.
        # This provider is a placeholder for direct URL processing
        # or listening to real-time streams if they were available.
        return []

    async def process_direct_url(self, url: str, query_id: int) -> Optional[RepoReference]:
        if "termbin.com" in url:
            # Example: https://termbin.com/xyz
            return RepoReference(
                search_query_id=query_id,
                provider=self.provider_name,
                repo_owner="termbin",
                repo_name="direct",
                repo_url=url,
                file_path="paste.txt",
                file_url=url,
                api_content_url=url,
                found_utc=datetime.now(timezone.utc)
            )
        return None
