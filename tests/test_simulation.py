"""Test the TEMPORARY multi-user simulation through the in-process API.

Remove together with api/simulation.py once a real load test replaces it."""

import pytest
from fastapi.testclient import TestClient

from api import auth, main, simulation
from core import storage


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1_000)
    main.AUDITS.clear()
    simulation.SIMULATIONS.clear()
    yield
    main.AUDITS.clear()
    main.app.dependency_overrides.clear()


def fake_engine(*_args, **_kwargs):
    yield {"type": "start", "total_pages": 2, "reference_count": 2, "dpi": 200}
    for number, status in ((1, "OK"), (2, "ERROR")):
        yield {"type": "page", "page": number, "total_pages": 2,
               "row": {"Page": number, "Overall Status": status}}
    yield {"type": "end", "total_pages": 2, "model": "Tesseract OCR", "elapsed_seconds": 0.1, "missing": []}


def test_simulated_users_run_concurrently_and_stay_isolated(monkeypatch):
    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)
    state = simulation.new_state("presenter", users=3, pages=1, dpi=200)

    simulation.run_simulation(state, lambda: TestClient(main.app))

    assert state["status"] == "completed", state
    for user in state["users"]:
        assert user["status"] == "completed", user
        assert user["error"] is None
        assert (user["processed"], user["matched"], user["mismatched"]) == (2, 1, 1)
        assert user["isolated"] is True, user["isolation_detail"]
        assert "404" in user["isolation_detail"]
    assert len({user["job_id"] for user in state["users"]}) == 3
    assert state["summary"]["isolation_passed"] is True
    assert state["summary"]["pages"] == 6


def test_simulated_accounts_and_audits_are_removed(monkeypatch):
    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)
    state = simulation.new_state("presenter", users=2, pages=1, dpi=200)

    simulation.run_simulation(state, lambda: TestClient(main.app))

    for user in state["users"]:
        assert storage.password_hash(user["username"]) is None
        assert storage.load_summary(user["job_id"]) is None


def test_failed_user_is_reported_without_blocking_the_others(monkeypatch):
    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)
    state = simulation.new_state("presenter", users=2, pages=1, dpi=200)
    state["users"][1]["username"] = "sim-invalid name"  # rejected when the account is created

    simulation.run_simulation(state, lambda: TestClient(main.app))

    assert state["status"] == "error"
    assert "3 to 32" in state["error"]
    assert state["summary"]["isolation_passed"] is False


@pytest.fixture
def client():
    main.app.dependency_overrides[auth.current_user] = lambda: "presenter"
    return TestClient(main.app)


def test_only_one_simulation_runs_at_a_time(client):
    assert simulation._RUNNING.acquire(blocking=False)
    try:
        response = client.post("/api/v1/simulations", json={"users": 2, "pages": 1, "dpi": 200})
        assert response.status_code == 409
    finally:
        simulation._RUNNING.release()


@pytest.mark.parametrize("body", [{"users": 1}, {"users": 9}, {"pages": 0}, {"dpi": 400}])
def test_simulation_limits_are_validated(client, body):
    response = client.post("/api/v1/simulations", json={"users": 2, "pages": 1, "dpi": 200, **body})
    assert response.status_code in (400, 422)


def test_unknown_simulation_returns_404(client):
    assert client.get("/api/v1/simulations/missing").status_code == 404
