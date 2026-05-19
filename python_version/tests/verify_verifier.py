import asyncio
import logging
from python_version.src.verifier import VerifierBot
from python_version.src.database.models import Base, APIKey, ApiStatusEnum, SearchProviderEnum
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def verify_verifier():
    logging.basicConfig(level=logging.INFO)
    db_url = "sqlite+aiosqlite:///:memory:"

    # Setup test DB
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        # Add a dummy key
        key = APIKey(
            api_key="sk-ant-api01-dummy-key-for-testing-purposes-only",
            status=ApiStatusEnum.UNVERIFIED,
            search_provider=SearchProviderEnum.GITHUB
        )
        session.add(key)
        await session.commit()

    bot = VerifierBot(db_url)
    # We won't actually run the HTTP requests in this mock check to avoid network issues
    # but we can verify the bot initializes and can fetch keys.
    print("VerifierBot initialized and test database prepared.")

    # Basic check of the status conversion logic
    from python_version.src.providers.base import ValidationAttemptStatus
    assert bot.convert_status(ValidationAttemptStatus.VALID) == ApiStatusEnum.VALID
    print("Status conversion logic verified.")

if __name__ == "__main__":
    try:
        import aiosqlite
    except ImportError:
        print("aiosqlite not installed, installing...")
        import subprocess
        subprocess.check_call(["pip", "install", "aiosqlite"])

    asyncio.run(verify_verifier())
