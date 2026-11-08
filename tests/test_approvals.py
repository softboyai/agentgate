import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app import approvals, audit, db, gateway, identity


@pytest.fixture
def drafter_token():
    db.init_db()
    identity.seed_demo_agents()
    return identity.issue_token("drafter")


def register_send_handler(monkeypatch, handler: Mock) -> None:
    registration = replace(gateway.TOOLS["send_return_notice"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "send_return_notice": registration},
    )


def request_send(token: str) -> dict:
    return gateway.call_tool(
        token,
        "send_return_notice",
        {"draft_id": "draft-demo-1"},
    )


def test_supervised_call_creates_pending_without_sending(drafter_token, monkeypatch) -> None:
    handler = Mock(return_value={"draft_id": "draft-demo-1", "sent": True})
    register_send_handler(monkeypatch, handler)

    result = request_send(drafter_token)

    assert result["status"] == "pending"
    assert result["reason"] == "pending_approval"
    assert result["approval_id"]
    assert "data" not in result
    handler.assert_not_called()
    pending = approvals.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_pending_request_has_pending_audit_entry(drafter_token, monkeypatch) -> None:
    register_send_handler(monkeypatch, Mock())

    result = request_send(drafter_token)
    entry = audit.list_recent()[0]

    assert entry["decision"] == "pending"
    assert entry["agent_id"] == "drafter"
    assert entry["tool_name"] == "send_return_notice"
    assert result["approval_id"]
    assert audit.verify()["ok"] is True


def test_approve_executes_once_and_records_officer(drafter_token, monkeypatch) -> None:
    handler = Mock(return_value={"draft_id": "draft-demo-1", "sent": True})
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]

    result = approvals.approve(approval_id, "officer-1", "Reviewed and approved")
    repeated = approvals.approve(approval_id, "officer-1")

    assert result["status"] == "allowed"
    assert result["reason"] == "executed_after_approval"
    assert result["data"]["sent"] is True
    assert repeated["status"] == "denied"
    assert repeated["reason"] == "approval_already_used"
    handler.assert_called_once_with(draft_id="draft-demo-1")
    entry = audit.list_recent()[0]
    assert entry["decision"] == "denied"
    executed_entry = audit.list_recent()[1]
    assert executed_entry["decision"] == "executed_after_approval"
    assert "officer-1" in executed_entry["reason"]


def test_reject_never_executes_handler(drafter_token, monkeypatch) -> None:
    handler = Mock()
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]

    result = approvals.reject(approval_id, "officer-2", "Not appropriate")

    assert result["reason"] == "rejected"
    handler.assert_not_called()
    assert audit.list_recent()[0]["decision"] == "rejected"


def test_agent_token_cannot_decide_approval(drafter_token, monkeypatch) -> None:
    register_send_handler(monkeypatch, Mock())
    approval_id = request_send(drafter_token)["approval_id"]

    result = approvals.approve(approval_id, drafter_token)

    assert result["status"] == "denied"
    assert result["reason"] == "invalid_officer"
    assert approvals.list_pending()[0]["id"] == approval_id


def test_agent_cannot_approve_own_request(drafter_token, monkeypatch) -> None:
    register_send_handler(monkeypatch, Mock())
    approval_id = request_send(drafter_token)["approval_id"]

    result = approvals.approve(approval_id, "drafter")

    assert result["status"] == "denied"
    assert result["reason"] == "self_approval_forbidden"


def test_changed_arguments_fail_hash_check(drafter_token, monkeypatch) -> None:
    handler = Mock()
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]
    changed = json.dumps({"draft_id": "draft-tampered"}, separators=(",", ":"))
    with db.get_connection() as connection:
        connection.execute(
            "UPDATE approvals SET arguments = ? WHERE id = ?",
            (changed, approval_id),
        )

    result = approvals.approve(approval_id, "officer-1")

    assert result["status"] == "denied"
    assert result["reason"] == "arguments_hash_mismatch"
    handler.assert_not_called()


def test_expired_approval_cannot_run(drafter_token, monkeypatch) -> None:
    handler = Mock()
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with db.get_connection() as connection:
        connection.execute(
            "UPDATE approvals SET expires_at = ? WHERE id = ?",
            (past, approval_id),
        )

    result = approvals.approve(approval_id, "officer-1")

    assert result["reason"] == "approval_expired"
    handler.assert_not_called()
    assert audit.list_recent()[0]["decision"] == "approval_expired"


def test_suspended_agent_approval_does_not_run(drafter_token, monkeypatch) -> None:
    handler = Mock()
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]
    identity.suspend_agent("drafter")

    result = approvals.approve(approval_id, "officer-1")

    assert result["status"] == "denied"
    assert result["reason"] == "agent_unavailable"
    handler.assert_not_called()


def test_removed_permission_blocks_approved_request(drafter_token, monkeypatch) -> None:
    handler = Mock()
    register_send_handler(monkeypatch, handler)
    approval_id = request_send(drafter_token)["approval_id"]
    with db.get_connection() as connection:
        connection.execute(
            "DELETE FROM permissions WHERE agent_id = ? AND tool_name = ?",
            ("drafter", "send_return_notice"),
        )

    result = approvals.approve(approval_id, "officer-1")

    assert result["status"] == "denied"
    assert result["reason"] == "permission_denied"
    handler.assert_not_called()


def test_audit_chain_verifies_after_approval_outcomes(drafter_token, monkeypatch) -> None:
    handler = Mock(return_value={"sent": True})
    register_send_handler(monkeypatch, handler)
    approved_id = request_send(drafter_token)["approval_id"]
    rejected_id = request_send(drafter_token)["approval_id"]

    approvals.approve(approved_id, "officer-1")
    approvals.reject(rejected_id, "officer-2")

    result = audit.verify()
    assert result["ok"] is True
    assert result["entries_checked"] == 4


def test_all_tools_have_one_of_the_four_risk_classes() -> None:
    assert {
        registration.risk_class for registration in gateway.TOOLS.values()
    } <= {"assistive", "supervised", "proactive", "decision"}
    assert gateway.TOOLS["approve_application"].risk_class == "decision"
    assert gateway.TOOLS["approve_application"].handler is None
