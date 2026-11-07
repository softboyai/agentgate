from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from app import db, gateway, identity


@pytest.fixture
def isolated_gateway(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agentgate.db")
    db.init_db()
    identity.seed_demo_agents()
    return identity.issue_token("reader")


def test_allowed_tool_call_executes_and_returns_data(
    isolated_gateway, monkeypatch
) -> None:
    handler = Mock(return_value={"application_id": "APP-1001"})
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "APP-1001"},
    )

    assert result == {
        "status": "allowed",
        "reason": "allowed",
        "message": "Tool 'get_application' completed.",
        "data": {"application_id": "APP-1001"},
    }
    handler.assert_called_once_with(application_id="APP-1001")


def test_handler_exception_returns_short_error_without_exception_details(
    isolated_gateway, monkeypatch
) -> None:
    handler = Mock(side_effect=RuntimeError("secret-value"))
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "APP-1001"},
    )

    assert result == {
        "status": "error",
        "reason": "tool_execution_failed",
        "message": "Tool 'get_application' failed during execution.",
    }
    assert "secret-value" not in result["message"]


def test_unavailable_service_returns_sanitized_message(isolated_gateway, monkeypatch) -> None:
    handler = Mock(
        return_value={
            "error": "Service unavailable: connection details",
            "status_code": 503,
        }
    )
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "APP-1003"},
    )

    assert result["status"] == "error"
    assert result["reason"] == "tool_execution_failed"
    assert result["message"] == "The downstream service is unavailable."


@pytest.mark.parametrize(
    ("token", "reason"),
    [(None, "invalid_token"), ("not-a-jwt", "altered_token")],
)
def test_missing_or_garbage_token_is_denied(
    isolated_gateway, token, reason
) -> None:
    result = gateway.call_tool(token, "get_application", {"application_id": "APP-1001"})

    assert result["status"] == "denied"
    assert result["reason"] == reason


def test_expired_token_is_denied(isolated_gateway) -> None:
    token = identity.issue_token("reader", expires_in=timedelta(seconds=-1))

    result = gateway.call_tool(token, "get_application", {"application_id": "APP-1001"})

    assert result["status"] == "denied"
    assert result["reason"] == "expired_token"


def test_suspended_agent_is_denied(isolated_gateway) -> None:
    identity.suspend_agent("reader")

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "APP-1001"},
    )

    assert result["status"] == "denied"
    assert result["reason"] == "suspended_agent"


def test_agent_without_permission_is_denied_before_argument_validation(
    isolated_gateway,
) -> None:
    with db.get_connection() as connection:
        connection.execute(
            "DELETE FROM permissions WHERE agent_id = ? AND tool_name = ?",
            ("reader", "get_application"),
        )

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": ""},
    )

    assert result["status"] == "denied"
    assert result["reason"] == "permission_denied"


def test_unknown_tool_returns_error(isolated_gateway) -> None:
    result = gateway.call_tool(isolated_gateway, "not_a_tool", {})

    assert result["status"] == "error"
    assert result["reason"] == "unknown_tool"


def test_invalid_arguments_are_rejected_before_execution(
    isolated_gateway, monkeypatch
) -> None:
    handler = Mock()
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )

    result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "", "unexpected": True},
    )

    assert result["status"] == "denied"
    assert result["reason"] == "invalid_arguments"
    handler.assert_not_called()


@pytest.mark.parametrize("agent_id", ["reader", "drafter", "flagger"])
def test_approve_application_is_denied_for_every_demo_agent(
    isolated_gateway, agent_id
) -> None:
    token = identity.issue_token(agent_id)

    result = gateway.call_tool(
        token,
        "approve_application",
        {"application_id": "APP-1001"},
    )

    assert result["status"] == "denied"
    assert result["reason"] == "permission_denied"


def test_failed_checks_never_call_a_tool_handler(isolated_gateway, monkeypatch) -> None:
    handler = Mock()
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )
    with db.get_connection() as connection:
        connection.execute(
            "DELETE FROM permissions WHERE agent_id = ? AND tool_name = ?",
            ("reader", "get_application"),
        )

    for token, name, arguments in [
        ("garbage", "get_application", {"application_id": "APP-1001"}),
        (isolated_gateway, "get_application", {"application_id": ""}),
        (isolated_gateway, "get_application", {"application_id": "APP-1001"}),
        (isolated_gateway, "unknown", {}),
    ]:
        assert gateway.call_tool(token, name, arguments)["status"] != "allowed"

    handler.assert_not_called()


def test_suspended_and_expired_agents_never_call_a_handler(
    isolated_gateway, monkeypatch
) -> None:
    handler = Mock()
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )
    expired_token = identity.issue_token("drafter", expires_in=timedelta(seconds=-1))
    identity.suspend_agent("reader")

    suspended_result = gateway.call_tool(
        isolated_gateway,
        "get_application",
        {"application_id": "APP-1001"},
    )
    expired_result = gateway.call_tool(
        expired_token,
        "get_application",
        {"application_id": "APP-1001"},
    )

    assert suspended_result["reason"] == "suspended_agent"
    assert expired_result["reason"] == "expired_token"
    handler.assert_not_called()


def test_database_initialization_does_not_restore_removed_permission(
    isolated_gateway,
) -> None:
    with db.get_connection() as connection:
        connection.execute(
            "DELETE FROM permissions WHERE agent_id = ? AND tool_name = ?",
            ("reader", "get_application"),
        )

    db.init_db()

    assert not db.has_permission("reader", "get_application")
