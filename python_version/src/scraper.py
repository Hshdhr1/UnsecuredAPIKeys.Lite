import asyncio
import logging
import httpx
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .database.models import APIKey, RepoReference, SearchQuery, ApiStatusEnum, SearchProviderEnum
from .providers.registry import ApiProviderRegistry


class ScraperBot:
    def __init__(self, db_url: str, github_token: Optional[str] = None):
        self.engine = create_async_engine(db_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.logger = logging.getLogger("ScraperBot")
        self.github_token = github_token
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
        # Mock GitHub search for demo
        # In real life, we would use GitHub Search API
        results = [
            {
                "repo_url": "https://github.com/example/repo",
                "file_url": "https://github.com/example/repo/blob/main/config.py",
                "api_key": "sk-ant-api01-discovered-key-12345",
                "repo_owner": "example",
                "repo_name": "repo"
            }
        ]

        for res in results:
            await self.save_key(session, res, query.id)

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
        self.logger.info(f"Saved new key found via query {query_id}")
