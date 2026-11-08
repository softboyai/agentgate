from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query

from systems.auth import require_gateway_key
from systems.data_loader import load_json
from systems.schemas import Agency, DraftReturnNoticeRequest, ReturnedFile

app = FastAPI(
    title="AgentGate Fake Review System",
    description=(
        "Fake agency review queues and returned files for a software demo. "
        "The data and checks are not official."
    ),
    version="0.2.0",
)

_application_ids = {
    record["application_id"] for record in load_json("fake_applications.json")
}
_review_data: dict[str, Any] = load_json("fake_reviews.json")
_queues: dict[Agency, list[str]] = _review_data["queues"]
_returned_files = [
    ReturnedFile.model_validate(file_record)
    for file_record in _review_data["returned_files"]
]
_return_notice_drafts: dict[str, dict[str, Any]] = {}


@app.get(
    "/queues",
    dependencies=[Depends(require_gateway_key)],
)
def list_queues(
    agency: Annotated[Agency | None, Query()] = None,
) -> dict[str, list[str]]:
    if agency is not None:
        return {agency: _queues[agency]}
    return _queues


@app.get(
    "/applications/{application_id}/reviews",
    dependencies=[Depends(require_gateway_key)],
)
def get_application_reviews(application_id: str) -> dict[str, Any]:
    if application_id not in _application_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{application_id}' was not found.",
        )
    return {
        "application_id": application_id,
        "queues": {
            agency: application_id in queued_ids
            for agency, queued_ids in _queues.items()
        },
        "returned_files": [
            record.model_dump()
            for record in _returned_files
            if record.application_id == application_id
        ],
    }


@app.get(
    "/returned-files",
    response_model=list[ReturnedFile],
    dependencies=[Depends(require_gateway_key)],
)
def list_returned_files(
    agency: Annotated[Agency | None, Query()] = None,
) -> list[ReturnedFile]:
    return [
        record
        for record in _returned_files
        if agency is None or record.agency == agency
    ]


@app.post(
    "/return-notices/drafts",
    dependencies=[Depends(require_gateway_key)],
)
def draft_return_notice(request: DraftReturnNoticeRequest) -> dict[str, Any]:
    if request.application_id not in _application_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{request.application_id}' was not found.",
        )
    draft_id = str(uuid4())
    draft = {
        "draft_id": draft_id,
        "application_id": request.application_id,
        "message": request.message,
        "sent": False,
    }
    _return_notice_drafts[draft_id] = draft
    return draft


@app.post(
    "/return-notices/{draft_id}/send",
    dependencies=[Depends(require_gateway_key)],
)
def send_return_notice(draft_id: str) -> dict[str, Any]:
    draft = _return_notice_drafts.get(draft_id)
    if draft is None:
        raise HTTPException(
            status_code=404,
            detail=f"Return notice draft '{draft_id}' was not found.",
        )
    if draft["sent"]:
        raise HTTPException(
            status_code=409,
            detail=f"Return notice draft '{draft_id}' has already been sent.",
        )
    draft["sent"] = True
    return draft
