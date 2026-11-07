import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
)

from app import db
from app.config import JWT_SECRET, TOKEN_TTL_MINUTES

ALGORITHM = "HS256"


class AgentNotFoundError(LookupError):
    pass


class AgentUnavailableError(ValueError):
    pass


class TokenVerificationError(ValueError):
    def __init__(
        self,
        reason: str,
        message: str,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.agent_id = agent_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _key_id(secret: str) -> str:
    return hashlib.sha256(b"agentgate-kid:" + secret.encode("utf-8")).hexdigest()[:8]


def _timestamp(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _row_to_agent(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def register_agent(
    name: str,
    owner: str,
    purpose: str,
    represents: str,
    agent_id: str | None = None,
    expires_at: str | datetime | None = None,
) -> dict[str, Any]:
    agent = {
        "id": agent_id or str(uuid4()),
        "name": name,
        "owner": owner,
        "purpose": purpose,
        "represents": represents,
        "status": "active",
        "created_at": _utc_now().isoformat(),
        "expires_at": _timestamp(expires_at),
    }
    try:
        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO agents
                    (id, name, owner, purpose, represents, status, created_at, expires_at)
                VALUES
                    (:id, :name, :owner, :purpose, :represents, :status, :created_at, :expires_at)
                """,
                agent,
            )
    except sqlite3.IntegrityError as error:
        raise ValueError(f"Agent '{agent['id']}' is already registered.") from error
    return agent


def get_agent(agent_id: str) -> dict[str, Any] | None:
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT id, name, owner, purpose, represents, status, created_at, expires_at "
            "FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
    return _row_to_agent(row)


def _set_agent_status(agent_id: str, status: str) -> dict[str, Any]:
    with db.get_connection() as connection:
        cursor = connection.execute(
            "UPDATE agents SET status = ? WHERE id = ?",
            (status, agent_id),
        )
        if cursor.rowcount == 0:
            raise AgentNotFoundError(f"Agent '{agent_id}' is not registered.")
    agent = get_agent(agent_id)
    if agent is None:
        raise AgentNotFoundError(f"Agent '{agent_id}' is not registered.")
    return agent


def suspend_agent(agent_id: str) -> dict[str, Any]:
    return _set_agent_status(agent_id, "suspended")


def resume_agent(agent_id: str) -> dict[str, Any]:
    return _set_agent_status(agent_id, "active")


def _agent_expiry(agent: dict[str, Any]) -> datetime | None:
    expiry = agent["expires_at"]
    if expiry is None:
        return None
    parsed = datetime.fromisoformat(expiry)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def issue_token(
    agent_id: str,
    expires_in: timedelta | None = None,
) -> str:
    agent = get_agent(agent_id)
    if agent is None:
        raise AgentNotFoundError(f"Agent '{agent_id}' is not registered.")
    if agent["status"] == "suspended":
        raise AgentUnavailableError(f"Agent '{agent_id}' is suspended.")
    own_expiry = _agent_expiry(agent)
    if own_expiry is not None and own_expiry <= _utc_now():
        raise AgentUnavailableError(f"Agent '{agent_id}' has expired.")

    token_expiry = _utc_now() + (
        expires_in if expires_in is not None else timedelta(minutes=TOKEN_TTL_MINUTES)
    )
    return jwt.encode(
        {"agent_id": agent_id, "exp": token_expiry},
        JWT_SECRET,
        algorithm=ALGORITHM,
        headers={"kid": _key_id(JWT_SECRET)},
    )


def verify_token(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as error:
        raise TokenVerificationError(
            "altered_token", "Token is malformed or has been altered."
        ) from error

    if "kid" not in header:
        raise TokenVerificationError(
            "invalid_token", "Token is malformed."
        )

    if header.get("kid") != _key_id(JWT_SECRET):
        raise TokenVerificationError(
            "wrong_secret", "Token was signed with a different JWT_SECRET."
        )

    try:
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "agent_id"]},
        )
    except MissingRequiredClaimError as error:
        raise TokenVerificationError(
            "invalid_token", f"Token is missing the required '{error.claim}' claim."
        ) from error
    except ExpiredSignatureError as error:
        raise TokenVerificationError(
            "expired_token", "Token has expired."
        ) from error
    except InvalidSignatureError as error:
        raise TokenVerificationError(
            "altered_token", "Token signature is invalid; it may have been altered."
        ) from error
    except InvalidTokenError as error:
        raise TokenVerificationError(
            "altered_token", "Token is malformed or has been altered."
        ) from error

    agent_id = claims.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise TokenVerificationError(
            "altered_token", "Token does not contain a valid agent_id."
        )
    agent = get_agent(agent_id)
    if agent is None:
        raise TokenVerificationError(
            "unknown_agent", f"Token refers to unknown agent '{agent_id}'."
        )
    if agent["status"] == "suspended":
        raise TokenVerificationError(
            "suspended_agent",
            f"Agent '{agent_id}' is suspended.",
            agent_id=agent_id,
        )
    own_expiry = _agent_expiry(agent)
    if own_expiry is not None and own_expiry <= _utc_now():
        raise TokenVerificationError(
            "agent_expired",
            f"Agent '{agent_id}' has expired.",
            agent_id=agent_id,
        )
    return agent


def seed_demo_agents() -> list[dict[str, Any]]:
    demo_agents = [
        {
            "agent_id": "reader",
            "name": "Reader",
            "owner": "AgentGate Demo Team",
            "purpose": "Read fake licensing application records.",
            "represents": "the licensing officer, RFDA desk",
        },
        {
            "agent_id": "drafter",
            "name": "Drafter",
            "owner": "AgentGate Demo Team",
            "purpose": "Prepare draft explanations from fake application records.",
            "represents": "the licensing officer, RICA desk",
        },
        {
            "agent_id": "flagger",
            "name": "Flagger",
            "owner": "AgentGate Demo Team",
            "purpose": "Identify fake application records that need human review.",
            "represents": "the licensing officer, RSB desk",
        },
    ]
    seeded = []
    for fields in demo_agents:
        agent = get_agent(fields["agent_id"])
        if agent is None:
            agent = register_agent(**fields)
        seeded.append(agent)
    return seeded
