from app import approvals, db, gateway, identity


def _show(label: str, result: dict) -> None:
    approval_id = result.get("approval_id")
    suffix = f" approval_id={approval_id}" if approval_id else ""
    print(f"{label}: status={result['status']} reason={result['reason']}{suffix}")


def main() -> None:
    db.init_db()
    identity.seed_demo_agents()
    drafter_token = identity.issue_token("drafter")

    draft = gateway.call_tool(
        drafter_token,
        "draft_return_notice",
        {
            "application_id": "APP-1001",
            "message": "Please provide the missing licensing document.",
        },
    )
    _show("Draft", draft)
    if draft["status"] != "allowed":
        return

    first_request = gateway.call_tool(
        drafter_token,
        "send_return_notice",
        {"draft_id": draft["data"]["draft_id"]},
    )
    _show("Send request", first_request)
    first_approval = approvals.approve(
        first_request["approval_id"],
        "officer-1",
        "Reviewed for the demo.",
    )
    _show("Officer approval", first_approval)

    repeated = approvals.approve(
        first_request["approval_id"],
        "officer-1",
        "Retry must not resend.",
    )
    _show("Repeated approval", repeated)

    second_draft = gateway.call_tool(
        drafter_token,
        "draft_return_notice",
        {
            "application_id": "APP-1001",
            "message": "A separate notice draft for the rejection demonstration.",
        },
    )
    _show("Second draft", second_draft)
    if second_draft["status"] != "allowed":
        return

    second_request = gateway.call_tool(
        drafter_token,
        "send_return_notice",
        {"draft_id": second_draft["data"]["draft_id"]},
    )
    _show("Second send request", second_request)
    rejected = approvals.reject(
        second_request["approval_id"],
        "officer-2",
        "Rejected for the demo.",
    )
    _show("Officer rejection", rejected)


if __name__ == "__main__":
    main()
