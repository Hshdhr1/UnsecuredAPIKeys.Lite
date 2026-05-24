import asyncio
import logging
import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from python_version.src.tg_bot import TelegramBot
from python_version.src.scraper import ScraperBot
from python_version.src.verifier import VerifierBot

async def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///dev.db")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_id = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))

    engine = create_async_engine(db_url)

    # Ensure tables are created
    from python_version.src.database.models import Base, SearchQuery
    from sqlalchemy import func, select
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed default queries if empty
    async with session_factory() as session:
        query_count = await session.scalar(select(func.count()).select_from(SearchQuery))
        if query_count == 0:
            logging.info("Seeding default search queries...")
            from datetime import datetime, timedelta, timezone
            default_patterns = ["sk-", "sk-proj-", "AIza", "gsk_", "pplx-", "xai-", "sk_test_"]
            for pattern in default_patterns:
                session.add(SearchQuery(
                    query=pattern,
                    is_enabled=True,
                    last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)
                ))
            await session.commit()

    bot = None
    if tg_token:
        bot = TelegramBot(tg_token, admin_id, db_url, session_factory)
        asyncio.create_task(bot.start())

    scraper = ScraperBot(db_url, tg_bot=bot)
    verifier = VerifierBot(db_url)

    print("\n" + "="*40)
    print(" 💎 UnsecuredAPIKeys Python Bot System ")
    print("="*40)
    print(f"[*] Database: {db_url}")
    print(f"[*] Telegram: {'Enabled' if bot else 'Disabled'}")
    print(f"[*] Admin ID: {admin_id}")
    print("[*] Status: RUNNING (Ctrl+C to stop)")
    print("="*40 + "\n")

    try:
        while True:
            try:
                await scraper.run_cycle()
                await verifier.run_cycle()
            except Exception as e:
                logging.error(f"Error in main loop: {e}")

            await asyncio.sleep(600) # 10 minutes
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("Shutdown initiated by user...")

if __name__ == "__main__":
    asyncio.run(main())
