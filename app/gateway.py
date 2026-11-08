
from typing import Any

from pydantic import ValidationError

from app import approvals, audit, db, identity
from app.tools_registry import TOOLS


def _result(
    status: str,
    reason: str,
    message: str,
    data: Any = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    result = {"status": status, "reason": reason, "message": message}
    if status == "allowed":
        result["data"] = data
    if approval_id is not None:
        result["approval_id"] = approval_id
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
    audit_decision: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    try:
        audit.append(
            {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "decision": audit_decision or status,
                "reason": reason,
            }
        )
    except Exception:
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed; the tool result was withheld.",
        )
    return _result(status, reason, message, data, approval_id)


def execute_registered_tool(tool_name: str, arguments: Any) -> dict[str, Any]:
    registration = TOOLS.get(tool_name)
    if registration is None or registration.handler is None:
        return _result("error", "tool_unavailable", "The registered action cannot run.")
    try:
        validated_arguments = registration.input_model.model_validate(arguments)
    except ValidationError:
        return _result(
            "denied",
            "invalid_arguments",
            "Stored approval arguments are invalid.",
        )
    try:
        data = registration.handler(**validated_arguments.model_dump(mode="json"))
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
        return _result("error", "tool_execution_failed", message)
    return _result("allowed", "allowed", f"Tool '{tool_name}' completed.", data)


def execute_approved_tool(tool_name: str, arguments: Any) -> dict[str, Any]:
    registration = TOOLS.get(tool_name)
    if registration is None or registration.risk_class != "supervised":
        return _result(
            "error",
            "tool_unavailable",
            "This approval is not for a supervised tool.",
        )
    return execute_registered_tool(tool_name, arguments)


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

    if registration.risk_class == "supervised":
        try:
            approval = approvals.create_pending(
                agent["id"],
                tool_name,
                validated_arguments.model_dump(mode="json"),
                "Supervised action requires officer approval.",
            )
        except Exception:
            return _finish(
                "error",
                "approval_creation_failed",
                "Could not create an approval request.",
                agent_id=agent["id"],
                tool_name=tool_name,
                arguments=arguments,
            )
        result = _finish(
            "pending",
            "pending_approval",
            "This action is waiting for an authorised officer.",
            agent_id=agent["id"],
            tool_name=tool_name,
            arguments=arguments,
            audit_decision="pending",
            approval_id=approval["id"],
        )
        if result["reason"] == "audit_failed":
            approvals.cancel_unlogged(approval["id"])
        return result

    if registration.risk_class == "decision":
        return _finish(
            "denied",
            "decision_requires_officer",
            "Only an authorised officer can make this decision.",
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

    result = execute_registered_tool(tool_name, validated_arguments.model_dump(mode="json"))
    if result["status"] != "allowed":
        return _finish(
            result["status"],
            result["reason"],
            result["message"],
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
        data=result["data"],
    )
