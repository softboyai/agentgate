import json
import re
from typing import Any, Callable

from app import gateway, identity, llm
from app.config import MAX_STEPS

_READINESS_TERMS = re.compile(
    r"\b(readiness|ready|complete|completeness|missing documents?|"
    r"required documents?|document check|documents needed|paperwork|"
    r"requirement|requirements|required|requires|require|needed|needs)\b",
    re.IGNORECASE,
)
_APPLICATION_ID = re.compile(r"\bAPP-\d+\b", re.IGNORECASE)
_DECISION_ACTION = re.compile(
    r"\b(?:approve|approves|approved|reject|rejects|rejected|grant|grants|"
    r"granted|refuse|refuses|refused|issue|issues|issued|issuance|"
    r"sign[\s-]+off)\b",
    re.IGNORECASE,
)
_APPLICATION_CONTEXT = re.compile(
    r"\bapplication(?:s)?\b|\blicen[cs]e(?:s)?\b|\bAPP-\d+\b",
    re.IGNORECASE,
)


def _refuse_decision_prompt(prompt: str) -> str | None:
    if _DECISION_ACTION.search(prompt) and _APPLICATION_CONTEXT.search(prompt):
        return (
            "I cannot approve, reject, grant, refuse, issue, or sign off an "
            "application. Only an authorised officer can do that. I can run "
            "the document check instead."
        )
    return None


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
    token: str,
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
    result = gateway.call_tool(token, "check_documents", json.loads(arguments))
    if result["status"] != "allowed":
        return f"The document check could not be completed: {result['message']}"
    return _format_document_check(json.dumps(result["data"]))


def run_agent(
    prompt: str,
    print_fn: Callable[[str], None] = print,
    agent_id: str = "reader",
) -> str:
    decision_refusal = _refuse_decision_prompt(prompt)
    if decision_refusal is not None:
        return decision_refusal

    identity.seed_demo_agents()
    try:
        token = identity.issue_token(agent_id)
    except identity.AgentUnavailableError as error:
        return f"Cannot start agent: {error}"

    readiness_result = _check_readiness_prompt(prompt, token, print_fn)
    if readiness_result is not None:
        return readiness_result
    try:
        permitted_tools = gateway.get_permitted_tool_definitions(token)
    except identity.TokenVerificationError as error:
        return f"Cannot load permitted tools: {error}"

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
        response = llm.get_chat_completion(messages, permitted_tools)
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
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                result = {
                    "status": "denied",
                    "reason": "invalid_arguments",
                    "message": "Tool arguments are not valid JSON.",
                }
            else:
                result = gateway.call_tool(
                    token,
                    tool_call.function.name,
                    arguments,
                )
            if (
                tool_call.function.name == "check_documents"
                and result["status"] == "allowed"
            ):
                return _format_document_check(json.dumps(result["data"]))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )
