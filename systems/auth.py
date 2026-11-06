import hmac
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import GATEWAY_SERVICE_KEY


def require_gateway_key(
    x_gateway_key: Annotated[str | None, Header(alias="X-Gateway-Key")] = None,
) -> None:
    if x_gateway_key is None:
        raise HTTPException(status_code=401, detail="X-Gateway-Key header is required")
    if not GATEWAY_SERVICE_KEY or not hmac.compare_digest(
        x_gateway_key, GATEWAY_SERVICE_KEY
    ):
        raise HTTPException(status_code=403, detail="Invalid gateway service key")
