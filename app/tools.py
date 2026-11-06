import json
from datetime import date
from typing import Any, Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    APPLICATION_SYSTEM_URL,
    GATEWAY_SERVICE_KEY,
    REVIEW_SYSTEM_URL,
)

Agency = Literal["RFDA", "RICA", "RSB", "REMA"]


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApplicationArgs(StrictArgs):
    application_id: str = Field(min_length=1, max_length=30)


class CheckDocumentsArgs(ApplicationArgs):
    pass


class ReturnedFilesArgs(StrictArgs):
    agency: Agency | None = None


def _service_request(base_url: str, path: str) -> Any:
    if not GATEWAY_SERVICE_KEY:
        return {"error": "GATEWAY_SERVICE_KEY is not configured", "status_code": 503}

    request = Request(
        f"{base_url}{path}",
        headers={"X-Gateway-Key": GATEWAY_SERVICE_KEY},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            try:
                return json.loads(response.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {
                    "error": "Service returned an invalid JSON response",
                    "status_code": 502,
                }
    except HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"detail": "Service returned an unreadable error response"}
        return {
            "error": (
                body.get("detail", "Service request failed")
                if isinstance(body, dict)
                else "Service request failed"
            ),
            "status_code": error.code,
        }
    except (TimeoutError, URLError) as error:
        reason = error.reason if isinstance(error, URLError) else "request timed out"
        return {"error": f"Service unavailable: {reason}", "status_code": 503}


def get_application(application_id: str) -> dict[str, Any]:
    result = _service_request(
        APPLICATION_SYSTEM_URL,
        f"/applications/{quote(application_id, safe='')}",
    )
    return result


def evaluate_documents(application: dict[str, Any]) -> dict[str, Any]:
    """Apply explicit, demo-only checks; these are not official licensing rules."""
    business_name = application["business_name"]
    business_size = application["business_size"]
    required_documents = {
        "trademark",
        "lease",
        "rra_classification",
        "occupancy_permit",
    }
    if business_size in {"SME", "large"}:
        required_documents.add("proof_of_payment")
    if application["uses_plastics"]:
        required_documents.add("esia")

    issues: list[dict[str, str]] = []
    for document_name, document in application["documents"].items():
        if not document["present"]:
            if document_name in required_documents:
                issues.append(
                    {
                        "code": "missing",
                        "document": document_name,
                        "message": f"Required document '{document_name}' is missing.",
                    }
                )
            continue

        if not document["legible"]:
            issues.append(
                {
                    "code": "illegible",
                    "document": document_name,
                    "message": f"Document '{document_name}' is not legible.",
                }
            )
        if not document["notarised"]:
            issues.append(
                {
                    "code": "not_notarised",
                    "document": document_name,
                    "message": f"Document '{document_name}' is not notarised.",
                }
            )

        expiry_date = document["expiry_date"]
        if expiry_date and date.fromisoformat(expiry_date) < date.today():
            issues.append(
                {
                    "code": "expired",
                    "document": document_name,
                    "message": f"Document '{document_name}' expired on {expiry_date}.",
                }
            )

        submitted_name = document["business_name"]
        if (
            submitted_name
            and submitted_name.strip().casefold() != business_name.strip().casefold()
        ):
            issues.append(
                {
                    "code": "name_mismatch",
                    "document": document_name,
                    "message": (
                        f"Document '{document_name}' names '{submitted_name}', "
                        f"not '{business_name}'."
                    ),
                }
            )

    return {
        "application_id": application["application_id"],
        "business_name": business_name,
        "business_size": business_size,
        "uses_plastics": application["uses_plastics"],
        "required_documents": sorted(required_documents),
        "rules_notice": "Demo checks only; these are not official licensing rules.",
        "passed": not issues,
        "issues": issues,
    }


def check_documents(application_id: str) -> dict[str, Any]:
    application = get_application(application_id)
    if "error" in application:
        return application
    return evaluate_documents(application)


def list_returned_files(agency: Agency | None = None) -> Any:
    query = f"?{urlencode({'agency': agency})}" if agency else ""
    result = _service_request(REVIEW_SYSTEM_URL, f"/returned-files{query}")
    return result


_TOOL_SPECS: dict[
    str, tuple[type[StrictArgs], Callable[..., Any], dict[str, Any]]
] = {
    "get_application": (
        ApplicationArgs,
        get_application,
        {
            "type": "object",
            "properties": {
                "application_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 30,
                    "description": "Application ID, for example APP-1001.",
                }
            },
            "required": ["application_id"],
            "additionalProperties": False,
        },
    ),
    "check_documents": (
        CheckDocumentsArgs,
        check_documents,
        {
            "type": "object",
            "properties": {
                "application_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 30,
                    "description": "Application ID to check using the demo rules.",
                }
            },
            "required": ["application_id"],
            "additionalProperties": False,
        },
    ),
    "list_returned_files": (
        ReturnedFilesArgs,
        list_returned_files,
        {
            "type": "object",
            "properties": {
                "agency": {
                    "type": ["string", "null"],
                    "enum": ["RFDA", "RICA", "RSB", "REMA", None],
                    "description": "Optional agency filter.",
                }
            },
            "additionalProperties": False,
        },
    ),
}

_TOOL_DESCRIPTIONS = {
    "get_application": "Retrieve one fake harmonised food/cosmetics application.",
    "check_documents": "Check fake application documents using explicit demo-only rules.",
    "list_returned_files": "List fake files returned by RFDA, RICA, RSB, or REMA.",
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
            "parameters": parameters,
        },
    }
    for name, (_, _, parameters) in _TOOL_SPECS.items()
]


def execute_tool(name: str, arguments_json: str) -> str:
    spec = _TOOL_SPECS.get(name)
    if spec is None:
        return json.dumps({"error": f"Tool not allowed: {name}"})

    args_model, handler, _ = spec
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as error:
        return json.dumps({"error": f"Invalid JSON arguments: {error.msg}"})

    try:
        validated = args_model.model_validate(arguments)
    except ValidationError as error:
        return json.dumps(
            {"error": "Invalid tool arguments", "details": error.errors()}
        )

    return json.dumps(handler(**validated.model_dump()), ensure_ascii=False)
