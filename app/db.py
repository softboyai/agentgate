import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "agentgate.db"


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        permissions_table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'permissions'"
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
