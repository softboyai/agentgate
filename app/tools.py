import json
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LicenceStatusArgs(StrictArgs):
    business: str = Field(min_length=1, max_length=100)


class OpenComplaintsArgs(StrictArgs):
    business: str | None = Field(default=None, min_length=1, max_length=100)


_LICENCES = {
    "Kivu Juice Co": {
        "business": "Kivu Juice Co",
        "license_id": "LIC-1001",
        "status": "valid",
        "expires_on": "2027-06-30",
    },
    "Musanze Tea House": {
        "business": "Musanze Tea House",
        "license_id": "LIC-1002",
        "status": "expired",
        "expires_on": "2026-03-31",
    },
}

_COMPLAINTS = [
    {
        "complaint_id": "CMP-2001",
        "business": "Kivu Juice Co",
        "subject": "Delayed service",
        "status": "open",
    },
    {
        "complaint_id": "CMP-2002",
        "business": "Musanze Tea House",
        "subject": "Incorrect billing",
        "status": "open",
    },
    {
        "complaint_id": "CMP-2003",
        "business": "Kivu Juice Co",
        "subject": "Resolved request",
        "status": "closed",
    },
]


def get_licence_status(business: str) -> dict[str, Any]:
    result = _LICENCES.get(business)
    if result is None:
        return {"error": "Business not found"}
    return result.copy()


def list_open_complaints(business: str | None = None) -> list[dict[str, str]]:
    return [
        complaint.copy()
        for complaint in _COMPLAINTS
        if complaint["status"] == "open"
        and (business is None or complaint["business"] == business)
    ]


_TOOL_SPECS: dict[
    str, tuple[type[StrictArgs], Callable[..., Any], dict[str, Any]]
] = {
    "get_licence_status": (
        LicenceStatusArgs,
        get_licence_status,
        {
            "type": "object",
            "properties": {
                "business": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "description": "Business name, for example Kivu Juice Co.",
                }
            },
            "required": ["business"],
            "additionalProperties": False,
        },
    ),
    "list_open_complaints": (
        OpenComplaintsArgs,
        list_open_complaints,
        {
            "type": "object",
            "properties": {
                "business": {
                    "type": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 100,
                    "description": "Optional business name to filter by.",
                }
            },
            "additionalProperties": False,
        },
    ),
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": (
                "Look up the licence status for a business in the fake licence data."
                if name == "get_licence_status"
                else "List open complaints in the fake data, optionally for one business."
            ),
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

    result = handler(**validated.model_dump())
    return json.dumps(result)
