import asyncio
import httpx
from typing import List
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class SourceGraphSearchProvider(BaseSearchProvider):
    API_ENDPOINT = "https://sourcegraph.com/.api/graphql"

    @property
    def provider_name(self) -> str:
        return "SourceGraph"

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        results = []
        headers = {
            "Authorization": f"token {token.token}",
            "Content-Type": "application/json"
        }

        gql_query = """
        query ($query: String!) {
            search(query: $query, version: V2) {
                results {
                    results {
                        ... on FileMatch {
                            file { path url }
                            repository { name url description defaultBranch { name } }
                        }
                    }
                }
            }
        }
        """

        variables = {
            "query": f"context:global {query.query} count:all"
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.API_ENDPOINT,
                headers=headers,
                json={"query": gql_query, "variables": variables}
            )
            response.raise_for_status()
            data = response.json()

            search_results = data.get("data", {}).get("search", {}).get("results", {}).get("results", [])

            for item in search_results:
                repo = item.get("repository", {})
                file = item.get("file", {})

                name_parts = repo.get("name", "").split('/')

                results.append(RepoReference(
                    search_query_id=query.id,
                    provider=self.provider_name,
                    repo_owner=name_parts[0] if len(name_parts) > 1 else None,
                    repo_name=name_parts[1] if len(name_parts) > 1 else repo.get("name"),
                    repo_url=repo.get("url"),
                    file_path=file.get("path"),
                    file_url=file.get("url"),
                    api_content_url=file.get("url"),
                    branch=repo.get("default_branch", {}).get("name"),
                    found_utc=datetime.now(timezone.utc)
                ))

        return results
