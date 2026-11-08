import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from app import audit, db, gateway, identity


@pytest.fixture
def audit_db(monkeypatch, tmp_path):
    database_path = tmp_path / "audit.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    return database_path


def append_entry(reason: str = "allowed") -> dict:
    return audit.append(
        {
            "agent_id": "reader",
            "tool_name": "get_application",
            "arguments": {"application_id": "APP-1001"},
            "decision": "allowed",
            "reason": reason,
        }
    )


def test_clean_chain_verifies(audit_db) -> None:
    append_entry()
    append_entry()

    assert audit.verify() == {
        "ok": True,
        "entries_checked": 2,
        "first_broken_id": None,
        "reason": None,
    }


def test_concurrent_appends_keep_a_single_chain(audit_db) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: append_entry(), range(8)))

    assert len(results) == 8
    assert audit.verify() == {
        "ok": True,
        "entries_checked": 8,
        "first_broken_id": None,
        "reason": None,
    }


def test_edited_row_is_reported_as_hash_mismatch(audit_db) -> None:
    first = append_entry()
    append_entry()
    with sqlite3.connect(audit_db) as connection:
        connection.execute(
            "UPDATE audit_log SET reason = ? WHERE id = ?",
            ("edited", first["id"]),
        )

    result = audit.verify()

    assert result["ok"] is False
    assert result["first_broken_id"] == first["id"]
    assert result["reason"] == "hash_mismatch"


def test_deleted_last_row_is_detected_by_chain_anchor(audit_db) -> None:
    append_entry()
    last = append_entry()
    with sqlite3.connect(audit_db) as connection:
        connection.execute("DELETE FROM audit_log WHERE id = ?", (last["id"],))

    result = audit.verify()

    assert result["ok"] is False
    assert result["first_broken_id"] == last["id"]
    assert result["reason"] == "previous_link_mismatch"


def test_reordered_rows_break_previous_link(audit_db) -> None:
    first = append_entry()
    second = append_entry()
    append_entry()
    with sqlite3.connect(audit_db) as connection:
        connection.execute(
            "UPDATE audit_log SET sequence = 100 WHERE id = ?",
            (first["id"],),
        )
        connection.execute(
            "UPDATE audit_log SET sequence = 1 WHERE id = ?",
            (second["id"],),
        )
        connection.execute(
            "UPDATE audit_log SET sequence = 2 WHERE id = ?",
            (first["id"],),
        )

    result = audit.verify()

    assert result["ok"] is False
    assert result["first_broken_id"] == second["id"]
    assert result["reason"] == "previous_link_mismatch"


def test_audit_sequence_is_auto_incrementing(audit_db) -> None:
    first = append_entry()
    second = append_entry()
    with sqlite3.connect(audit_db) as connection:
        rows = connection.execute(
            "SELECT sequence, id FROM audit_log ORDER BY sequence"
        ).fetchall()

    assert rows == [(1, first["id"]), (2, second["id"])]


def test_masking_preserves_application_id_and_redacts_personal_data(
    audit_db,
) -> None:
    audit.append(
        {
            "agent_id": "reader",
            "tool_name": "get_application",
            "arguments": {
                "application_id": "APP-1001",
                "contact_name": "Jean Marie",
                "email": "jean@example.invalid",
                "phone": "+250788123456",
                "national_id_number": "1234567890123456",
            },
            "decision": "denied",
            "reason": "invalid_arguments",
        }
    )

    args_masked = audit.list_recent()[0]["args_masked"]
    masked = json.loads(args_masked)

    assert masked["application_id"] == "APP-1001"
    assert masked["contact_name"] == "Je***"
    assert masked["email"] == "je***"
    assert masked["phone"] == "+2***"
    assert masked["national_id_number"] == "12***"

    audit.append(
        {
            "agent_id": "reader",
            "tool_name": "get_application",
            "arguments": {"note": "n" * 500},
            "decision": "denied",
            "reason": "invalid_arguments",
        }
    )
    truncated = audit.list_recent()[0]["args_masked"]
    assert len(truncated) <= 300
    assert len(audit.list_recent()[1]["args_masked"]) <= 300


