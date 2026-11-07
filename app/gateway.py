
from typing import Any

from pydantic import ValidationError

from app import audit, db, identity
from app.tools_registry import TOOLS


def _result(
    status: str,
    reason: str,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    result = {"status": status, "reason": reason, "message": message}
    if status == "allowed":
        result["data"] = data
    return result


def _finish(
    status: str,
    reason: str,
    message: str,
    *,
    agent_id: str | None,
    tool_name: str,
    arguments: Any,
    data: Any = None,
) -> dict[str, Any]:
    try:
        audit.append(
            {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "decision": status,
                "reason": reason,
            }
        )
    except Exception:
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed; the tool result was withheld.",
        )
    return _result(status, reason, message, data)


def get_permitted_tool_definitions(token: str) -> list[dict[str, Any]]:
    agent = identity.verify_token(token)
    permitted = db.get_agent_permissions(agent["id"])
    return [
        TOOLS[name].model_definition()
        for name in TOOLS
        if name in permitted
    ]


def call_tool(
    token: str,
    tool_name: str,
    arguments: Any,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token:
        return _finish(
            "denied",
            "invalid_token",
            "A valid agent token is required.",
            agent_id=None,
            tool_name=tool_name,
            arguments=arguments,
        )

    try:
        agent = identity.verify_token(token)
    except identity.TokenVerificationError as error:
        return _finish(
            "denied",
            error.reason,
            str(error),
            agent_id=error.agent_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    registration = TOOLS.get(tool_name)
    if registration is None:
        return _finish(
            "error",
            "unknown_tool",
            f"Tool '{tool_name}' is not registered.",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    if not db.has_permission(agent["id"], tool_name):
        return _finish(
            "denied",
            "permission_denied",
            f"Agent '{agent['id']}' is not permitted to use '{tool_name}'.",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    try:
        validated_arguments = registration.input_model.model_validate(arguments)
    except ValidationError as error:
        return _finish(
            "denied",
            "invalid_arguments",
            f"Arguments for '{tool_name}' are invalid: {error}",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    if registration.handler is None:
        return _finish(
            "error",
            "tool_unavailable",
            f"Tool '{tool_name}' is registered but has no executable handler.",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    try:
        data = registration.handler(**validated_arguments.model_dump())
    except Exception:
        return _finish(
            "error",
            "tool_execution_failed",
            f"Tool '{tool_name}' failed during execution.",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    if isinstance(data, dict) and "error" in data:
        message = (
            "The downstream service is unavailable."
            if data.get("status_code") == 503
            else str(data["error"])
        )
        return _finish(
            "error",
            "tool_execution_failed",
            message,
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
        )

    return _finish(
        "allowed",
        "allowed",
        f"Tool '{tool_name}' completed.",
        agent_id=agent["id"],
        tool_name=tool_name,
        arguments=arguments,
        data=data,
    )
