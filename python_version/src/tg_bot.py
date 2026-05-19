import asyncio
import logging
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .database.models import APIKey, SearchProviderToken, SearchProviderEnum, ApplicationSetting


class TelegramBot:
    def __init__(self, token: str, admin_id: int, db_url: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.admin_id = admin_id
        self.db_url = db_url
        self.engine = create_async_engine(db_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.logger = logging.getLogger("TelegramBot")

        self.setup_handlers()

    def setup_handlers(self):
        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.message.register(self.process_github_token, F.text.startswith("ghp_"))
        self.dp.message.register(self.cmd_export, F.text == "Выгрузить ключи")
        self.dp.message.register(self.cmd_add_github, F.text == "Добавить GitHub токен")

        self.dp.callback_query.register(self.verify_key_callback, F.data.startswith("verify_"))

    async def start(self):
        self.logger.info("Starting Telegram Bot...")
        await self.dp.start_polling(self.bot)

    async def cmd_start(self, message: types.Message):
        if message.from_user.id != self.admin_id:
            await message.answer("У вас нет доступа к этому боту.")
            return

        builder = ReplyKeyboardBuilder()
        builder.button(text="Выгрузить ключи")
        builder.button(text="Добавить GitHub токен")
        builder.adjust(2)

        await message.answer(
            "Добро пожаловать! Выберите действие:",
            reply_markup=builder.as_markup(resize_keyboard=True)
        )

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

    async def cmd_add_github(self, message: types.Message):
        if message.from_user.id != self.admin_id: return
        await message.answer("Пришлите ваш GitHub токен (начинается с ghp_):")

    async def process_github_token(self, message: types.Message):
        if message.from_user.id != self.admin_id: return

        token = message.text.strip()
        async with self.session_factory() as session:
            new_token = SearchProviderToken(
                token=token,
                search_provider=SearchProviderEnum.GITHUB,
                is_enabled=True
            )
            session.add(new_token)
            await session.commit()

        await message.answer(f"GitHub токен успешно добавлен и активирован.")

    async def notify_new_key(self, key_id: int, api_type: str, api_key: str):
        builder = InlineKeyboardBuilder()
        builder.button(text="Проверить", callback_data=f"verify_{key_id}")

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

    async def verify_key_callback(self, callback: types.CallbackQuery):
        if callback.from_user.id != self.admin_id: return

        key_id = int(callback.data.split("_")[1])
        await callback.answer("Запуск проверки...")

        from .verifier import VerifierBot
        verifier = VerifierBot(self.db_url)

        async with self.session_factory() as session:
            stmt = select(APIKey).where(APIKey.id == key_id)
            result = await session.execute(stmt)
            key = result.scalar_one_or_none()

            if not key:
                await callback.message.answer("Ключ не найден в БД.")
                return

            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                await verifier.verify_single_key(session, key, client)
                await session.commit()

            await callback.message.answer(f"Результат проверки ключа {key_id}: {key.status}")
