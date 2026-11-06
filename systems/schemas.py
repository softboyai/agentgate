from typing import Literal

from pydantic import BaseModel, ConfigDict

Agency = Literal["RFDA", "RICA", "RSB", "REMA"]
BusinessSize = Literal["MSME", "SME", "large"]
DocumentName = Literal[
    "trademark",
    "lease",
    "rra_classification",
    "occupancy_permit",
    "proof_of_payment",
    "esia",
]


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    present: bool
    legible: bool
    notarised: bool
    expiry_date: str | None
    business_name: str | None


class ApplicationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    business_name: str
    sector: Literal["food", "cosmetics"]
    business_size: BusinessSize
    uses_plastics: bool
    documents: dict[DocumentName, DocumentRecord]


class ReturnedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    agency: Agency
    return_rounds: int
    reasons: list[str]
