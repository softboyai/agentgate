import json
import re
from typing import Any, Callable

from app import llm
from app.config import MAX_STEPS
from app.tools import TOOL_DEFINITIONS, execute_tool

_READINESS_TERMS = re.compile(
    r"\b(readiness|ready|complete|completeness|missing documents?|"
    r"required documents?|document check|documents needed|paperwork|"
    r"requirement|requirements|required|requires|require|needed|needs)\b",
    re.IGNORECASE,
)
_APPLICATION_ID = re.compile(r"\bAPP-\d+\b", re.IGNORECASE)


def _format_document_check(result_json: str) -> str:
    result = json.loads(result_json)
    if not isinstance(result, dict):
        return f"Document check returned an unexpected result: {result_json}"
    if result.get("passed") is True:
        return "The document check passed."
    if result.get("passed") is not False:
        error = result.get("error")
        if error:
            return f"The document check could not be completed: {error}"
        return f"Document check returned an inconclusive result: {result_json}"

    required_documents = result.get("required_documents", [])
    issues = result.get("issues", [])
    lines = ["The document check did not pass."]
    if isinstance(required_documents, list):
        lines.append(
            "Required documents listed by check_documents: "
            + (", ".join(required_documents) if required_documents else "none")
        )
    lines.append("Issues reported by check_documents:")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            message = issue.get("message")
            if isinstance(message, str):
                lines.append(f"- {message}")
            elif issue.get("code") == "missing" and isinstance(
                issue.get("document"), str
            ):
                lines.append(f"- Required document '{issue['document']}' is missing.")
    return "\n".join(lines)


def _check_readiness_prompt(
    prompt: str,
    print_fn: Callable[[str], None],
) -> str | None:
    if not _READINESS_TERMS.search(prompt):
        return None

    match = _APPLICATION_ID.search(prompt)
    if match is None:
        return "Please provide an application ID so I can check its documents."

    application_id = match.group().upper()
    arguments = json.dumps({"application_id": application_id})
    print_fn(f"Tool call: check_documents({arguments})")
    return _format_document_check(execute_tool("check_documents", arguments))


def run_agent(
    prompt: str,
    print_fn: Callable[[str], None] = print,
) -> str:
    readiness_result = _check_readiness_prompt(prompt, print_fn)
    if readiness_result is not None:
        return readiness_result

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Use only the provided tools and their fake data. "
                "Do not claim to access real systems. For any application "
                "readiness, completeness, or document-requirement question, "
                "you must call check_documents and base the answer only on "
                "that tool's output. Never infer requirements from general "
                "knowledge. List a document as missing only when it appears "
                "in the tool output's issues. Use required_documents from that "
                "output to explain requirements. If passed is true, answer "
                "'The document check passed.' and stop."
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
            if tool_call.function.name == "check_documents":
                return _format_document_check(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
