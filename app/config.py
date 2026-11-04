import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).with_name(".env"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = os.getenv("MODEL", "deepseek-chat")
MAX_STEPS = int(os.getenv("MAX_STEPS", "5"))

if MAX_STEPS < 1:
    raise ValueError("MAX_STEPS must be at least 1")
