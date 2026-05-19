import asyncio
import logging
from python_version.src.scraper import ScraperBot
from python_version.src.database.models import Base, SearchQuery
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from datetime import datetime, timedelta, timezone

async def verify_scraper():
    logging.basicConfig(level=logging.INFO)
    # Using a file-based SQLite database for testing because :memory: is cleared
    # when the connection is closed, and ScraperBot creates its own engine.
    db_path = "test_scraper.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # Setup test DB
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        # Add a dummy query
        query = SearchQuery(
            query="anthropic_key",
            is_enabled=True,
            last_search_utc=datetime.now(timezone.utc) - timedelta(hours=3)
        )
        session.add(query)
        await session.commit()

    bot = ScraperBot(db_url)
    await bot.run_cycle()
    print("ScraperBot cycle completed successfully.")

    # Cleanup
    import os
    await engine.dispose()
    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    asyncio.run(verify_scraper())
