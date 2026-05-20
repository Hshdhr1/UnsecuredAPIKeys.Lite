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
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    bot = None
    if tg_token:
        bot = TelegramBot(tg_token, admin_id, session_factory)
        asyncio.create_task(bot.start())

    scraper = ScraperBot(db_url, tg_bot=bot)
    verifier = VerifierBot(db_url)

    while True:
        try:
            await scraper.run_cycle()
            await verifier.run_cycle()
        except Exception as e:
            logging.error(f"Error in main loop: {e}")

        await asyncio.sleep(600) # 10 minutes

if __name__ == "__main__":
    asyncio.run(main())
