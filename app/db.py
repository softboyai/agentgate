import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "agentgate.db"
AUDIT_GENESIS_HASH = "agentgate-audit-genesis-v1"


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        permissions_table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'permissions'"
        ).fetchone() is not None
        approvals_table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'approvals'"
        ).fetchone() is not None
        officers_table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'officers'"
        ).fetchone() is not None
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner TEXT NOT NULL,
                purpose TEXT NOT NULL,
                represents TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended')),
                created_at TEXT NOT NULL,
                expires_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS permissions (
                agent_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                PRIMARY KEY (agent_id, tool_name)
            )
            """
        )
        _ensure_audit_log_schema(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_chain_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_hash TEXT NOT NULL,
                entry_count INTEGER NOT NULL,
                last_entry_id TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO audit_chain_state
                (id, last_hash, entry_count, last_entry_id)
            VALUES (1, ?, 0, NULL)
            """,
            (AUDIT_GENESIS_HASH,),
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                requested_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_by TEXT,
                decided_at TEXT,
                decision_note TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS officers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        if not officers_table_exists:
            connection.executemany(
                "INSERT INTO officers (id, name) VALUES (?, ?)",
                [
                    ("officer-1", "Demo Officer One"),
                    ("officer-2", "Demo Officer Two"),
                ],
            )
        if not permissions_table_exists:
            connection.executemany(
                "INSERT INTO permissions (agent_id, tool_name) VALUES (?, ?)",
                [
                    ("reader", "get_application"),
                    ("reader", "check_documents"),
                    ("reader", "list_returned_files"),
                    ("drafter", "get_application"),
                    ("drafter", "check_documents"),
                    ("flagger", "get_application"),
                    ("flagger", "list_returned_files"),
                ],
            )
        if not approvals_table_exists:
            connection.executemany(
                "INSERT OR IGNORE INTO permissions (agent_id, tool_name) VALUES (?, ?)",
                [
                    ("drafter", "draft_return_notice"),
                    ("drafter", "send_return_notice"),
                ],
            )


def _create_audit_log_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE audit_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            timestamp TEXT NOT NULL,
            agent_id TEXT,
            tool_name TEXT NOT NULL,
            args_masked TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (
                decision IN (
                    'allowed', 'denied', 'error', 'pending',
                    'executed_after_approval', 'rejected', 'approval_expired'
                )
            ),
            reason TEXT NOT NULL,
            model_used TEXT,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL
        )
        """
    )


def _ensure_audit_log_schema(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'audit_log'"
    ).fetchone()
    if table is None:
        _create_audit_log_table(connection)
        return

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(audit_log)").fetchall()
    }
    if "sequence" in columns and "approval_expired" in table[0]:
        return

    connection.execute("ALTER TABLE audit_log RENAME TO audit_log_legacy")
    _create_audit_log_table(connection)
    if "sequence" in columns:
        rows = connection.execute(
            "SELECT sequence, id, timestamp, agent_id, tool_name, args_masked, "
            "decision, reason, model_used, prev_hash, entry_hash "
            "FROM audit_log_legacy ORDER BY sequence"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT id, timestamp, agent_id, tool_name, args_masked, decision, "
            "reason, model_used, prev_hash, entry_hash FROM audit_log_legacy"
        ).fetchall()
        remaining = list(rows)
        ordered_rows = []
        previous_hash = AUDIT_GENESIS_HASH
        while remaining:
            next_row = next(
                (row for row in remaining if row[8] == previous_hash),
                None,
            )
            if next_row is None:
                ordered_rows.extend(remaining)
                break
            ordered_rows.append(next_row)
            remaining.remove(next_row)
            previous_hash = next_row[9]
        rows = ordered_rows

    if "sequence" in columns:
        connection.executemany(
            """
            INSERT INTO audit_log
                (sequence, id, timestamp, agent_id, tool_name, args_masked, decision,
                 reason, model_used, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    else:
        connection.executemany(
            """
            INSERT INTO audit_log
                (id, timestamp, agent_id, tool_name, args_masked, decision, reason,
                 model_used, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    connection.execute("DROP TABLE audit_log_legacy")


def has_permission(agent_id: str, tool_name: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM permissions WHERE agent_id = ? AND tool_name = ?",
            (agent_id, tool_name),
        ).fetchone()
    return row is not None


def get_agent_permissions(agent_id: str) -> set[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT tool_name FROM permissions WHERE agent_id = ?",
            (agent_id,),
        ).fetchall()
    return {row["tool_name"] for row in rows}


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    init_db()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
