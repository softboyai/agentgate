from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app import tools


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    description: str
    input_model: type[BaseModel]
    risk_class: str
    handler: Callable[..., Any] | None

    def model_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


TOOLS: dict[str, ToolRegistration] = {
    "get_application": ToolRegistration(
        name="get_application",
        description="Retrieve one fake harmonised food/cosmetics application.",
        input_model=tools.ApplicationArgs,
        risk_class="assistive",
        handler=tools.get_application,
    ),
    "check_documents": ToolRegistration(
        name="check_documents",
        description="Check fake application documents using demo-only Python rules.",
        input_model=tools.CheckDocumentsArgs,
        risk_class="assistive",
        handler=tools.check_documents,
    ),
    "list_returned_files": ToolRegistration(
        name="list_returned_files",
        description="List fake files returned by an agency, optionally filtered.",
        input_model=tools.ReturnedFilesArgs,
        risk_class="assistive",
        handler=tools.list_returned_files,
    ),
    "approve_application": ToolRegistration(
        name="approve_application",
        description="Application decisions are made only by an authorised officer.",
        input_model=tools.ApplicationArgs,
        risk_class="human_only",
        handler=None,
    ),
}
