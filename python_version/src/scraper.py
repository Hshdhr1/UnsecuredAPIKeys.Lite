import asyncio
import logging
import httpx
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .database.models import APIKey, RepoReference, SearchQuery, ApiStatusEnum, SearchProviderEnum, SearchProviderToken
from .providers.registry import ApiProviderRegistry
from .tg_bot import TelegramBot


class ScraperBot:
    def __init__(self, db_url: str, github_token: Optional[str] = None, tg_bot: Optional[TelegramBot] = None):
        self.engine = create_async_engine(db_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.logger = logging.getLogger("ScraperBot")
        self.github_token = github_token
        self.tg_bot = tg_bot
        self.registry = ApiProviderRegistry()
        self.providers = self.registry.get_all_providers()

    async def run_cycle(self):
        self.logger.info("Starting scraping cycle...")
        async with self.session_factory() as session:
            # Fetch queries that are due
            now = datetime.now(timezone.utc)
            stmt = select(SearchQuery).where(
                SearchQuery.is_enabled == True,
                SearchQuery.last_search_utc < now - timedelta(hours=2)
            ).order_by(SearchQuery.last_search_utc)

            result = await session.execute(stmt)
            queries = result.scalars().all()

            if not queries:
                self.logger.info("No queries to run.")
                return

            for query in queries:
                await self.process_query(session, query)
                query.last_search_utc = datetime.now(timezone.utc)
                await session.commit()

        self.logger.info("Scraping cycle completed.")

    async def process_query(self, session: AsyncSession, query: SearchQuery):
        self.logger.info(f"Running query: {query.query}")

        # Get all enabled tokens
        stmt = select(SearchProviderToken).where(SearchProviderToken.is_enabled == True)
        result = await session.execute(stmt)
        tokens = result.scalars().all()

        if not tokens:
            self.logger.warning("No search provider tokens available.")
            return

        search_providers = self.registry.get_all_search_providers()

        for token in tokens:
            # Find matching provider
            provider = next((p for p in search_providers if p.provider_name.lower() == token.search_provider.value.lower()), None)

            if not provider:
                continue

            self.logger.info(f"Searching {provider.provider_name} with token {token.id}")
            try:
                repo_refs = await provider.search_async(query, token)
                for ref in repo_refs:
                    await self.process_repo_reference(session, ref, query.id, token)
            except Exception as e:
                self.logger.error(f"Search failed for {provider.provider_name}: {e}")

    async def process_repo_reference(self, session: AsyncSession, ref: RepoReference, query_id: int, token: SearchProviderToken):
        # Fetch content from ref.api_content_url and find keys
        self.logger.info(f"Processing reference: {ref.file_url}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                headers = {}
                if ref.provider == "GitHub":
                    headers["Authorization"] = f"token {token.token}"
                elif ref.provider == "GitLab":
                    headers["PRIVATE-TOKEN"] = token.token

                response = await client.get(ref.api_content_url, headers=headers)
                if response.status_code == 200:
                    content = response.text
                    # Run regex patterns from all providers
                    for provider in self.providers:
                        for pattern in provider.regex_patterns:
                            import re
                            matches = re.findall(pattern, content)
                            for match in matches:
                                await self.save_key(session, {
                                    "api_key": match,
                                    "repo_url": ref.repo_url,
                                    "file_url": ref.file_url,
                                    "repo_owner": ref.repo_owner,
                                    "repo_name": ref.repo_name
                                }, query_id)
            except Exception as e:
                self.logger.error(f"Failed to fetch content from {ref.api_content_url}: {e}")

    async def save_key(self, session: AsyncSession, res: Dict[str, Any], query_id: int):
        api_key_str = res["api_key"]

        # Check if key already exists
        stmt = select(APIKey).where(APIKey.api_key == api_key_str)
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            self.logger.info(f"Key {api_key_str[:10]}... already exists.")
            return

        now = datetime.now(timezone.utc)
        new_key = APIKey(
            api_key=api_key_str,
            status=ApiStatusEnum.UNVERIFIED,
            search_provider=SearchProviderEnum.GITHUB,
            first_found_utc=now,
            last_found_utc=now
        )

        ref = RepoReference(
            repo_url=res["repo_url"],
            file_url=res["file_url"],
            repo_owner=res["repo_owner"],
            repo_name=res["repo_name"],
            repo_id=0,  # Added to satisfy NOT NULL constraint
            search_query_id=query_id,
            line_number=1,
            found_utc=now
        )
        new_key.references.append(ref)

        session.add(new_key)
        await session.flush()  # To get the ID

        self.logger.info(f"Saved new key found via query {query_id}")

        if self.tg_bot:
            await self.tg_bot.notify_new_key(new_key.id, str(new_key.api_type), new_key.api_key)
