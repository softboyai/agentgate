import sqlite3
import tempfile
from pathlib import Path
from shutil import copy2

from app import audit, db


def main() -> None:
    source = db.DB_PATH
    if not source.exists():
        raise FileNotFoundError(f"Database does not exist: {source}")

    with tempfile.TemporaryDirectory(prefix="agentgate-audit-tamper-") as folder:
        copy_path = Path(folder) / "agentgate-copy.db"
        copy2(source, copy_path)
        connection = sqlite3.connect(copy_path)
        try:
            rows = connection.execute(
                "SELECT id FROM audit_log ORDER BY rowid"
            ).fetchall()
            if len(rows) < 3:
                raise RuntimeError(
                    "Run the gateway demo first to create at least three audit entries."
                )
            broken_id = rows[len(rows) // 2][0]
            connection.execute(
                "UPDATE audit_log SET reason = ? WHERE id = ?",
                ("tampered_reason", broken_id),
            )
            connection.commit()
        finally:
            connection.close()

        result = audit.verify(copy_path)
        print(
            f"tampered copy row: {broken_id}; "
            f"verify: ok={result['ok']} "
            f"first_broken_id={result['first_broken_id']} "
            f"reason={result['reason']}"
        )


if __name__ == "__main__":
    main()
