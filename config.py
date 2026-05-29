import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
HIKER_API_TOKEN = os.getenv("HIKER_API_TOKEN", "")

BASE       = "https://api.hikerapi.com/v2"
HIKER_BASE = "https://api.hikerapi.com"

# глобальные семафоры — общие на ВЕСЬ бот, все пользователи
# HikerAPI лимит: 15 req/s, avg ответ ~300ms → 15 × 0.3 = 4 слота
# OpenAI лимит:   15 req/s, avg ответ ~3.5s  → держим 15 слотов (даёт ~4 req/s, с запасом)
API_SEM = asyncio.Semaphore(4)   # одновременных запросов к HikerAPI
GPT_SEM = asyncio.Semaphore(15)  # одновременных запросов к OpenAI

# порог подписчиков — аккаунты с большим числом пропускаем
SKIP_FOLLOWERS = 10_000
