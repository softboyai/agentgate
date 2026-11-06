import pytest
from fastapi.testclient import TestClient

from systems import auth
from systems.application_system import app as application_app
from systems.review_system import app as review_app

KEY = "test-gateway-service-key"
SYSTEM_CASES = [
    (application_app, "/applications/APP-1001", "/applications/APP-9999"),
    (
        review_app,
        "/applications/APP-1001/reviews",
        "/applications/APP-9999/reviews",
    ),
]


@pytest.fixture(autouse=True)
def gateway_key(monkeypatch) -> None:
    monkeypatch.setattr(auth, "GATEWAY_SERVICE_KEY", KEY)


@pytest.mark.parametrize(("service", "known_path", "unknown_path"), SYSTEM_CASES)
def test_missing_gateway_key_is_401(
    service, known_path: str, unknown_path: str
) -> None:
    response = TestClient(service).get(known_path)

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Gateway-Key header is required"


@pytest.mark.parametrize(("service", "known_path", "unknown_path"), SYSTEM_CASES)
def test_wrong_gateway_key_is_403(
    service, known_path: str, unknown_path: str
) -> None:
    response = TestClient(service).get(
        known_path,
        headers={"X-Gateway-Key": "wrong-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid gateway service key"


@pytest.mark.parametrize(("service", "known_path", "unknown_path"), SYSTEM_CASES)
def test_correct_gateway_key_returns_application_or_review(
    service, known_path: str, unknown_path: str
) -> None:
    response = TestClient(service).get(
        known_path,
        headers={"X-Gateway-Key": KEY},
    )

    assert response.status_code == 200
    assert response.json()["application_id"] == "APP-1001"


@pytest.mark.parametrize(("service", "known_path", "unknown_path"), SYSTEM_CASES)
def test_unknown_application_is_404(
    service, known_path: str, unknown_path: str
) -> None:
    response = TestClient(service).get(
        unknown_path,
        headers={"X-Gateway-Key": KEY},
    )

    assert response.status_code == 404
    assert "APP-9999" in response.json()["detail"]


def test_application_openapi_includes_authenticated_endpoint() -> None:
    response = TestClient(application_app).get("/openapi.json")

    assert response.status_code == 200
    assert "/applications/{application_id}" in response.json()["paths"]


def test_review_queues_include_all_four_agencies() -> None:
    response = TestClient(review_app).get(
        "/queues",
        headers={"X-Gateway-Key": KEY},
    )

    assert response.status_code == 200
    assert set(response.json()) == {"RFDA", "RICA", "RSB", "REMA"}


def test_returned_files_include_return_round_count() -> None:
    response = TestClient(review_app).get(
        "/returned-files",
        headers={"X-Gateway-Key": KEY},
    )

    assert response.status_code == 200
    assert response.json()[0]["return_rounds"] >= 1
