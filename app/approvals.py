import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app import audit, db
from app.config import APPROVAL_TTL_MINUTES


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _canonical_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _arguments_hash(arguments_json: str) -> str:
    return hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()


def _result(
    status: str,
    reason: str,
    message: str,
    *,
    approval_id: str | None = None,
    data: Any = None,
) -> dict[str, Any]:
    result = {"status": status, "reason": reason, "message": message}
    if approval_id is not None:
        result["approval_id"] = approval_id
    if status == "allowed":
        result["data"] = data
    return result


def _audit(entry: dict[str, Any]) -> bool:
    try:
        audit.append(entry)
    except Exception:
        return False
    return True


def create_pending(
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    requested_at = _utc_now()
    arguments_json = _canonical_arguments(arguments)
    approval = {
        "id": str(uuid4()),
        "agent_id": agent_id,
        "tool_name": tool_name,
        "arguments": arguments_json,
        "arguments_hash": _arguments_hash(arguments_json),
        "reason": reason,
        "status": "pending",
        "requested_at": _timestamp(requested_at),
        "expires_at": _timestamp(
            requested_at + timedelta(minutes=APPROVAL_TTL_MINUTES)
        ),
        "decided_by": None,
        "decided_at": None,
        "decision_note": None,
    }
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO approvals
                (id, agent_id, tool_name, arguments, arguments_hash, reason, status,
                 requested_at, expires_at, decided_by, decided_at, decision_note)
            VALUES
                (:id, :agent_id, :tool_name, :arguments, :arguments_hash, :reason,
                 :status, :requested_at, :expires_at, :decided_by, :decided_at,
                 :decision_note)
            """,
            approval,
        )
    return approval


def cancel_unlogged(approval_id: str) -> None:
    with db.get_connection() as connection:
        connection.execute(
            """
            UPDATE approvals
            SET status = 'rejected', decision_note = 'Audit write failed'
            WHERE id = ? AND status = 'pending'
            """,
            (approval_id,),
        )


def list_pending() -> list[dict[str, Any]]:
    now = _timestamp(_utc_now())
    with db.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, agent_id, tool_name, arguments, arguments_hash, reason,
                   status, requested_at, expires_at, decided_by, decided_at,
                   decision_note
            FROM approvals
            WHERE status = 'pending' AND expires_at > ?
            ORDER BY requested_at
            """,
            (now,),
        ).fetchall()
    return [dict(row) for row in rows]


def _approval_audit(
    approval: sqlite3.Row,
    decision: str,
    reason: str,
    officer_id: str | None = None,
) -> bool:
    return _audit(
        {
            "agent_id": approval["agent_id"],
            "tool_name": approval["tool_name"],
            "arguments": approval["arguments"],
            "decision": decision,
            "reason": f"{reason}; officer={officer_id}" if officer_id else reason,
        }
    )


def _registered_officer(connection: sqlite3.Connection, officer_id: str) -> bool:
    if not isinstance(officer_id, str) or not officer_id or "." in officer_id:
        return False
    return connection.execute(
        "SELECT 1 FROM officers WHERE id = ?",
        (officer_id,),
    ).fetchone() is not None


def _deny(
    reason: str,
    message: str,
    approval: sqlite3.Row | None = None,
    officer_id: str | None = None,
) -> dict[str, Any]:
    if approval is not None:
        recorded = _approval_audit(approval, "denied", reason, officer_id)
    else:
        recorded = _audit(
            {
                "agent_id": None,
                "tool_name": "approval",
                "arguments": {},
                "decision": "denied",
                "reason": reason,
            }
        )
    if not recorded:
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed while recording the approval decision.",
        )
    return _result("denied", reason, message)


