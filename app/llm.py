from typing import Any

from openai import OpenAI

BASE_URL = "https://api.deepseek.com"


def get_chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    from app.config import DEEPSEEK_API_KEY, MODEL

    api_key = DEEPSEEK_API_KEY
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing from the project .env file")

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0,
    )
