import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BOT_API_SERVER_URL = os.environ["BOT_API_SERVER_URL"].rstrip("/")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/audiolecture")
