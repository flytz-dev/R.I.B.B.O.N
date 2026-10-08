"""Test SQLite audit history and report persistence."""

import pytest

from core import storage, engine


def _row(page, overall_status="OK", code="113640", amount="76,82", category="", **metrics):
    """Build a report row using the engine contract."""
    return {
        "Page": page,
        "Reference Line": page,
        "Code (Reference PDF)": code,
        "Code (OCR Slips)": code,
        "Code Status": "OK",
        "Amount (Reference PDF)": amount,
        "Amount (OCR Slips)": amount,
        "Amount Status": "OK",
        "Overall Status": overall_status,
        "Category": category,
        "Matched By": "CODE",
        "Code Confidence": 90,
        "Amount Confidence": 85,
        "Code Votes": 1,
        "Amount Votes": 1,
        "Strategies Run": 1,
        "Render ms": 400,
        "OCR ms": 600,
        "Review": "",
        **metrics,
    }


@pytest.fixture
def registered_audit():
    storage.register_audit("job1", "2026-08-15T18:00:00", 500, "payment_slips.pdf", "reference.pdf")
    return "job1"


def test_new_audit_has_no_pages(registered_audit):
    summary = storage.load_summary(registered_audit)

    assert summary["status"] == "processing"
    assert summary["processed_pages"] == 0
    assert summary["matched"] == 0
    assert summary["mismatched"] == 0
    assert summary["total_pages"] is None


def test_report_uses_engine_format(registered_audit):
    """Report uses engine format."""
    storage.save_page(registered_audit, _row(1))

    report = storage.load_report(registered_audit)

    assert len(report) == 1
    assert set(report[0]) == set(engine.REPORT_COLUMNS)
    assert report[0] == _row(1)


def test_pages_are_returned_in_order(registered_audit):
    for page in (3, 1, 2):
        storage.save_page(registered_audit, _row(page))

    report = storage.load_report(registered_audit)

    assert [row["Page"] for row in report] == [1, 2, 3]


def test_rewriting_page_does_not_duplicate_it(registered_audit):
    """Rewriting page does not duplicate it."""
    storage.save_page(registered_audit, _row(1, overall_status="ERROR"))
    storage.save_page(registered_audit, _row(1, overall_status="OK"))

    report = storage.load_report(registered_audit)

    assert len(report) == 1
    assert report[0]["Overall Status"] == "OK"


def test_summary_counts_matches_and_mismatches(registered_audit):
    storage.save_page(registered_audit, _row(1, "OK"))
    storage.save_page(registered_audit, _row(2, "OK"))
    storage.save_page(registered_audit, _row(3, "ERROR"))
    storage.save_page(registered_audit, _row(4, "END OF LIST"))

    summary = storage.load_summary(registered_audit)

    assert summary["processed_pages"] == 4
    assert summary["matched"] == 2
    assert summary["mismatched"] == 1


def test_status_update_preserves_page_count(registered_audit):
    storage.update_status(registered_audit, "processing", 50)
    storage.update_status(registered_audit, "completed")

    summary = storage.load_summary(registered_audit)

    assert summary["status"] == "completed"
    assert summary["total_pages"] == 50, "later updates must preserve the total"


def test_unknown_audit_returns_none():
    assert storage.load_summary("missing") is None
    assert storage.load_report("missing") == []


def test_history_is_newest_first():
    for number, timestamp in enumerate(["2026-08-10T09:00:00", "2026-08-15T09:00:00", "2026-08-12T09:00:00"]):
        storage.register_audit(f"job{number}", timestamp, 500, "b.pdf", "c.pdf")

    history = storage.list_audits()

    assert [item["job_id"] for item in history] == ["job1", "job2", "job0"]


def test_history_respects_limit():
    for number in range(10):
        storage.register_audit(f"job{number}", f"2026-08-{number + 1:02d}T09:00:00", 500, "b.pdf", "c.pdf")

    assert len(storage.list_audits(limit=3)) == 3


def test_deleting_audit_cascades_to_pages(registered_audit):
    """Deleting audit cascades to pages."""
    storage.save_page(registered_audit, _row(1))

    with storage._connection() as connection:
        connection.execute("DELETE FROM audit WHERE job_id = ?", (registered_audit,))

    assert storage.load_report(registered_audit) == []


# ==========================================
# Reviews, missing slips, and ownership
# ==========================================


def test_review_is_saved_and_cleared(registered_audit):
    storage.save_page(registered_audit, _row(1, "ERROR"))

    assert storage.set_review(registered_audit, 1, storage.REVIEW_OCR)
    assert storage.load_report(registered_audit)[0]["Review"] == "OCR"

    storage.set_review(registered_audit, 1, None)
    assert storage.load_report(registered_audit)[0]["Review"] == ""


def test_review_of_unknown_page_reports_failure(registered_audit):
    assert storage.set_review(registered_audit, 99, storage.REVIEW_DOCUMENT) is False


