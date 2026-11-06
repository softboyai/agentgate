import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV_FILE = (PROJECT_ROOT / ".env").resolve()

load_dotenv(ROOT_ENV_FILE)
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
TOKEN_TTL_MINUTES = 15


def load_jwt_secret(env_file: Path = ROOT_ENV_FILE) -> str:
    secret = dotenv_values(env_file).get("JWT_SECRET")
    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET must be set in the project-root .env file and contain "
            "at least 32 characters."
        )
    return secret


JWT_SECRET = load_jwt_secret()

if MAX_STEPS < 1:
    raise ValueError("MAX_STEPS must be at least 1")
