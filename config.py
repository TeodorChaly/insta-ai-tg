import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
HIKER_API_TOKEN = os.getenv("HIKER_API_TOKEN", "")

BASE       = "https://api.hikerapi.com/v2"
HIKER_BASE = "https://api.hikerapi.com"

# лимиты параллельных запросов
API_SEM = 5   # одновременных запросов к HikerAPI
GPT_SEM = 3   # одновременных запросов к OpenAI

# порог подписчиков — аккаунты с большим числом пропускаем
SKIP_FOLLOWERS = 10_000