def _expired(approval: sqlite3.Row, officer_id: str) -> dict[str, Any]:
    now = _timestamp(_utc_now())
    with db.get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE approvals
            SET status = 'expired', decided_by = ?, decided_at = ?,
                decision_note = 'Approval expired before decision'
            WHERE id = ? AND status = 'pending'
            """,
            (officer_id, now, approval["id"]),
        )
    if not _approval_audit(approval, "approval_expired", "Approval expired", officer_id):
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed; the approval was not executed.",
            approval_id=approval["id"],
        )
    return _result(
        "error",
        "approval_expired",
        "This approval has expired and cannot be executed.",
        approval_id=approval["id"],
    )


def _read_approval(approval_id: str) -> sqlite3.Row | None:
    with db.get_connection() as connection:
        return connection.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()


def _consume_for_approval(
    approval_id: str,
    officer_id: str,
    note: str | None,
) -> tuple[sqlite3.Row | None, dict[str, Any] | None]:
    now = _utc_now()
    failure: tuple[str, str, sqlite3.Row | None] | None = None
    expired = False
    approval: sqlite3.Row | None = None
    with db.get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        approval = connection.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if approval is None:
            failure = ("approval_not_found", "Approval was not found.", None)
        elif approval["agent_id"] == officer_id:
            failure = (
                "self_approval_forbidden",
                "An agent cannot approve its own request.",
                approval,
            )
        elif not _registered_officer(connection, officer_id):
            failure = (
                "invalid_officer",
                "Only a registered demo officer may decide an approval.",
                approval,
            )
        elif approval["status"] != "pending":
            failure = (
                "approval_already_used",
                f"Approval is already {approval['status']} and cannot run again.",
                approval,
            )
        elif datetime.fromisoformat(approval["expires_at"]) <= now:
            connection.execute(
                """
                UPDATE approvals
                SET status = 'expired', decided_by = ?, decided_at = ?,
                    decision_note = 'Approval expired before decision'
                WHERE id = ? AND status = 'pending'
                """,
                (officer_id, _timestamp(now), approval_id),
            )
            expired = True
        elif _arguments_hash(approval["arguments"]) != approval["arguments_hash"]:
            connection.execute(
                """
                UPDATE approvals
                SET status = 'rejected', decided_by = ?, decided_at = ?,
                    decision_note = 'Stored arguments hash did not match'
                WHERE id = ? AND status = 'pending'
                """,
                (officer_id, _timestamp(now), approval_id),
            )
            failure = (
                "arguments_hash_mismatch",
                "Stored approval arguments have changed; the request was refused.",
                approval,
            )

        if failure is None and not expired and approval is not None:
            agent = connection.execute(
                "SELECT status, expires_at FROM agents WHERE id = ?",
                (approval["agent_id"],),
            ).fetchone()
            if agent is None or agent["status"] != "active":
                failure = (
                    "agent_unavailable",
                    "The requesting agent is not active; the approval cannot run.",
                    approval,
                )
            elif agent["expires_at"]:
                agent_expiry = datetime.fromisoformat(agent["expires_at"])
                if agent_expiry.tzinfo is None:
                    agent_expiry = agent_expiry.replace(tzinfo=timezone.utc)
                if agent_expiry <= now:
                    failure = (
                        "agent_unavailable",
                        "The requesting agent has expired; the approval cannot run.",
                        approval,
                    )

        if failure is None and not expired and approval is not None:
            permission = connection.execute(
                "SELECT 1 FROM permissions WHERE agent_id = ? AND tool_name = ?",
                (approval["agent_id"], approval["tool_name"]),
            ).fetchone()
            if permission is None:
                failure = (
                    "permission_denied",
                    "The requesting agent no longer has permission for this action.",
                    approval,
                )

        if failure is None and not expired and approval is not None:
            connection.execute(
                """
                UPDATE approvals
                SET status = 'approved', decided_by = ?, decided_at = ?,
                    decision_note = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    officer_id,
                    _timestamp(now),
                    (note or "Approved by demo officer")[:300],
                    approval_id,
                ),
            )

    if failure is not None:
        reason, message, failed_approval = failure
        return None, _deny(reason, message, failed_approval, officer_id)
    if expired and approval is not None:
        return approval, _expired(approval, officer_id)
    return approval, None


def approve(
    approval_id: str,
    officer_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    approval, failure = _consume_for_approval(approval_id, officer_id, note)
    if failure is not None:
        return failure
    if approval is None:
        return _result("error", "approval_not_found", "Approval was not found.")
    try:
        arguments = json.loads(approval["arguments"])
        from app import gateway

        result = gateway.execute_approved_tool(approval["tool_name"], arguments)
    except Exception:
        result = _result(
            "error",
            "tool_execution_failed",
            "The approved action failed during execution.",
        )

    if result["status"] != "allowed":
        if not _approval_audit(
            approval,
            "error",
            result["reason"],
            officer_id,
        ):
            return _result(
                "error",
                "audit_failed",
                "Audit log write failed; the tool result was withheld.",
                approval_id=approval_id,
            )
        return result

    if not _approval_audit(
        approval,
        "executed_after_approval",
        f"Executed after officer approval; officer={officer_id}",
    ):
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed; the tool result was withheld.",
            approval_id=approval_id,
        )
    return _result(
        "allowed",
        "executed_after_approval",
        "Approved action executed.",
        approval_id=approval_id,
        data=result["data"],
    )


def reject(
    approval_id: str,
    officer_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    now = _timestamp(_utc_now())
    failure: tuple[str, str, sqlite3.Row | None] | None = None
    expired = False
    approval: sqlite3.Row | None = None
    with db.get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        approval = connection.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if approval is None:
            failure = ("approval_not_found", "Approval was not found.", None)
        elif approval["agent_id"] == officer_id:
            failure = (
                "self_approval_forbidden",
                "An agent cannot reject its own request.",
                approval,
            )
        elif not _registered_officer(connection, officer_id):
            failure = (
                "invalid_officer",
                "Only a registered demo officer may decide an approval.",
                approval,
            )
        elif approval["status"] != "pending":
            failure = (
                "approval_already_used",
                f"Approval is already {approval['status']} and cannot be decided again.",
                approval,
            )
        elif datetime.fromisoformat(approval["expires_at"]) <= _utc_now():
            expired = True
        else:
            connection.execute(
            """
            UPDATE approvals
            SET status = 'rejected', decided_by = ?, decided_at = ?,
                decision_note = ?
            WHERE id = ? AND status = 'pending'
            """,
            (officer_id, now, (note or "Rejected by demo officer")[:300], approval_id),
            )

    if failure is not None:
        reason, message, failed_approval = failure
        return _deny(reason, message, failed_approval, officer_id)
    if approval is None:
        return _result("error", "approval_not_found", "Approval was not found.")
    if expired:
        return _expired(approval, officer_id)

    if not _approval_audit(
        approval,
        "rejected",
        "Approval rejected",
        officer_id,
    ):
        return _result(
            "error",
            "audit_failed",
            "Audit log write failed while recording rejection.",
            approval_id=approval_id,
        )
    return _result(
        "denied",
        "rejected",
        "The officer rejected this action.",
        approval_id=approval_id,
    )
