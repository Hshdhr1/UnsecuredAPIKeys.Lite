import asyncio
import httpx
from typing import List
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class PastebinSearchProvider(BaseSearchProvider):
    @property
    def provider_name(self) -> str:
        return "Pastebin"

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        # Note: Pastebin public archive doesn't allow searching by keyword easily without Pro.
        # We will scrape the "Archive" (recent pastes) and check them if the query is broad,
        # or we just provide the infrastructure for it.
        results = []
        archive_url = "https://pastebin.com/archive"

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            try:
                response = await client.get(archive_url, headers=headers)
                if response.status_code == 200:
                    import re
                    # Find paste IDs in the archive table
                    # Example: <a href="/XYZ">Title</a>
                    paste_ids = re.findall(r'href="/([a-zA-Z0-9]{8})"', response.text)

                    for pid in set(paste_ids):
                        if pid in ["archive", "messages", "settings", "pro", "login", "signup"]:
                            continue

                        raw_url = f"https://pastebin.com/raw/{pid}"
                        results.append(RepoReference(
                            search_query_id=query.id,
                            provider=self.provider_name,
                            repo_owner="public",
                            repo_name=pid,
                            repo_url=f"https://pastebin.com/{pid}",
                            file_path=f"{pid}.txt",
                            file_url=f"https://pastebin.com/{pid}",
                            api_content_url=raw_url,
                            found_utc=datetime.now(timezone.utc)
                        ))
            except Exception as e:
                self.logger.error(f"Pastebin archive scrape failed: {e}")

        return results
