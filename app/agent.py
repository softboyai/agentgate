from typing import Any, Callable

from app import llm
from app.config import MAX_STEPS
from app.tools import TOOL_DEFINITIONS, execute_tool


def run_agent(
    prompt: str,
    print_fn: Callable[[str], None] = print,
) -> str:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Use only the provided tools and their fake data. "
                "Do not claim to access real systems."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    steps = 0

    while True:
        response = llm.get_chat_completion(messages, TOOL_DEFINITIONS)
        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls or []

        if not tool_calls:
            return assistant_message.content or ""

        messages.append(assistant_message.model_dump(exclude_none=True))
        for tool_call in tool_calls:
            print_fn(
                f"Tool call: {tool_call.function.name}"
                f"({tool_call.function.arguments})"
            )

        for tool_call in tool_calls:
            if steps >= MAX_STEPS:
                return f"Stopped after reaching the {MAX_STEPS}-step limit."

            steps += 1
            result = execute_tool(
                tool_call.function.name,
                tool_call.function.arguments,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
