import asyncio
import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .database.models import APIKey, ApiStatusEnum, ApiTypeEnum
from .providers.registry import ApiProviderRegistry
from .providers.base import ValidationAttemptStatus


class VerifierBot:
    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.logger = logging.getLogger("VerifierBot")
        self.registry = ApiProviderRegistry()
        self.providers = self.registry.get_all_providers()

    async def run_cycle(self):
        self.logger.info("Starting verification cycle...")
        async with self.session_factory() as session:
            # Fetch all keys for verification without restrictions
            stmt = select(APIKey).limit(100)

            result = await session.execute(stmt)
            keys = result.scalars().all()

            if not keys:
                self.logger.info("No keys to verify.")
                return

            self.logger.info(f"Processing {len(keys)} keys...")

            async with httpx.AsyncClient(timeout=30.0) as client:
                tasks = [self.verify_single_key(session, key, client) for key in keys]
                await asyncio.gather(*tasks)

            await session.commit()
        self.logger.info("Verification cycle completed.")

    async def verify_single_key(self, session: AsyncSession, key: APIKey, client: httpx.AsyncClient):
        import re
        provider = None
        for p in self.providers:
            for pattern in p.regex_patterns:
                if re.search(pattern, key.api_key):
                    provider = p
                    break
            if provider:
                break

        if not provider:
            # Fallback to first provider or mark as unknown
            provider = self.providers[0] if self.providers else None

        if not provider:
            self.logger.warning(f"No provider found for key {key.id}")
            return

        try:
            result = await provider.validate_key_async(key.api_key, client)

            key.status = self.convert_status(result.status)
            key.last_checked_utc = datetime.now(timezone.utc)
            if result.status == ValidationAttemptStatus.VALID:
                try:
                    key.api_type = ApiTypeEnum(provider.api_type)
                except ValueError:
                    key.api_type = ApiTypeEnum.UNKNOWN
                key.error_count = 0
            elif result.status in (ValidationAttemptStatus.HTTP_ERROR, ValidationAttemptStatus.NETWORK_ERROR):
                key.error_count += 1

            self.logger.info(f"Key {key.id} verified: {key.status}")
        except Exception as e:
            self.logger.error(f"Error verifying key {key.id}: {e}")
            key.error_count += 1

    def convert_status(self, status: ValidationAttemptStatus) -> ApiStatusEnum:
        mapping = {
            ValidationAttemptStatus.VALID: ApiStatusEnum.VALID,
            ValidationAttemptStatus.UNAUTHORIZED: ApiStatusEnum.INVALID,
            ValidationAttemptStatus.HTTP_ERROR: ApiStatusEnum.ERROR,
            ValidationAttemptStatus.NETWORK_ERROR: ApiStatusEnum.ERROR,
            ValidationAttemptStatus.PROVIDER_SPECIFIC_ERROR: ApiStatusEnum.ERROR,
        }
        return mapping.get(status, ApiStatusEnum.ERROR)
