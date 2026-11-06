from fastapi import Depends, FastAPI, HTTPException
from pydantic import TypeAdapter

from systems.auth import require_gateway_key
from systems.data_loader import load_json
from systems.schemas import ApplicationRecord

app = FastAPI(
    title="AgentGate Fake Application System",
    description=(
        "Fake food and cosmetics licensing applications for a software demo. "
        "The data and checks are not official."
    ),
    version="0.2.0",
)

_records = TypeAdapter(list[ApplicationRecord]).validate_python(
    load_json("fake_applications.json")
)
APPLICATIONS = {record.application_id: record for record in _records}


@app.get(
    "/applications/{application_id}",
    response_model=ApplicationRecord,
    dependencies=[Depends(require_gateway_key)],
)
def get_application(application_id: str) -> ApplicationRecord:
    application = APPLICATIONS.get(application_id)
    if application is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application '{application_id}' was not found.",
        )
    return application