def test_unknown_review_verdict_is_rejected(registered_audit):
    with pytest.raises(ValueError):
        storage.set_review(registered_audit, 1, "MAYBE")


def test_finished_audit_keeps_duration_and_missing_slips(registered_audit):
    storage.finish_audit(registered_audit, 12.5, [{"line": 3, "code": "116110", "amount": "82,30"}])

    assert storage.load_summary(registered_audit)["duration_seconds"] == 12.5
    assert storage.load_missing(registered_audit) == [{"line": 3, "code": "116110", "amount": "82,30"}]


def test_history_by_owner_includes_legacy_audits_only():
    storage.register_audit("mine", "2026-10-01T09:00:00", 300, "b.pdf", "c.pdf", owner="ana")
    storage.register_audit("theirs", "2026-10-02T09:00:00", 300, "b.pdf", "c.pdf", owner="bruno")
    storage.register_audit("legacy", "2026-09-01T09:00:00", 500, "b.pdf", "c.pdf")

    visible = {item["job_id"] for item in storage.list_audits(owner="ana")}

    assert visible == {"mine", "legacy"}
    assert len(storage.list_audits()) == 3


def test_deleting_users_removes_their_audits():
    storage.create_user("sim-x-1", "hash", "2026-10-05T10:00:00", simulated=True)
    storage.register_audit("sim", "2026-10-05T10:00:00", 200, "b.pdf", "c.pdf", owner="sim-x-1")
    storage.save_page("sim", _row(1))

    storage.delete_users(["sim-x-1"])

    assert storage.load_summary("sim") is None
    assert storage.password_hash("sim-x-1") is None


# ==========================================
# Metrics
# ==========================================


def test_metrics_group_timing_and_reviews_by_resolution():
    storage.register_audit("fast", "2026-10-01T09:00:00", 200, "b.pdf", "c.pdf")
    storage.save_page("fast", _row(1, **{"Render ms": 100, "OCR ms": 300}))
    storage.save_page("fast", _row(2, "ERROR", **{"Review": "OCR", "Strategies Run": 8}))
    storage.save_page("fast", _row(3, "ERROR", **{"Review": "DOCUMENT"}))
    storage.save_page("fast", _row(4, "ERROR"))
    storage.finish_audit("fast", 8.0, [])

    storage.register_audit("slow", "2026-10-01T10:00:00", 500, "b.pdf", "c.pdf")
    storage.save_page("slow", _row(1, **{"Render ms": 900, "OCR ms": 2100}))

    metrics = {row["dpi"]: row for row in storage.metrics_by_dpi()}

    fast = metrics[200]
    assert fast["pages"] == 4
    assert fast["flagged"] == 3
    assert fast["ocr_errors"] == 1
    assert fast["document_discrepancies"] == 1
    assert fast["pending_review"] == 1
    assert fast["consensus_pages"] == 1
    assert fast["avg_render_ms"] == 325.0
    assert fast["wall_seconds_per_page"] == 2.0
    assert metrics[500]["avg_ocr_ms"] == 2100.0
    assert metrics[500]["wall_seconds_per_page"] is None, "audit without a recorded duration"


def test_metrics_exclude_simulated_users():
    storage.create_user("sim-x-1", "hash", "2026-10-05T10:00:00", simulated=True)
    storage.register_audit("sim", "2026-10-05T10:00:00", 200, "b.pdf", "c.pdf", owner="sim-x-1")
    storage.save_page("sim", _row(1))

    assert storage.metrics_by_dpi() == []


def test_legacy_pages_without_timing_are_counted_but_not_timed():
    storage.register_audit("old", "2026-08-01T10:00:00", 500, "b.pdf", "c.pdf")
    storage.save_page("old", _row(1, **{"Render ms": None, "OCR ms": None}))

    metrics = storage.metrics_by_dpi()[0]

    assert metrics["pages"] == 1
    assert metrics["timed_pages"] == 0
    assert metrics["avg_ocr_ms"] is None


# ==========================================
# Users and sessions
# ==========================================


def test_duplicate_username_is_rejected():
    import sqlite3

    storage.create_user("ana", "hash", "2026-10-05T10:00:00")
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_user("ana", "other", "2026-10-05T10:00:00")


def test_sessions_expire():
    storage.create_user("ana", "hash", "2026-10-05T10:00:00")
    storage.create_session("token", "ana", expires_at=100.0, now=50.0)

    assert storage.session_user("token", now=99.0) == "ana"
    assert storage.session_user("token", now=101.0) is None


def test_deleted_session_no_longer_authenticates():
    storage.create_user("ana", "hash", "2026-10-05T10:00:00")
    storage.create_session("token", "ana", expires_at=100.0, now=50.0)

    storage.delete_session("token")

    assert storage.session_user("token", now=60.0) is None
