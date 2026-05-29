import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN: str = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
TIMEZONE: str = os.environ.get("TIMEZONE", "Europe/Moscow")
DB_PATH: str = os.environ.get("DB_PATH", "lingo.db")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is not set")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY is not set")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set")