def test_tokens_secrets_and_model_output_are_not_stored(audit_db) -> None:
    audit.append(
        {
            "agent_id": "reader",
            "tool_name": "get_application",
            "arguments": {
                "application_id": "APP-1001",
                "access_token": "header.payload.signature",
                "client_secret": "fake-secret-value",
                "model_output": "private model text",
            },
            "decision": "denied",
            "reason": "invalid_arguments",
            "model_used": "private model text",
        }
    )
    with sqlite3.connect(audit_db) as connection:
        stored = " ".join(str(value) for value in connection.execute(
            "SELECT * FROM audit_log"
        ).fetchone())

    assert "header.payload.signature" not in stored
    assert "fake-secret-value" not in stored
    assert "private model text" not in stored


def test_unparsed_model_tool_arguments_are_not_stored(audit_db) -> None:
    audit.append(
        {
            "agent_id": None,
            "tool_name": "get_application",
            "arguments": "unparsed model output containing confidential material",
            "decision": "denied",
            "reason": "invalid_arguments",
        }
    )

    stored_args = audit.list_recent()[0]["args_masked"]

    assert "confidential material" not in stored_args
    assert json.loads(stored_args) == {"unparsed_arguments": True}


def test_invalid_token_gateway_call_is_logged_with_null_agent(audit_db) -> None:
    result = gateway.call_tool("invalid-token", "get_application", {})

    assert result["status"] == "denied"
    entry = audit.list_recent()[0]
    assert entry["decision"] == "denied"
    assert entry["agent_id"] is None


def test_each_gateway_outcome_is_logged(audit_db, monkeypatch) -> None:
    identity.seed_demo_agents()
    reader_token = identity.issue_token("reader")
    expired_token = identity.issue_token(
        "drafter",
        expires_in=timedelta(seconds=-1),
    )
    suspended_token = identity.issue_token("flagger")
    identity.suspend_agent("flagger")

    handler = Mock(return_value={"application_id": "APP-1001"})
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )

    outcomes = [
        gateway.call_tool(reader_token, "get_application", {"application_id": "APP-1001"}),
        gateway.call_tool(None, "get_application", {}),
        gateway.call_tool(expired_token, "get_application", {"application_id": "APP-1001"}),
        gateway.call_tool(suspended_token, "get_application", {"application_id": "APP-1001"}),
        gateway.call_tool(reader_token, "unknown_tool", {}),
        gateway.call_tool(reader_token, "approve_application", {"application_id": "APP-1001"}),
        gateway.call_tool(reader_token, "get_application", {"application_id": ""}),
    ]
    handler.side_effect = RuntimeError("private exception details")
    outcomes.append(
        gateway.call_tool(
            reader_token,
            "get_application",
            {"application_id": "APP-1001"},
        )
    )

    assert [outcome["reason"] for outcome in outcomes] == [
        "allowed",
        "invalid_token",
        "expired_token",
        "suspended_agent",
        "unknown_tool",
        "permission_denied",
        "invalid_arguments",
        "tool_execution_failed",
    ]
    entries = audit.list_recent(limit=len(outcomes))
    assert audit.verify()["entries_checked"] == len(outcomes)
    assert entries[6]["agent_id"] is None
    assert entries[4]["agent_id"] == "flagger"


def test_audit_failure_withholds_allowed_data(audit_db, monkeypatch) -> None:
    identity.seed_demo_agents()
    token = identity.issue_token("reader")
    handler = Mock(return_value={"application_id": "APP-1001"})
    registration = replace(gateway.TOOLS["get_application"], handler=handler)
    monkeypatch.setattr(
        gateway,
        "TOOLS",
        {**gateway.TOOLS, "get_application": registration},
    )
    monkeypatch.setattr(
        gateway.audit,
        "append",
        Mock(side_effect=sqlite3.OperationalError("private database detail")),
    )

    result = gateway.call_tool(
        token,
        "get_application",
        {"application_id": "APP-1001"},
    )

    assert result == {
        "status": "error",
        "reason": "audit_failed",
        "message": "Audit log write failed; the tool result was withheld.",
    }
    assert "data" not in result
    handler.assert_called_once()


def test_audit_module_exposes_no_update_or_delete_function() -> None:
    assert not hasattr(audit, "update")
    assert not hasattr(audit, "delete")
    assert not hasattr(audit, "update_entry")
    assert not hasattr(audit, "delete_entry")
