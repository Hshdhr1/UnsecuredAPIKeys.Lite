import asyncio
import logging
import httpx
import re
import base64
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
        self.on_item_scanned = None
        self.on_key_found = None

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
        tokens = list(result.scalars().all())

        search_providers = self.registry.get_all_search_providers()

        # Add virtual tokens for public providers that don't need auth
        public_providers = ["Pastebin", "Termbin", "Gist", "Ghostbin", "Hastebin", "GoogleDork", "BingDork"]
        for p_name in public_providers:
            try:
                p_enum = SearchProviderEnum[p_name.upper()]
                # Check if we already have a token for this
                if not any(t.search_provider == p_enum for t in tokens):
                    tokens.append(SearchProviderToken(id=0, token="public", search_provider=p_enum, is_enabled=True))
            except KeyError: continue

        if not tokens:
            self.logger.warning("No search provider tokens available.")
            return

        for token in tokens:
            # Find matching provider by Enum member name or string value
            token_p_name = token.search_provider.name.lower()
            provider = next((p for p in search_providers if p.provider_name.lower() == token_p_name), None)

            if not provider:
                # Try by value if name didn't match (for custom enums)
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
        if self.on_item_scanned:
            self.on_item_scanned()

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                headers = {
                    "User-Agent": "UnsecuredAPIKeys-Scraper/1.0"
                }
                if ref.provider == "GitHub":
                    headers["Authorization"] = f"token {token.token}"
                elif ref.provider == "GitLab":
                    headers["PRIVATE-TOKEN"] = token.token

                response = await client.get(ref.api_content_url, headers=headers)
                if response.status_code == 200:
                    content = ""

                    # If it's an API URL, it's likely JSON with base64 content
                    # If it's a raw URL (from Selenium), it's direct text
                    if "api.github.com" in ref.api_content_url:
                        data = response.json()
                        content_b64 = data.get("content", "").replace("\n", "")
                        content = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
                    elif "gitlab.com/api" in ref.api_content_url:
                        data = response.json()
                        content_b64 = data.get("content", "")
                        content = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
                    else:
                        # Fallback for direct text (raw.githubusercontent.com, pastebin, etc.)
                        content = response.text

                    # Run regex patterns from all providers
                    for provider in self.providers:
                        for pattern in provider.regex_patterns:
                            matches = re.findall(pattern, content)
                            for match in matches:
                                await self.save_key(session, {
                                    "api_key": match,
                                    "repo_url": ref.repo_url,
                                    "file_url": ref.file_url,
                                    "repo_owner": ref.repo_owner,
                                    "repo_name": ref.repo_name,
                                    "search_provider": ref.provider
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
        provider_name = res.get("search_provider", "GitHub")
        try:
            # Map string name to IntEnum member
            provider_enum = SearchProviderEnum[provider_name.upper()]
        except (KeyError, ValueError):
            provider_enum = SearchProviderEnum.UNKNOWN

        new_key = APIKey(
            api_key=api_key_str,
            status=ApiStatusEnum.UNVERIFIED,
            search_provider=provider_enum,
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

        if self.on_key_found:
            self.on_key_found(new_key)

        self.logger.info(f"Saved new key found via query {query_id}")

        if self.tg_bot:
            # Use name property for cleaner display
            api_type_name = str(new_key.api_type.name) if hasattr(new_key.api_type, 'name') else str(new_key.api_type)
            await self.tg_bot.notify_new_key(new_key.id, api_type_name, new_key.api_key)
