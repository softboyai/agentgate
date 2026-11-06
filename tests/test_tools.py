import json

from app import tools
from systems.application_system import APPLICATIONS


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.content


def _issue_codes(application_id: str) -> set[tuple[str, str]]:
    result = tools.evaluate_documents(
        APPLICATIONS[application_id].model_dump()
    )
    return {(issue["code"], issue["document"]) for issue in result["issues"]}


def test_tool_schemas_expose_only_stage_two_tools() -> None:
    names = [tool["function"]["name"] for tool in tools.TOOL_DEFINITIONS]

    assert names == [
        "get_application",
        "check_documents",
        "list_returned_files",
    ]


def test_missing_lease_is_reported() -> None:
    assert ("missing", "lease") in _issue_codes("APP-1007")


def test_expired_trademark_is_reported() -> None:
    assert ("expired", "trademark") in _issue_codes("APP-1003")


def test_business_name_mismatch_is_reported() -> None:
    assert ("name_mismatch", "lease") in _issue_codes("APP-1004")


def test_large_company_without_payment_proof_is_reported() -> None:
    assert ("missing", "proof_of_payment") in _issue_codes("APP-1005")


def test_plastics_business_without_esia_is_reported() -> None:
    assert ("missing", "esia") in _issue_codes("APP-1006")


def test_missing_lease_example_is_reported() -> None:
    assert ("missing", "lease") in _issue_codes("APP-1007")


def test_plastics_business_without_esia_example_is_reported() -> None:
    application = APPLICATIONS["APP-1006"]
    assert application.uses_plastics is True
    assert ("missing", "esia") in _issue_codes("APP-1006")


def test_added_missing_lease_application_has_only_lease_problem() -> None:
    result = tools.evaluate_documents(APPLICATIONS["APP-1011"].model_dump())

    assert result["issues"] == [
        {
            "code": "missing",
            "document": "lease",
            "message": "Required document 'lease' is missing.",
        }
    ]


def test_added_plastics_application_without_esia_has_only_esia_problem() -> None:
    application = APPLICATIONS["APP-1012"]
    result = tools.evaluate_documents(application.model_dump())

    assert application.uses_plastics is True
    assert result["issues"] == [
        {
            "code": "missing",
            "document": "esia",
            "message": "Required document 'esia' is missing.",
        }
    ]


def test_illegible_document_example_is_reported() -> None:
    assert ("illegible", "lease") in _issue_codes("APP-1009")


def test_not_notarised_document_example_is_reported() -> None:
    assert ("not_notarised", "trademark") in _issue_codes("APP-1010")


def test_msme_does_not_need_payment_proof_under_demo_rule() -> None:
    result = tools.evaluate_documents(APPLICATIONS["APP-1002"].model_dump())

    assert result["passed"] is True
    assert result["required_documents"] == [
        "lease",
        "occupancy_permit",
        "rra_classification",
        "trademark",
    ]
    assert ("missing", "proof_of_payment") not in _issue_codes("APP-1002")
    assert ("missing", "esia") not in _issue_codes("APP-1002")


def test_document_quality_issues_are_reported() -> None:
    application = APPLICATIONS["APP-1001"].model_dump()
    application["documents"]["lease"]["legible"] = False
    application["documents"]["trademark"]["notarised"] = False

    result = tools.evaluate_documents(application)
    codes = {(issue["code"], issue["document"]) for issue in result["issues"]}

    assert ("illegible", "lease") in codes
    assert ("not_notarised", "trademark") in codes


def test_document_checks_identify_demo_rules_in_output() -> None:
    result = tools.evaluate_documents(APPLICATIONS["APP-1001"].model_dump())

    assert "not official licensing rules" in result["rules_notice"]
    assert result["passed"] is True


def test_execute_tool_rejects_unknown_tool() -> None:
    result = json.loads(tools.execute_tool("delete_application", "{}"))

    assert result["error"] == "Tool not allowed: delete_application"


def test_execute_tool_validates_arguments() -> None:
    result = json.loads(tools.execute_tool("get_application", '{"application_id": ""}'))

    assert result["error"] == "Invalid tool arguments"


def test_execute_tool_rejects_extra_arguments() -> None:
    result = json.loads(
        tools.execute_tool(
            "get_application",
            '{"application_id": "APP-1001", "system": "production"}',
        )
    )

    assert result["error"] == "Invalid tool arguments"


def test_document_tool_returns_unknown_application_error(monkeypatch) -> None:
    monkeypatch.setattr(
        tools,
        "get_application",
        lambda _: {"error": "Application 'APP-9999' was not found.", "status_code": 404},
    )

    result = json.loads(
        tools.execute_tool("check_documents", '{"application_id": "APP-9999"}')
    )

    assert result["status_code"] == 404
    assert "APP-9999" in result["error"]


def test_application_tool_sends_gateway_key_over_http(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout: int):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(b'{"application_id": "APP-1001"}')

    monkeypatch.setattr(tools, "GATEWAY_SERVICE_KEY", "local-test-key")
    monkeypatch.setattr(tools, "APPLICATION_SYSTEM_URL", "http://application.test")
    monkeypatch.setattr(tools, "urlopen", fake_urlopen)

    result = tools.get_application("APP-1001")

    assert result == {"application_id": "APP-1001"}
    assert captured["request"].full_url == (
        "http://application.test/applications/APP-1001"
    )
    assert captured["request"].headers["X-gateway-key"] == "local-test-key"
    assert captured["timeout"] == 5


def test_tool_returns_explicit_error_when_gateway_key_is_not_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(tools, "GATEWAY_SERVICE_KEY", "")

    assert tools.get_application("APP-1001") == {
        "error": "GATEWAY_SERVICE_KEY is not configured",
        "status_code": 503,
    }
