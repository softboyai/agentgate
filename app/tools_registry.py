from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel

from app import tools

RiskClass = Literal["assistive", "supervised", "proactive", "decision"]


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    description: str
    input_model: type[BaseModel]
    risk_class: RiskClass
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
        risk_class="decision",
        handler=None,
    ),
    "draft_return_notice": ToolRegistration(
        name="draft_return_notice",
        description="Draft a fake return notice for officer review.",
        input_model=tools.DraftReturnNoticeArgs,
        risk_class="assistive",
        handler=tools.draft_return_notice,
    ),
    "send_return_notice": ToolRegistration(
        name="send_return_notice",
        description="Send a return notice after an authorised officer approves it.",
        input_model=tools.SendReturnNoticeArgs,
        risk_class="supervised",
        handler=tools.send_return_notice,
    ),
}
