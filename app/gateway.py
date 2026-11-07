
from typing import Any

from pydantic import ValidationError

from app import db, identity
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
        return _result("denied", "invalid_token", "A valid agent token is required.")

    try:
        agent = identity.verify_token(token)
    except identity.TokenVerificationError as error:
        return _result("denied", error.reason, str(error))

    registration = TOOLS.get(tool_name)
    if registration is None:
        return _result("error", "unknown_tool", f"Tool '{tool_name}' is not registered.")

    if not db.has_permission(agent["id"], tool_name):
        return _result(
            "denied",
            "permission_denied",
            f"Agent '{agent['id']}' is not permitted to use '{tool_name}'.",
        )

    try:
        validated_arguments = registration.input_model.model_validate(arguments)
    except ValidationError as error:
        return _result(
            "denied",
            "invalid_arguments",
            f"Arguments for '{tool_name}' are invalid: {error}",
        )

    if registration.handler is None:
        return _result(
            "error",
            "tool_unavailable",
            f"Tool '{tool_name}' is registered but has no executable handler.",
        )

    try:
        data = registration.handler(**validated_arguments.model_dump())
    except Exception:
        return _result(
            "error",
            "tool_execution_failed",
            f"Tool '{tool_name}' failed during execution.",
        )

    if isinstance(data, dict) and "error" in data:
        message = (
            "The downstream service is unavailable."
            if data.get("status_code") == 503
            else str(data["error"])
        )
        return _result(
            "error",
            "tool_execution_failed",
            message,
        )

    return _result("allowed", "allowed", f"Tool '{tool_name}' completed.", data)
