import asyncio
import httpx
from typing import List
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class GitHubSearchProvider(BaseSearchProvider):
    @property
    def provider_name(self) -> str:
        return "GitHub"

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        results = []
        headers = {
            "Authorization": f"token {token.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "UnsecuredAPIKeys-Scraper"
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            page = 1
            per_page = 100

            while True:
                url = f"https://api.github.com/search/code?q={httpx.utils.quote(query.query)}&page={page}&per_page={per_page}"
                self.logger.info(f"Requesting GitHub: {url}")

                response = await client.get(url, headers=headers)

                if response.status_code == 403: # Rate limit
                    reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                    sleep_time = max(0, reset_time - datetime.now(timezone.utc).timestamp()) + 1
                    self.logger.warning(f"GitHub Rate limit. Sleeping {sleep_time}s")
                    await asyncio.sleep(min(sleep_time, 60)) # Cap sleep
                    continue

                response.raise_for_status()
                data = response.json()

                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    repo = item.get("repository", {})
                    results.append(RepoReference(
                        search_query_id=query.id,
                        provider=self.provider_name,
                        repo_id=repo.get("id", 0),
                        repo_owner=repo.get("owner", {}).get("login"),
                        repo_name=repo.get("name"),
                        repo_url=repo.get("html_url"),
                        file_path=item.get("path"),
                        file_url=item.get("html_url"),
                        api_content_url=item.get("url"),
                        file_sha=item.get("sha"),
                        found_utc=datetime.now(timezone.utc),
                        branch=repo.get("default_branch")
                    ))

                if len(items) < per_page or len(results) >= 1000:
                    break

                page += 1
                await asyncio.sleep(2)

        return results
