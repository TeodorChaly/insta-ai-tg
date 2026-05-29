"""
Точка входа: создаём Bot и Dispatcher, подключаем роутер хендлеров.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import config
from handlers import router as main_router
from payments import router as pay_router

_COMMANDS = {
    "en": [
        BotCommand(command="start",   description="Start / restart the bot"),
        BotCommand(command="new",     description="New scan (likes are kept)"),
        BotCommand(command="liked",   description="Liked profiles list"),
        BotCommand(command="profile", description="My search filters"),
        BotCommand(command="credits", description="Balance & buy scans"),
        BotCommand(command="lang",    description="Change language"),
        BotCommand(command="cancel",  description="Cancel current action"),
    ],
    "ru": [
        BotCommand(command="start",   description="Начать / перезапустить бота"),
        BotCommand(command="new",     description="Новый скан (лайки сохраняются)"),
        BotCommand(command="liked",   description="Список лайкнутых профилей"),
        BotCommand(command="profile", description="Мои фильтры поиска"),
        BotCommand(command="credits", description="Баланс и покупка сканов"),
        BotCommand(command="lang",    description="Сменить язык"),
        BotCommand(command="cancel",  description="Отменить текущее действие"),
    ],
    "de": [
        BotCommand(command="start",   description="Bot starten / neu starten"),
        BotCommand(command="new",     description="Neuer Scan (Likes bleiben)"),
        BotCommand(command="liked",   description="Gelikte Profile"),
        BotCommand(command="profile", description="Meine Suchfilter"),
        BotCommand(command="credits", description="Guthaben & Scans kaufen"),
        BotCommand(command="lang",    description="Sprache ändern"),
        BotCommand(command="cancel",  description="Aktion abbrechen"),
    ],
}


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    bot = Bot(
        token=config.TELEGRAM_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    from aiogram.types import BotCommandScopeDefault
    for lang_code, cmds in _COMMANDS.items():
        await bot.set_my_commands(cmds, language_code=lang_code)
    # дефолтный набор (для остальных языков) — английский
    await bot.set_my_commands(_COMMANDS["en"], scope=BotCommandScopeDefault())
    logging.info("Команды зарегистрированы для en / ru / de")

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(pay_router)   # payments первым — pre_checkout/successful_payment
    dp.include_router(main_router)

    logging.info("Бот запущен. Ctrl+C для остановки.")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "pre_checkout_query"],
    )


if __name__ == "__main__":
    asyncio.run(main())
