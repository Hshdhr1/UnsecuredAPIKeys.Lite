import asyncio
import httpx
import gitlab
from typing import List
from datetime import datetime, timezone
from .search_base import BaseSearchProvider
from ..database.models import RepoReference, SearchQuery, SearchProviderToken


class GitLabSearchProvider(BaseSearchProvider):
    @property
    def provider_name(self) -> str:
        return "GitLab"

    async def search_async(self, query: SearchQuery, token: SearchProviderToken) -> List[RepoReference]:
        results = []
        # GitLab API is easier to use via python-gitlab but let's keep it async/httpx for consistency where possible
        # or use sync in a thread. Let's use httpx.

        async with httpx.AsyncClient(timeout=30.0) as client:
            page = 1
            per_page = 100

            while True:
                url = f"https://gitlab.com/api/v4/search?scope=blobs&search={httpx.utils.quote(query.query)}&page={page}&per_page={per_page}"
                headers = {"PRIVATE-TOKEN": token.token}

                response = await client.get(url, headers=headers)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                data = response.json()

                if not data:
                    break

                for item in data:
                    project_id = item.get("project_id")
                    # Fetch project details (can be slow, ideally batch or cache)
                    proj_url = f"https://gitlab.com/api/v4/projects/{project_id}"
                    proj_resp = await client.get(proj_url, headers=headers)
                    if proj_resp.status_code == 200:
                        project = proj_resp.json()

                        results.append(RepoReference(
                            search_query_id=query.id,
                            provider=self.provider_name,
                            repo_owner=project.get("namespace", {}).get("full_path"),
                            repo_name=project.get("name"),
                            repo_url=project.get("web_url"),
                            repo_id=project.get("id"),
                            file_path=item.get("path"),
                            file_url=f"{project.get('web_url')}/-/blob/{item.get('ref') or project.get('default_branch')}/{item.get('path')}",
                            api_content_url=f"https://gitlab.com/api/v4/projects/{project_id}/repository/files/{httpx.utils.quote(item.get('path', ''))}?ref={item.get('ref') or project.get('default_branch')}",
                            branch=item.get("ref") or project.get("default_branch"),
                            found_utc=datetime.now(timezone.utc)
                        ))

                if len(data) < per_page:
                    break
                page += 1
                await asyncio.sleep(1)

        return results
