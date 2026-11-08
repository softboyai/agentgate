import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import db

GENESIS_HASH = db.AUDIT_GENESIS_HASH
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\bBearer\s+\S+|"
    r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b|"
    r"\b(?:secret|token|password|api[_-]?key)\s*[:=]\s*\S+"
)
_PERSONAL_KEYS = ("name", "email", "phone", "idnumber", "nationalid", "identitynumber")
_SENSITIVE_KEYS = {
    "token",
    "accesstoken",
    "refreshtoken",
    "jwt",
    "secret",
    "password",
    "authorization",
    "apikey",
    "modeloutput",
    "modelreply",
    "assistantresponse",
    "response",
    "output",
    "completion",
    "messages",
    "prompt",
    "content",
    "message",
    "notice",
}


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _clean_text(value: str, limit: int = 300) -> str:
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", value)[:limit]


def _mask_value(value: Any, key: str = "") -> Any:
    normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
    if normalized_key in _SENSITIVE_KEYS or any(
        sensitive_part in normalized_key
        for sensitive_part in ("token", "secret", "password", "apikey", "authorization")
    ):
        return "[REDACTED]"
    if normalized_key != "applicationid" and any(
        personal_key in normalized_key for personal_key in _PERSONAL_KEYS
    ):
        if value is None:
            return None
        text = str(value)
        return f"{_clean_text(text, 2)}***"
    if isinstance(value, dict):
        return {
            str(child_key)[:300]: _mask_value(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_mask_value(item) for item in value]
    if isinstance(value, str):
        return _clean_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported]"


def _masked_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return _canonical_json({"unparsed_arguments": True})

    masked = _mask_value(arguments)
    serialized = _canonical_json(masked if isinstance(masked, dict) else {"value": masked})
    if len(serialized) > 300:
        return _canonical_json({"truncated": True})
    return serialized


def _entry_hash(fields: dict[str, Any]) -> str:
    previous_hash = fields["prev_hash"]
    payload = _canonical_json(fields)
    return hashlib.sha256(previous_hash.encode("utf-8") + payload.encode("utf-8")).hexdigest()


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path or db.DB_PATH, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def append(entry: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    db.init_db(db_path)
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT last_hash, entry_count FROM audit_chain_state WHERE id = 1"
        ).fetchone()
        if state is None:
            raise RuntimeError("Audit chain state is missing.")

        fields = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": entry.get("agent_id"),
            "tool_name": _clean_text(str(entry.get("tool_name") or "unknown")),
            "args_masked": _masked_arguments(entry.get("arguments")),
            "decision": entry["decision"],
            "reason": _clean_text(str(entry.get("reason") or "")),
            "model_used": None,
            "prev_hash": state["last_hash"],
        }
        if fields["decision"] not in {
            "allowed",
            "denied",
            "error",
            "pending",
            "executed_after_approval",
            "rejected",
            "approval_expired",
        }:
            raise ValueError("Audit decision is not supported.")
        if fields["agent_id"] is not None:
            fields["agent_id"] = str(fields["agent_id"])[:300]

        entry_hash = _entry_hash(fields)
        connection.execute(
            """
            INSERT INTO audit_log
                (id, timestamp, agent_id, tool_name, args_masked, decision,
                 reason, model_used, prev_hash, entry_hash)
            VALUES
                (:id, :timestamp, :agent_id, :tool_name, :args_masked, :decision,
                 :reason, :model_used, :prev_hash, :entry_hash)
            """,
            {**fields, "entry_hash": entry_hash},
        )
        connection.execute(
            """
            UPDATE audit_chain_state
            SET last_hash = ?, entry_count = entry_count + 1, last_entry_id = ?
            WHERE id = 1
            """,
            (entry_hash, fields["id"]),
        )
        connection.commit()
        return {**fields, "entry_hash": entry_hash}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify(db_path: Path | None = None) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        try:
            rows = connection.execute(
                "SELECT sequence, id, timestamp, agent_id, tool_name, args_masked, decision, "
                "reason, model_used, prev_hash, entry_hash "
                "FROM audit_log ORDER BY sequence"
            ).fetchall()
            state = connection.execute(
                "SELECT last_hash, entry_count, last_entry_id "
                "FROM audit_chain_state WHERE id = 1"
            ).fetchone()
        except sqlite3.Error:
            return _broken(0, None, "previous_link_mismatch")
        if state is None:
            return _broken(0, None, "previous_link_mismatch")

        previous_hash = GENESIS_HASH
        for checked, row in enumerate(rows, start=1):
            fields = dict(row)
            fields.pop("sequence")
            entry_id = fields["id"]
            if fields["prev_hash"] != previous_hash:
                return _broken(checked - 1, entry_id, "previous_link_mismatch")
            stored_hash = fields.pop("entry_hash")
            if _entry_hash(fields) != stored_hash:
                return _broken(checked - 1, entry_id, "hash_mismatch")
            previous_hash = stored_hash

        if len(rows) != state["entry_count"] or previous_hash != state["last_hash"]:
            missing_id = state["last_entry_id"]
            if missing_id and not any(row["id"] == missing_id for row in rows):
                return _broken(len(rows), missing_id, "previous_link_mismatch")
            broken_id = rows[-1]["id"] if rows else state["last_entry_id"]
            return _broken(len(rows), broken_id, "previous_link_mismatch")
        return {
            "ok": True,
            "entries_checked": len(rows),
            "first_broken_id": None,
            "reason": None,
        }
    finally:
        connection.close()


def list_recent(limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("Audit entry limit must be positive.")
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT sequence, id, timestamp, agent_id, tool_name, args_masked, decision, "
            "reason, model_used, prev_hash, entry_hash "
            "FROM audit_log ORDER BY sequence DESC LIMIT ?",
            (limit,),
        ).fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            entry.pop("sequence")
            entries.append(entry)
        return entries
    finally:
        connection.close()


def _broken(
    entries_checked: int,
    broken_id: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "entries_checked": entries_checked,
        "first_broken_id": broken_id,
        "reason": reason,
    }
