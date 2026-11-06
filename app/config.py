import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).with_name(".env"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = os.getenv("MODEL", "deepseek-chat")
MAX_STEPS = int(os.getenv("MAX_STEPS", "5"))
GATEWAY_SERVICE_KEY = os.getenv("GATEWAY_SERVICE_KEY", "")
APPLICATION_SYSTEM_URL = os.getenv(
    "APPLICATION_SYSTEM_URL", "http://127.0.0.1:8001"
).rstrip("/")
REVIEW_SYSTEM_URL = os.getenv(
    "REVIEW_SYSTEM_URL", "http://127.0.0.1:8002"
).rstrip("/")

if MAX_STEPS < 1:
    raise ValueError("MAX_STEPS must be at least 1")
