"""SQLite audit history, user accounts, and metrics.

The API persists audit metadata and report rows, but not annotated images.
The OCR engine does not depend on this module."""

import contextlib
import os
import sqlite3

from core.migrations import migrate_legacy_schema, migrate_legacy_values

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The file keeps the project's first name so existing history stays where it was;
# PYCONFER_DB is still accepted for the same reason.
DATABASE_PATH = (
    os.environ.get("RIBBON_DB") or os.environ.get("PYCONFER_DB")
    or os.path.join(BASE_DIR, "pyconfer.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    job_id           TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    status           TEXT NOT NULL,
    dpi              INTEGER,
    payment_slips_file  TEXT,
    reference_file TEXT,
    total_pages    INTEGER,
    owner          TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS page (
    job_id        TEXT NOT NULL REFERENCES audit(job_id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,
    reference_line INTEGER,
    reference_code  TEXT,
    ocr_code       TEXT,
    code_status TEXT,
    reference_amount  TEXT,
    ocr_amount       TEXT,
    amount_status  TEXT,
    overall_status  TEXT,
    category      TEXT,
    matched_by    TEXT,
    code_confidence INTEGER,
    amount_confidence INTEGER,
    code_votes    INTEGER,
    amount_votes  INTEGER,
    strategies_run INTEGER,
    render_ms     INTEGER,
    ocr_ms        INTEGER,
    review        TEXT,
    PRIMARY KEY (job_id, page)
);

CREATE TABLE IF NOT EXISTS missing_slip (
    job_id         TEXT NOT NULL REFERENCES audit(job_id) ON DELETE CASCADE,
    reference_line INTEGER NOT NULL,
    code           TEXT,
    amount         TEXT,
    PRIMARY KEY (job_id, reference_line)
);

CREATE TABLE IF NOT EXISTS app_user (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    simulated     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES app_user(username) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_owner ON audit(owner);
"""

# Additive migrations for databases created by earlier versions.
ADDED_COLUMNS = (
    ("page", "category", "TEXT"),
    ("page", "reference_line", "INTEGER"),
    ("page", "matched_by", "TEXT"),
    ("page", "code_confidence", "INTEGER"),
    ("page", "amount_confidence", "INTEGER"),
    ("page", "code_votes", "INTEGER"),
    ("page", "amount_votes", "INTEGER"),
    ("page", "strategies_run", "INTEGER"),
    ("page", "render_ms", "INTEGER"),
    ("page", "ocr_ms", "INTEGER"),
    ("page", "review", "TEXT"),
    ("audit", "owner", "TEXT"),
    ("audit", "duration_seconds", "REAL"),
)

# Map public report labels to database column names.
_COLUMNS = (
    ("Page", "page"),
    ("Reference Line", "reference_line"),
    ("Code (Reference PDF)", "reference_code"),
    ("Code (OCR Slips)", "ocr_code"),
    ("Code Status", "code_status"),
    ("Amount (Reference PDF)", "reference_amount"),
    ("Amount (OCR Slips)", "ocr_amount"),
    ("Amount Status", "amount_status"),
    ("Overall Status", "overall_status"),
    ("Category", "category"),
    ("Matched By", "matched_by"),
    ("Code Confidence", "code_confidence"),
    ("Amount Confidence", "amount_confidence"),
    ("Code Votes", "code_votes"),
    ("Amount Votes", "amount_votes"),
    ("Strategies Run", "strategies_run"),
    ("Render ms", "render_ms"),
    ("OCR ms", "ocr_ms"),
    ("Review", "review"),
)

# Human verdicts on a flagged page.
REVIEW_DOCUMENT = "DOCUMENT"  # the document really differs from the reference
REVIEW_OCR = "OCR"            # the document is correct; OCR misread it
REVIEW_VERDICTS = (REVIEW_DOCUMENT, REVIEW_OCR)


def configure(path: str) -> None:
    """Select a database file, including temporary databases used by tests."""
    global DATABASE_PATH
    DATABASE_PATH = path


@contextlib.contextmanager
def _connection():
    """Open one connection per operation and enable foreign-key enforcement."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_schema() -> None:
    """Create the schema and apply migrations while preserving existing history."""
    with _connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        migrate_legacy_schema(connection)

        # Add columns to existing tables before creating indexes that reference them.
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, column, column_type in ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

        # execute() preserves the transaction; executescript() would commit it early.
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)

        migrate_legacy_values(connection)


# ==========================================
# Audits and report rows
# ==========================================

def register_audit(job_id, created_at, dpi, payment_slips_file, reference_file, owner=None) -> None:
    with _connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO audit "
            "(job_id, created_at, status, dpi, payment_slips_file, reference_file, total_pages, owner) "
            "VALUES (?, ?, 'processing', ?, ?, ?, NULL, ?)",
            (job_id, created_at, dpi, payment_slips_file, reference_file, owner),
        )


def update_status(job_id, status, total_pages=None) -> None:
    """Update the audit state and optionally the total page count."""
    with _connection() as connection:
        if total_pages is None:
            connection.execute("UPDATE audit SET status = ? WHERE job_id = ?", (status, job_id))
        else:
            connection.execute(
                "UPDATE audit SET status = ?, total_pages = ? WHERE job_id = ?",
                (status, total_pages, job_id),
            )


def finish_audit(job_id, duration_seconds, missing) -> None:
    """Record the wall-clock duration and reference entries without a slip."""
    with _connection() as connection:
        connection.execute(
            "UPDATE audit SET duration_seconds = ? WHERE job_id = ?", (duration_seconds, job_id)
        )
        connection.executemany(
            "INSERT OR REPLACE INTO missing_slip (job_id, reference_line, code, amount) VALUES (?, ?, ?, ?)",
            [(job_id, entry["line"], entry["code"], entry["amount"]) for entry in missing],
        )


def save_page(job_id, row: dict) -> None:
    """Persist each processed page so an interrupted audit retains earlier results."""
    values = [job_id] + [row.get(label) for label, _column in _COLUMNS]
    columns = ", ".join(column for _label, column in _COLUMNS)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    with _connection() as connection:
        connection.execute(
            f"INSERT OR REPLACE INTO page (job_id, {columns}) VALUES (?, {placeholders})",
            values,
        )


def set_review(job_id, page, verdict) -> bool:
    """Record or clear a reviewer's verdict; returns False when the page does not exist."""
    if verdict not in REVIEW_VERDICTS + (None,):
        raise ValueError(f"Unknown review verdict: {verdict}")
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE page SET review = ? WHERE job_id = ? AND page = ?", (verdict, job_id, page)
        )
    return cursor.rowcount > 0


def load_report(job_id) -> list[dict]:
    """Return ordered rows using the same keys as the stream and CSV."""
    columns = ", ".join(column for _label, column in _COLUMNS)
    with _connection() as connection:
        rows = connection.execute(
            f"SELECT {columns} FROM page WHERE job_id = ? ORDER BY page", (job_id,)
        ).fetchall()

    report = [{label: row[column] for label, column in _COLUMNS} for row in rows]
    for item in report:
        # Unreviewed pages read the same as in the live stream.
        item["Review"] = item["Review"] or ""
    return report


def load_missing(job_id) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT reference_line AS line, code, amount FROM missing_slip "
            "WHERE job_id = ? ORDER BY reference_line",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


_SUMMARY_QUERY = """
SELECT a.job_id,
       a.created_at,
       a.status,
       a.total_pages,
       a.dpi,
       a.owner,
       a.duration_seconds,
       COUNT(p.page)                                              AS processed_pages,
       COALESCE(SUM(p.overall_status = 'OK'), 0)                      AS matched,
       COALESCE(SUM(p.overall_status = 'ERROR'), 0)                    AS mismatched
  FROM audit a
  LEFT JOIN page p ON p.job_id = a.job_id
"""


def load_summary(job_id) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            _SUMMARY_QUERY + " WHERE a.job_id = ? GROUP BY a.job_id", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_audits(limit: int = 50, owner: str | None = None) -> list[dict]:
    """List audits newest first; with an owner, only theirs and unowned legacy audits."""
    where, parameters = "", []
    if owner is not None:
        where, parameters = " WHERE a.owner = ? OR a.owner IS NULL", [owner]
    with _connection() as connection:
        rows = connection.execute(
            _SUMMARY_QUERY + where + " GROUP BY a.job_id ORDER BY a.created_at DESC, a.rowid DESC LIMIT ?",
            (*parameters, limit),
        ).fetchall()
    return [dict(row) for row in rows]


# ==========================================
# Metrics
# ==========================================

# Simulated users exist only during the temporary multi-user demonstration.
_REAL_AUDITS = "COALESCE((SELECT simulated FROM app_user u WHERE u.username = a.owner), 0) = 0"

_PAGE_METRICS_QUERY = f"""
SELECT a.dpi,
       COUNT(DISTINCT a.job_id)                                   AS audits,
       COUNT(p.page)                                              AS pages,
       COUNT(p.ocr_ms)                                            AS timed_pages,
       AVG(p.render_ms)                                           AS avg_render_ms,
       AVG(p.ocr_ms)                                              AS avg_ocr_ms,
       COALESCE(SUM(p.strategies_run > 1), 0)                     AS consensus_pages,
       COALESCE(SUM(p.overall_status = 'OK'), 0)                  AS matched,
       COALESCE(SUM(p.overall_status = 'ERROR'), 0)               AS flagged,
       COALESCE(SUM(p.review = '{REVIEW_DOCUMENT}'), 0)           AS document_discrepancies,
       COALESCE(SUM(p.review = '{REVIEW_OCR}'), 0)                AS ocr_errors,
       COALESCE(SUM(p.overall_status = 'ERROR' AND COALESCE(p.review, '') = ''), 0) AS pending_review,
       AVG(p.code_confidence)                                     AS avg_code_confidence,
       AVG(p.amount_confidence)                                   AS avg_amount_confidence
  FROM audit a
  JOIN page p ON p.job_id = a.job_id
 WHERE {_REAL_AUDITS}
 GROUP BY a.dpi
 ORDER BY a.dpi
"""

# Wall-clock time per page uses only audits that finished and recorded their duration.
_WALL_TIME_QUERY = f"""
SELECT a.dpi,
       SUM(a.duration_seconds)                  AS seconds,
       SUM((SELECT COUNT(*) FROM page p WHERE p.job_id = a.job_id)) AS pages
  FROM audit a
 WHERE a.duration_seconds IS NOT NULL AND {_REAL_AUDITS}
 GROUP BY a.dpi
"""


def metrics_by_dpi() -> list[dict]:
    """Aggregate timing, discrepancies, and reviewer verdicts for each resolution."""
    with _connection() as connection:
        rows = [dict(row) for row in connection.execute(_PAGE_METRICS_QUERY)]
        wall = {row["dpi"]: dict(row) for row in connection.execute(_WALL_TIME_QUERY)}

    for row in rows:
        timing = wall.get(row["dpi"])
        row["wall_seconds_per_page"] = (
            timing["seconds"] / timing["pages"] if timing and timing["pages"] else None
        )
        for key in ("avg_render_ms", "avg_ocr_ms", "avg_code_confidence", "avg_amount_confidence"):
            if row[key] is not None:
                row[key] = round(row[key], 1)
    return rows


# ==========================================
# Users and sessions
# ==========================================

def create_user(username, password_hash, created_at, simulated=False) -> None:
    """Create an account; raises sqlite3.IntegrityError when the username exists."""
    with _connection() as connection:
        connection.execute(
            "INSERT INTO app_user (username, password_hash, created_at, simulated) VALUES (?, ?, ?, ?)",
            (username, password_hash, created_at, int(simulated)),
        )


def password_hash(username) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT password_hash FROM app_user WHERE username = ?", (username,)
        ).fetchone()
    return row["password_hash"] if row else None


def create_session(token_hash, username, expires_at, now) -> None:
    with _connection() as connection:
        connection.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO session (token_hash, username, expires_at) VALUES (?, ?, ?)",
            (token_hash, username, expires_at),
        )


def session_user(token_hash, now) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT username FROM session WHERE token_hash = ? AND expires_at > ?", (token_hash, now)
        ).fetchone()
    return row["username"] if row else None


def delete_session(token_hash) -> None:
    with _connection() as connection:
        connection.execute("DELETE FROM session WHERE token_hash = ?", (token_hash,))


def delete_users(usernames) -> None:
    """Remove accounts together with their audits, pages, and sessions."""
    usernames = list(usernames)
    if not usernames:
        return
    placeholders = ", ".join("?" for _ in usernames)
    with _connection() as connection:
        connection.execute(f"DELETE FROM audit WHERE owner IN ({placeholders})", usernames)
        connection.execute(f"DELETE FROM app_user WHERE username IN ({placeholders})", usernames)
