import json

from app import audit, db


def main() -> None:
    db.init_db()
    for row in audit.list_recent(20):
        print(json.dumps(row, sort_keys=True))
    print(f"verify: {json.dumps(audit.verify(), sort_keys=True)}")


if __name__ == "__main__":
    main()
