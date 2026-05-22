import asyncio
import httpx
from typing import List
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class GenericArchiveSearchProvider(BaseSearchProvider):
    def __init__(self, name: str, base_url: str, archive_path: str, id_regex: str, raw_template: str, logger=None):
        super().__init__(logger)
        self._name = name
        self._base_url = base_url
        self._archive_path = archive_path
        self._id_regex = id_regex
        self._raw_template = raw_template

    @property
    def provider_name(self) -> str:
        return self._name

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        results = []
        url = f"{self._base_url}/{self._archive_path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    import re
                    ids = re.findall(self._id_regex, response.text)

                    for item_id in set(ids):
                        raw_url = self._raw_template.replace("{id}", item_id)
                        results.append(RepoReference(
                            search_query_id=query.id,
                            provider=self.provider_name,
                            repo_owner="public",
                            repo_name=item_id,
                            repo_url=f"{self._base_url}/{item_id}",
                            file_path=f"{item_id}.txt",
                            file_url=f"{self._base_url}/{item_id}",
                            api_content_url=raw_url,
                            found_utc=datetime.now(timezone.utc)
                        ))
            except Exception as e:
                self.logger.error(f"{self._name} archive scrape failed: {e}")

        return results
