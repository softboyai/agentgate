from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import config, db, identity

TEST_SECRET = "test-jwt-secret-that-is-at-least-32-chars"


@pytest.fixture
def isolated_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agents.sqlite3")
    monkeypatch.setattr(identity, "JWT_SECRET", TEST_SECRET)
    db.init_db()
    return identity


def _token_for_agent(agent_id: str, expires_at: datetime) -> str:
    return jwt.encode(
        {"agent_id": agent_id, "exp": expires_at},
        TEST_SECRET,
        algorithm=identity.ALGORITHM,
        headers={"kid": identity._key_id(TEST_SECRET)},
    )


def _register(identity_module, agent_id: str = "test-agent", **overrides):
    fields = {
        "agent_id": agent_id,
        "name": "Test Agent",
        "owner": "Demo Owner",
        "purpose": "Test identity behavior.",
        "represents": "a fake licensing officer",
    }
    fields.update(overrides)
    return identity_module.register_agent(**fields)


def test_valid_token_verifies(isolated_identity) -> None:
    agent = _register(isolated_identity)
    token = isolated_identity.issue_token(agent["id"])

    assert isolated_identity.verify_token(token)["id"] == "test-agent"


def test_token_defaults_to_fifteen_minute_expiry(isolated_identity) -> None:
    agent = _register(isolated_identity)
    token = isolated_identity.issue_token(agent["id"])
    claims = jwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
    )

    assert claims["agent_id"] == agent["id"]
    assert 14 * 60 < claims["exp"] - datetime.now(timezone.utc).timestamp() <= 15 * 60


def test_expired_token_fails(isolated_identity) -> None:
    _register(isolated_identity)
    token = isolated_identity.issue_token(
        "test-agent",
        expires_in=timedelta(seconds=-1),
    )

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "expired_token"
    assert "expired" in str(error.value).lower()


def test_altered_token_fails(isolated_identity) -> None:
    _register(isolated_identity)
    token = isolated_identity.issue_token("test-agent")
    header, payload, signature = token.split(".")
    replacement = "A" if payload[0] != "A" else "B"
    altered_token = ".".join((header, replacement + payload[1:], signature))

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(altered_token)

    assert error.value.reason == "altered_token"
    assert "altered" in str(error.value).lower()


def test_token_signed_with_wrong_secret_fails(isolated_identity, monkeypatch) -> None:
    _register(isolated_identity)
    monkeypatch.setattr(identity, "JWT_SECRET", "another-different-secret-longer-than-32")
    token = isolated_identity.issue_token("test-agent")
    monkeypatch.setattr(identity, "JWT_SECRET", TEST_SECRET)

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "wrong_secret"
    assert "different jwt_secret" in str(error.value).lower()


def test_token_without_kid_is_malformed(isolated_identity) -> None:
    token = jwt.encode(
        {
            "agent_id": "test-agent",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm=identity.ALGORITHM,
    )

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "invalid_token"
    assert str(error.value) == "Token is malformed."


def test_token_without_exp_claim_is_invalid(isolated_identity) -> None:
    token = jwt.encode(
        {"agent_id": "test-agent"},
        TEST_SECRET,
        algorithm=identity.ALGORITHM,
        headers={"kid": identity._key_id(TEST_SECRET)},
    )

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "invalid_token"
    assert "exp" in str(error.value)


def test_unknown_agent_fails(isolated_identity) -> None:
    _register(isolated_identity)
    token = isolated_identity.issue_token("test-agent")
    with db.get_connection() as connection:
        connection.execute("DELETE FROM agents WHERE id = ?", ("test-agent",))

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "unknown_agent"
    assert "unknown agent" in str(error.value).lower()


def test_suspended_agent_fails_even_with_unexpired_token(isolated_identity) -> None:
    agent = _register(isolated_identity)
    token = isolated_identity.issue_token(agent["id"])
    isolated_identity.suspend_agent(agent["id"])

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "suspended_agent"
    assert "suspended" in str(error.value).lower()


def test_resumed_agent_works_again(isolated_identity) -> None:
    agent = _register(isolated_identity)
    token = isolated_identity.issue_token(agent["id"])
    isolated_identity.suspend_agent(agent["id"])
    isolated_identity.resume_agent(agent["id"])

    assert isolated_identity.verify_token(token)["status"] == "active"


def test_agent_past_own_expiry_fails(isolated_identity) -> None:
    expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    _register(isolated_identity, expires_at=expiry)
    token = _token_for_agent(
        "test-agent",
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    with pytest.raises(identity.TokenVerificationError) as error:
        isolated_identity.verify_token(token)

    assert error.value.reason == "agent_expired"
    assert "agent" in str(error.value).lower()
    assert "expired" in str(error.value).lower()


def test_missing_jwt_secret_refuses_startup(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MODEL=deepseek-chat\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="JWT_SECRET must be set"):
        config.load_jwt_secret(env_file)


def test_short_jwt_secret_refuses_startup(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JWT_SECRET=too-short\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        config.load_jwt_secret(env_file)


def test_seed_demo_agents_registers_three_fake_agents(isolated_identity) -> None:
    seeded = isolated_identity.seed_demo_agents()

    assert {agent["id"] for agent in seeded} == {"reader", "drafter", "flagger"}
    assert all(agent["owner"] for agent in seeded)
    assert all(agent["purpose"] for agent in seeded)
    assert all(agent["represents"] for agent in seeded)
    assert isolated_identity.seed_demo_agents() == seeded
