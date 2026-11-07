from app import db, gateway, identity


def main() -> None:
    db.init_db()
    identity.seed_demo_agents()
    reader_token = identity.issue_token("reader")
    cases = [
        (
            "allowed application",
            reader_token,
            "get_application",
            {"application_id": "APP-1001"},
        ),
        (
            "permission denied",
            reader_token,
            "approve_application",
            {"application_id": "APP-1001"},
        ),
        (
            "invalid arguments",
            reader_token,
            "get_application",
            {"application_id": ""},
        ),
        ("unknown tool", reader_token, "unknown_tool", {}),
        (
            "unknown application",
            reader_token,
            "get_application",
            {"application_id": "APP-9999"},
        ),
    ]

    for label, token, tool_name, arguments in cases:
        result = gateway.call_tool(token, tool_name, arguments)
        print(
            f"{label}: status={result['status']} "
            f"reason={result['reason']}"
        )


if __name__ == "__main__":
    main()
