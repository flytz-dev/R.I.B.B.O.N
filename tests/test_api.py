"""Test API validation, streaming lifecycle, and memory limits."""

import queue
import time

import pytest
from fastapi.testclient import TestClient

from api import auth, main
from core import storage

USER = "tester"


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the global in-memory audit registry between tests."""
    main.AUDITS.clear()
    yield
    main.AUDITS.clear()
    main.app.dependency_overrides.clear()


@pytest.fixture
def client():
    """A client signed in as USER, bypassing the session cookie."""
    main.app.dependency_overrides[auth.current_user] = lambda: USER
    return TestClient(main.app)


@pytest.fixture
def anonymous():
    """A client using the real session-cookie authentication."""
    return TestClient(main.app)


def _audit(status, age_seconds=0.0, **extras):
    return {
        "status": status,
        "started_at": time.monotonic() - age_seconds,
        **extras,
    }


# ==========================================
# Queue cleanup
# ==========================================


def test_draining_queue_releases_pending_events():
    event_queue = queue.Queue(maxsize=4)
    for number in range(4):
        event_queue.put_nowait({"page": number, "image": "x" * 130_000})

    main._drain_queue(event_queue)

    assert event_queue.empty()


def test_shutdown_does_not_block_on_full_queue():
    """Shutdown does not block on full queue."""
    event_queue = queue.Queue(maxsize=3)
    for number in range(3):
        event_queue.put_nowait(number)

    start = time.monotonic()
    main._publish_shutdown(event_queue, main._SENTINEL)
    assert time.monotonic() - start < 1, "must not block waiting for space"

    assert event_queue.get_nowait() is main._SENTINEL
    assert event_queue.empty()


# ==========================================
# Registry expiration
# ==========================================


def test_finished_audit_expires_by_age():
    main.AUDITS.update({
        "old": _audit("completed", main.AUDIT_TTL_SECONDS + 60),
        "recent": _audit("completed"),
    })

    main._purge_old_audits()

    assert set(main.AUDITS) == {"recent"}


@pytest.mark.parametrize("status", ["completed", "error", "abandoned"])
def test_all_finished_states_expire(status):
    main.AUDITS["oldest"] = _audit(status, main.AUDIT_TTL_SECONDS + 60)

    main._purge_old_audits()

    assert main.AUDITS == {}


def test_running_audit_is_never_discarded():
    """Running audit is never discarded."""
    main.AUDITS["running"] = _audit("processing", age_seconds=999_999)

    main._purge_old_audits()

    assert "running" in main.AUDITS


def test_registry_limit_keeps_recent_audits():
    # Future timestamps isolate count limits from TTL expiration.
    for number in range(main.MAX_AUDITS + 10):
        main.AUDITS[f"job{number:03d}"] = _audit("completed", age_seconds=-number)

    main._purge_old_audits()

    assert len(main.AUDITS) == main.MAX_AUDITS
    assert "job000" not in main.AUDITS, "oldest audit should have been removed"
    assert f"job{main.MAX_AUDITS + 9:03d}" in main.AUDITS, "newest audit should remain"


def test_registry_limit_preserves_running_audits():
    for number in range(main.MAX_AUDITS + 10):
        main.AUDITS[f"live{number:03d}"] = _audit("processing", age_seconds=-number)

    main._purge_old_audits()

    assert len(main.AUDITS) == main.MAX_AUDITS + 10


# ==========================================
# Abandoned browser sessions
# ==========================================


def test_audit_without_consumer_is_abandoned(monkeypatch, tmp_path):
    """Audit without consumer is abandoned."""
    monkeypatch.setattr(main, "CONSUMER_TIMEOUT_SECONDS", 0.2)

    def fake_engine(*_args, **_kwargs):
        yield {"type": "start", "total_pages": 500, "reference_count": 500}
        for number in range(1, 501):
            yield {
                "type": "page",
                "page": number,
                "row": {"Overall Status": "OK"},
                "image": "x" * 130_000,
            }

    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)

    main.AUDITS["orphan"] = _audit(
        "processing",
        event_queue=queue.Queue(maxsize=main.MAX_QUEUE_SIZE),
        report=[],
        total_pages=None,
        matched=0,
        mismatched=0,
        created_at="",
    )
    temp_directory = tmp_path / "job"
    temp_directory.mkdir()

    main._execute_audit("orphan", "payment_slips.pdf", "reference.pdf", 200, str(temp_directory))

    audit = main.AUDITS["orphan"]
    assert audit["status"] == "abandoned"
    assert len(audit["report"]) < 500, "should stop well before the end"
    assert audit["event_queue"].get_nowait() is main._SENTINEL
    assert audit["event_queue"].empty(), "pending images should have been released"
    assert not temp_directory.exists(), "temporary directory should have been removed"


# ==========================================
# Endpoints
# ==========================================


@pytest.mark.parametrize("path", [
    "/api/v1/audits/missing",
    "/api/v1/audits/missing/events",
    "/api/v1/audits/missing/report.csv",
])
def test_unknown_job_returns_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Audit not found."


def test_post_returns_only_job_id_and_message(client):
    """Post returns only job id and message."""
    response = client.post(
        "/api/v1/audits",
        files={
            "payment_slips": ("payment_slips.pdf", b"%PDF-1.4", "application/pdf"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {"job_id", "message"}


def test_non_pdf_upload_is_rejected(client):
    response = client.post(
        "/api/v1/audits",
        files={
            "payment_slips": ("spreadsheet.xlsx", b"not a pdf", "application/vnd.ms-excel"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )

    assert response.status_code == 400
    assert "spreadsheet.xlsx" in response.json()["detail"]


def test_report_before_first_page_returns_409(client):
    main.AUDITS["newly_created"] = _audit("processing", report=[])

    response = client.get("/api/v1/audits/newly_created/report.csv")

    assert response.status_code == 409


def test_expired_audit_is_available_from_history(client):
    """Expired audit is available from history."""
    storage.register_audit("old", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    storage.update_status("old", "completed", 2)
    for page, statusText in ((1, "OK"), (2, "ERROR")):
        storage.save_page("old", {
            "Page": page,
            "Code (Reference PDF)": "113640",
            "Code (OCR Slips)": "113640",
            "Code Status": "OK",
            "Amount (Reference PDF)": "76,82",
            "Amount (OCR Slips)": "76,82",
            "Amount Status": "OK",
            "Overall Status": statusText,
        })

    assert "old" not in main.AUDITS, "audit must be absent from memory"

    summary = client.get("/api/v1/audits/old").json()
    assert summary["status"] == "completed"
    assert summary["processed_pages"] == 2
    assert summary["matched"] == 1
    assert summary["mismatched"] == 1

    csv = client.get("/api/v1/audits/old/report.csv")
    assert csv.status_code == 200
    assert "113640" in csv.text


def test_old_audit_pages_come_from_history(client):
    """Old audit pages come from history."""
    storage.register_audit("past", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    storage.save_page("past", {
        "Page": 47,
        "Code (Reference PDF)": "116110",
        "Code (OCR Slips)": "716110",
        "Code Status": "MISMATCH",
        "Amount (Reference PDF)": "76,82",
        "Amount (OCR Slips)": "76,82",
        "Amount Status": "OK",
        "Overall Status": "ERROR",
    })

    rows = client.get("/api/v1/audits/past/pages").json()

    assert len(rows) == 1
    assert rows[0]["Page"] == 47
    assert rows[0]["Code (OCR Slips)"] == "716110"
    assert "image" not in rows[0], "annotated images are not persisted"


def test_live_audit_pages_come_from_memory(client):
    main.AUDITS["live"] = _audit("processing", report=[{"Page": 1}])

    assert client.get("/api/v1/audits/live/pages").json() == [{"Page": 1}]


def test_unknown_job_pages_return_404(client):
    assert client.get("/api/v1/audits/missing/pages").status_code == 404


def test_front_end_requires_cache_revalidation(client):
    """Front end requires cache revalidation."""
    response = client.get("/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_history_lists_audits(client):
    storage.register_audit("j1", "2026-08-10T09:00:00", 500, "b.pdf", "c.pdf")
    storage.register_audit("j2", "2026-08-15T09:00:00", 300, "b.pdf", "c.pdf")

    history = client.get("/api/v1/audits").json()

    assert [item["job_id"] for item in history] == ["j2", "j1"]


def test_memory_takes_priority_over_history(client):
    """Memory takes priority over history."""
    storage.register_audit("live", "2026-08-15T18:00:00", 500, "b.pdf", "c.pdf")
    main.AUDITS["live"] = _audit(
        "processing",
        report=[{"Page": 1}],
        total_pages=50,
        matched=1,
        mismatched=0,
        created_at="2026-08-15T18:00:00",
    )

    summary = client.get("/api/v1/audits/live").json()

    assert summary["processed_pages"] == 1, "must not use the database that has no pages yet"
    assert summary["total_pages"] == 50


def test_summary_reflects_progress(client):
    main.AUDITS["ongoing"] = _audit(
        "processing",
        report=[{"Page": 1}, {"Page": 2}],
        total_pages=50,
        matched=2,
        mismatched=0,
        created_at="2026-08-15T18:43:51",
    )

    body = client.get("/api/v1/audits/ongoing").json()

    assert body["processed_pages"] == 2
    assert body["total_pages"] == 50
    assert body["matched"] == 2
    assert body["status"] == "processing"


# ==========================================
# Authentication
# ==========================================


@pytest.fixture
def fast_hashing(monkeypatch):
    """Keep password hashing cheap in tests; production uses the full iteration count."""
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1_000)


@pytest.mark.parametrize("method, path", [
    ("get", "/api/v1/audits"),
    ("get", "/api/v1/metrics"),
    ("get", "/api/v1/auth/me"),
    ("post", "/api/v1/simulations"),
])
def test_api_requires_sign_in(anonymous, method, path):
    response = getattr(anonymous, method)(path)
    assert response.status_code == 401


def test_register_sign_out_and_sign_in(anonymous, fast_hashing):
    credentials = {"username": "ana.silva", "password": "correct horse"}

    assert anonymous.post("/api/v1/auth/register", json=credentials).json() == {"username": "ana.silva"}
    assert anonymous.get("/api/v1/auth/me").json() == {"username": "ana.silva"}
    cookie = anonymous.cookies.get(auth.SESSION_COOKIE)

    assert anonymous.post("/api/v1/auth/logout").status_code == 204
    anonymous.cookies.set(auth.SESSION_COOKIE, cookie)
    assert anonymous.get("/api/v1/auth/me").status_code == 401, "logout must revoke the session"

    anonymous.cookies.clear()
    assert anonymous.post("/api/v1/auth/login", json=credentials).status_code == 200
    assert anonymous.get("/api/v1/auth/me").json() == {"username": "ana.silva"}


def test_session_cookie_is_http_only(anonymous, fast_hashing):
    response = anonymous.post("/api/v1/auth/register", json={"username": "ana", "password": "12345678"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_password_is_not_stored_in_plain_text(anonymous, fast_hashing):
    anonymous.post("/api/v1/auth/register", json={"username": "ana", "password": "12345678"})
    stored = storage.password_hash("ana")
    assert "12345678" not in stored
    assert stored.startswith("pbkdf2_sha256$")


@pytest.mark.parametrize("password", ["wrong password", ""])
def test_wrong_password_is_rejected(anonymous, fast_hashing, password):
    anonymous.post("/api/v1/auth/register", json={"username": "ana", "password": "12345678"})
    anonymous.cookies.clear()

    response = anonymous.post("/api/v1/auth/login", json={"username": "ana", "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_unknown_user_gets_the_same_error(anonymous, fast_hashing):
    response = anonymous.post("/api/v1/auth/login", json={"username": "nobody", "password": "12345678"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


@pytest.mark.parametrize("username, password, message", [
    ("ab", "12345678", "3 to 32"),
    ("ana silva", "12345678", "3 to 32"),
    ("ana", "short", "at least 8"),
    ("sim-ana", "12345678", "reserved"),
])
def test_invalid_registrations_are_rejected(anonymous, fast_hashing, username, password, message):
    response = anonymous.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_username_must_be_unique(anonymous, fast_hashing):
    anonymous.post("/api/v1/auth/register", json={"username": "ana", "password": "12345678"})
    response = anonymous.post("/api/v1/auth/register", json={"username": "ana", "password": "87654321"})
    assert response.status_code == 400
    assert "taken" in response.json()["detail"]


def test_api_no_longer_allows_cross_origin_credentials(anonymous):
    response = anonymous.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# ==========================================
# Ownership
# ==========================================


def test_users_cannot_open_other_users_audits(client):
    storage.register_audit("theirs", "2026-10-01T09:00:00", 300, "b.pdf", "c.pdf", owner="someone-else")
    storage.save_page("theirs", {"Page": 1, "Overall Status": "OK"})
    main.AUDITS["live-theirs"] = _audit("processing", report=[], owner="someone-else")

    for path in (
        "/api/v1/audits/theirs", "/api/v1/audits/theirs/pages", "/api/v1/audits/theirs/report.csv",
        "/api/v1/audits/theirs/missing", "/api/v1/audits/live-theirs", "/api/v1/audits/live-theirs/events",
    ):
        assert client.get(path).status_code == 404, path
    assert client.put("/api/v1/audits/theirs/pages/1/review", json={"verdict": "OCR"}).status_code == 404
    assert client.get("/api/v1/audits").json() == []


def test_new_audit_belongs_to_the_signed_in_user(client):
    response = client.post(
        "/api/v1/audits",
        files={
            "payment_slips": ("payment_slips.pdf", b"%PDF-1.4", "application/pdf"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )
    job_id = response.json()["job_id"]
    assert main.AUDITS[job_id]["owner"] == USER
    assert storage.load_summary(job_id)["owner"] == USER


@pytest.mark.parametrize("dpi", [50, 1200])
def test_out_of_range_resolution_is_rejected(client, dpi):
    response = client.post(
        f"/api/v1/audits?dpi={dpi}",
        files={
            "payment_slips": ("payment_slips.pdf", b"%PDF-1.4", "application/pdf"),
            "reference": ("reference.pdf", b"%PDF-1.4", "application/pdf"),
        },
    )
    assert response.status_code == 422


# ==========================================
# Review, missing slips, and metrics
# ==========================================


def _stored_page(job_id, page, status="ERROR", owner=USER, **extra):
    if not storage.load_summary(job_id):
        storage.register_audit(job_id, "2026-10-01T09:00:00", 300, "b.pdf", "c.pdf", owner=owner)
    storage.save_page(job_id, {
        "Page": page, "Code (Reference PDF)": "113640", "Code (OCR Slips)": "113840",
        "Overall Status": status, "Render ms": 100, "OCR ms": 400, **extra,
    })


def test_review_updates_stored_report(client):
    _stored_page("past", 1)

    response = client.put("/api/v1/audits/past/pages/1/review", json={"verdict": "OCR"})

    assert response.status_code == 200
    assert response.json()["Review"] == "OCR"
    assert storage.load_report("past")[0]["Review"] == "OCR"


def test_review_updates_live_report_and_history(client):
    _stored_page("live", 1)
    main.AUDITS["live"] = _audit("processing", report=[{"Page": 1, "Review": ""}], owner=USER)

    response = client.put("/api/v1/audits/live/pages/1/review", json={"verdict": "DOCUMENT"})

    assert response.json()["Review"] == "DOCUMENT"
    assert main.AUDITS["live"]["report"][0]["Review"] == "DOCUMENT"
    assert storage.load_report("live")[0]["Review"] == "DOCUMENT"


def test_review_can_be_cleared(client):
    _stored_page("past", 1, Review="OCR")
    response = client.put("/api/v1/audits/past/pages/1/review", json={"verdict": None})
    assert response.json()["Review"] == ""


@pytest.mark.parametrize("page, verdict, status", [(1, "MAYBE", 400), (7, "OCR", 404)])
def test_invalid_reviews_are_rejected(client, page, verdict, status):
    _stored_page("past", 1)
    assert client.put(f"/api/v1/audits/past/pages/{page}/review", json={"verdict": verdict}).status_code == status


def test_missing_slips_come_from_history_and_csv(client):
    _stored_page("past", 1, status="OK")
    storage.finish_audit("past", 3.0, [{"line": 2, "code": "116110", "amount": "82,30"}])

    assert client.get("/api/v1/audits/past/missing").json() == [{"line": 2, "code": "116110", "amount": "82,30"}]
    csv_text = client.get("/api/v1/audits/past/report.csv").text
    assert "MISSING SLIP" in csv_text
    assert "116110" in csv_text


def test_metrics_endpoint_reports_each_resolution(client):
    _stored_page("past", 1, Review="OCR")
    _stored_page("past", 2, status="OK")

    metrics = client.get("/api/v1/metrics").json()

    assert len(metrics) == 1
    assert metrics[0]["dpi"] == 300
    assert metrics[0]["pages"] == 2
    assert metrics[0]["ocr_errors"] == 1
    assert metrics[0]["avg_ocr_ms"] == 400.0


def test_history_shows_resolution_and_duration(client):
    _stored_page("past", 1, status="OK")
    storage.finish_audit("past", 4.5, [])

    entry = client.get("/api/v1/audits").json()[0]

    assert entry["dpi"] == 300
    assert entry["duration_seconds"] == 4.5


def test_finished_audit_records_duration_and_missing_slips(monkeypatch, tmp_path):
    def fake_engine(*_args, **_kwargs):
        yield {"type": "start", "total_pages": 1, "reference_count": 2}
        yield {"type": "page", "page": 1, "row": {"Page": 1, "Overall Status": "OK"}}
        yield {"type": "end", "total_pages": 1, "elapsed_seconds": 2.5,
               "missing": [{"line": 2, "code": "116110", "amount": "82,30"}]}

    monkeypatch.setattr(main.engine, "stream_audit", fake_engine)
    storage.register_audit("done", "2026-10-01T09:00:00", 300, "b.pdf", "c.pdf", owner=USER)
    main.AUDITS["done"] = _audit(
        "processing", event_queue=queue.Queue(maxsize=main.MAX_QUEUE_SIZE), report=[],
        total_pages=None, matched=0, mismatched=0, created_at="", owner=USER,
    )

    main._execute_audit("done", "slips.pdf", "reference.pdf", 300, str(tmp_path))

    assert main.AUDITS["done"]["missing"][0]["line"] == 2
    assert storage.load_summary("done")["duration_seconds"] == 2.5
    assert storage.load_missing("done")[0]["code"] == "116110"
