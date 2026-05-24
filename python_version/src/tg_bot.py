import asyncio
import logging
import httpx
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .database.models import APIKey, SearchProviderToken, SearchProviderEnum, ApplicationSetting


class TelegramBot:
    def __init__(self, token: str, admin_id: int, db_url: str, session_factory: async_sessionmaker[AsyncSession]):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.admin_id = admin_id
        self.db_url = db_url
        self.session_factory = session_factory
        self.logger = logging.getLogger("TelegramBot")

        self.setup_handlers()

    def setup_handlers(self):
        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.message.register(self.process_token, F.text.regexp(r"^(ghp_|glpat-|sgp_)"))
        self.dp.message.register(self.cmd_selenium, F.text == "🌐 Selenium Парсинг")
        self.dp.message.register(self.cmd_export, F.text == "📦 Выгрузить ключи")
        self.dp.message.register(self.cmd_add_token, F.text == "🔑 Добавить токен")
        self.dp.message.register(self.cmd_sources, F.text == "🌐 Источники")
        self.dp.message.register(self.cmd_add_query, Command("add_query"))
        self.dp.message.register(self.cmd_seed, Command("seed"))

        self.dp.callback_query.register(self.verify_key_callback, F.data.startswith("verify_"))

    async def start(self):
        self.logger.info("Starting Telegram Bot...")
        await self.dp.start_polling(self.bot)

    async def cmd_start(self, message: types.Message):
        if message.from_user.id != self.admin_id:
            await message.answer("У вас нет доступа к этому боту.")
            return

        builder = ReplyKeyboardBuilder()
        builder.button(text="📦 Выгрузить ключи")
        builder.button(text="🔑 Добавить токен")
        builder.button(text="🌐 Источники")
        builder.button(text="🌐 Selenium Парсинг")
        builder.adjust(2)

        await message.answer(
            "💎 **UnsecuredAPIKeys Admin Bot**\n\nВыберите действие из меню ниже:",
            reply_markup=builder.as_markup(resize_keyboard=True),
            parse_mode="Markdown"
        )

    async def cmd_sources(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        async with self.session_factory() as session:
            stmt = select(SearchProviderToken)
            result = await session.execute(stmt)
            tokens = result.scalars().all()

            if not tokens:
                await message.answer("Активных источников (токенов) не найдено.")
                return

            text = "🛰 **Активные источники поиска:**\n\n"
            for t in tokens:
                status = "✅" if t.is_enabled else "❌"
                text += f"{status} {t.search_provider.value}: `{t.token[:10]}...`\n"

            await message.answer(text, parse_mode="Markdown")

    async def cmd_export(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        async with self.session_factory() as session:
            stmt = select(APIKey).limit(100)
            result = await session.execute(stmt)
            keys = result.scalars().all()

            if not keys:
                await message.answer("Ключей не найдено.")
                return

            text = "Список последних ключей:\n\n"
            for key in keys:
                text += f"ID: {key.id} | {key.api_type} | {key.status}\n`{key.api_key}`\n\n"
                if len(text) > 3500:
                    await message.answer(text, parse_mode="Markdown")
                    text = ""

            if text:
                await message.answer(text, parse_mode="Markdown")

    async def cmd_add_token(self, message: types.Message):
        if message.from_user.id != self.admin_id: return
        await message.answer(
            "Пришлите ваш токен одного из провайдеров:\n"
            "• **GitHub**: начинается с `ghp_`\n"
            "• **GitLab**: начинается с `glpat-`\n"
            "• **SourceGraph**: начинается с `sgp_`",
            parse_mode="Markdown"
        )

    async def process_token(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        token = message.text.strip()
        provider = SearchProviderEnum.UNKNOWN

        if token.startswith("ghp_"): provider = SearchProviderEnum.GITHUB
        elif token.startswith("glpat-"): provider = SearchProviderEnum.GITLAB
        elif token.startswith("sgp_"): provider = SearchProviderEnum.SOURCEGRAPH
        elif token == "pastebin": provider = SearchProviderEnum.PASTEBIN
        elif token == "termbin": provider = SearchProviderEnum.TERMBIN

        async with self.session_factory() as session:
            # Duplicate check
            existing = await session.execute(select(SearchProviderToken).where(SearchProviderToken.token == token))
            if existing.scalar_one_or_none():
                await message.answer("⚠️ Этот токен уже добавлен.")
                return

            new_token = SearchProviderToken(
                token=token,
                search_provider=provider,
                is_enabled=True
            )
            session.add(new_token)
            await session.commit()

        await message.answer(f"✅ Токен {provider.name} успешно добавлен и активирован.")

    async def cmd_seed(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        from .database.models import SearchQuery
        from datetime import datetime, timedelta, timezone

        default_patterns = ["sk-", "sk-proj-", "AIza", "gsk_", "pplx-", "xai-", "sk_test_"]
        async with self.session_factory() as session:
            for pattern in default_patterns:
                # Basic check to avoid exact duplicates
                stmt = select(SearchQuery).where(SearchQuery.query == pattern)
                existing = await session.execute(stmt)
                if not existing.scalar_one_or_none():
                    session.add(SearchQuery(
                        query=pattern,
                        is_enabled=True,
                        last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)
                    ))
            await session.commit()

        await message.answer("✅ База данных заполнена стандартными запросами.")

    async def cmd_add_query(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        from .database.models import SearchQuery
        from datetime import datetime, timedelta, timezone

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Использование: `/add_query sk-`", parse_mode="Markdown")
            return

        query_text = args[1].strip()
        async with self.session_factory() as session:
            new_query = SearchQuery(
                query=query_text,
                is_enabled=True,
                last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)
            )
            session.add(new_query)
            await session.commit()

        await message.answer(f"✅ Поисковый запрос `{query_text}` добавлен.", parse_mode="Markdown")

    async def notify_new_key(self, key_id: int, api_type: str, api_key: str):
        builder = InlineKeyboardBuilder()
        builder.button(text="🔎 Проверить", callback_data=f"verify_{key_id}")

        text = (
            f"🔔 **Найден новый ключ!**\n\n"
            f"ID: {key_id}\n"
            f"Тип: {api_type}\n"
            f"Ключ: `{api_key}`"
        )

        try:
            await self.bot.send_message(
                self.admin_id,
                text,
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
        except Exception as e:
            self.logger.error(f"Failed to send notification: {e}")

    async def cmd_selenium(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        from .selenium_scraper import SeleniumScraper
        from .scraper import ScraperBot

        scraper = SeleniumScraper()

        await message.answer("🏁 **Запуск Selenium парсинга (headless Chrome)...**\n\nЭто может занять некоторое время.", parse_mode="Markdown")

        # Searching for several common patterns
        queries = ["sk-", "sk-proj-", "AIza", "gsk_"]
        total_results = 0

        for query in queries:
            await message.answer(f"🔍 Поиск в браузере: `{query}`", parse_mode="Markdown")
            results = await scraper.search_github_browser(query)

            if results:
                await message.answer(f"✅ Найдено ссылок для `{query}`: {len(results)}. Начинаю обработку контента...", parse_mode="Markdown")

                # Use ScraperBot logic to process these references
                from .database.models import RepoReference, SearchProviderToken, SearchProviderEnum

                async with self.session_factory() as session:
                    # We need a dummy token object or similar if the method requires it
                    dummy_token = SearchProviderToken(token="selenium", search_provider=SearchProviderEnum.GITHUB)

                    # Create a temporary ScraperBot instance to reuse process_repo_reference
                    scraper_bot = ScraperBot(self.db_url, tg_bot=self)

                    for res in results:
                        ref = RepoReference(
                            repo_url=res["repo_url"],
                            file_url=res["file_url"],
                            api_content_url=res["api_content_url"],
                            repo_owner=res["repo_owner"],
                            repo_name=res["repo_name"],
                            file_path=res["file_path"],
                            provider=res["provider"],
                            repo_id=0,
                            search_query_id=0,
                            line_number=1
                        )
                        await scraper_bot.process_repo_reference(session, ref, 0, dummy_token)
                        await session.commit()

                total_results += len(results)
            else:
                await message.answer(f"ℹ️ По запросу `{query}` ничего не найдено.", parse_mode="Markdown")

        await message.answer(f"🏁 **Selenium парсинг завершен.**\nВсего ссылок обработано: {total_results}", parse_mode="Markdown")

    async def verify_key_callback(self, callback: types.CallbackQuery):
        if callback.from_user.id != self.admin_id: return

        from .verifier import VerifierBot

        key_id = int(callback.data.split("_")[1])
        await callback.answer("Запуск проверки...")

        verifier = VerifierBot(self.db_url)

        async with self.session_factory() as session:
            stmt = select(APIKey).where(APIKey.id == key_id)
            result = await session.execute(stmt)
            key = result.scalar_one_or_none()

            if not key:
                await callback.message.answer("Ключ не найден в БД.")
                return

            async with httpx.AsyncClient(timeout=30.0) as client:
                await verifier.verify_single_key(session, key, client)
                await session.commit()

            await callback.message.answer(f"Результат проверки ключа {key_id}: {key.status}")
