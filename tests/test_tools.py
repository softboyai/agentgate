import json

from app.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    get_licence_status,
    list_open_complaints,
)


def test_get_licence_status_returns_kivu_juice_fake_data() -> None:
    assert get_licence_status("Kivu Juice Co") == {
        "business": "Kivu Juice Co",
        "license_id": "LIC-1001",
        "status": "valid",
        "expires_on": "2027-06-30",
    }


def test_get_licence_status_returns_error_for_unknown_business() -> None:
    assert get_licence_status("Unknown Fake Business") == {
        "error": "Business not found"
    }


def test_list_open_complaints_returns_only_open_and_filters() -> None:
    complaints = list_open_complaints("Kivu Juice Co")

    assert len(complaints) == 1
    assert complaints[0]["complaint_id"] == "CMP-2001"
    assert complaints[0]["status"] == "open"


def test_tool_schemas_use_business_names() -> None:
    licence_tool, complaints_tool = TOOL_DEFINITIONS

    assert licence_tool["function"]["parameters"]["required"] == ["business"]
    assert "business" in licence_tool["function"]["parameters"]["properties"]
    assert "business" in complaints_tool["function"]["parameters"]["properties"]


def test_execute_tool_rejects_unknown_tool() -> None:
    result = json.loads(execute_tool("delete_licence", "{}"))

    assert result["error"] == "Tool not allowed: delete_licence"


def test_execute_tool_validates_arguments() -> None:
    result = json.loads(
        execute_tool("get_licence_status", '{"business": ""}')
    )

    assert result["error"] == "Invalid tool arguments"


def test_execute_tool_rejects_extra_arguments() -> None:
    result = json.loads(
        execute_tool(
            "get_licence_status",
            '{"business": "Kivu Juice Co", "system": "production"}',
        )
    )

    assert result["error"] == "Invalid tool arguments"
