"""Exercise upgrades from actual legacy schema names using disposable databases."""

import sqlite3

import pytest

from core import storage


@pytest.fixture
def legacy_database(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", str(path))
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE auditoria (
                job_id TEXT PRIMARY KEY, criada_em TEXT NOT NULL, status TEXT NOT NULL,
                dpi INTEGER, arquivo_boletos TEXT, arquivo_consulta TEXT, total_paginas INTEGER
            );
            CREATE TABLE pagina (
                job_id TEXT NOT NULL REFERENCES auditoria(job_id) ON DELETE CASCADE,
                pagina INTEGER NOT NULL, cod_consulta TEXT, cod_ocr TEXT, status_codigo TEXT,
                val_consulta TEXT, val_ocr TEXT, status_valor TEXT, status_geral TEXT,
                PRIMARY KEY (job_id, pagina)
            );
        """)
    return path


@pytest.mark.parametrize("has_category", [False, True])
def test_legacy_history_survives_upgrade_and_repeated_startup(legacy_database, has_category):
    with sqlite3.connect(legacy_database) as connection:
        if has_category:
            connection.execute("ALTER TABLE pagina ADD COLUMN natureza TEXT")
        for index, state in enumerate(["concluida", "processando", "erro", "abandonada"]):
            connection.execute(
                "INSERT INTO auditoria VALUES (?, ?, ?, 500, 'original.pdf', 'reference.pdf', 4)",
                (f"job{index}", "2026-08-15T18:00:00", state),
            )
        for number, state in enumerate(["OK", "ERRO", "ERRO", "FIM DA LISTA"], start=1):
            connection.execute(
                "INSERT INTO pagina (job_id, pagina, cod_consulta, cod_ocr, status_codigo, "
                "val_consulta, val_ocr, status_valor, status_geral) "
                "VALUES ('job0', ?, '113640', '113840', 'DIFERENTE', '76,82', '99,99', 'DIFERENTE', ?)",
                (number, state),
            )
        if has_category:
            for number, category in enumerate(["", "OUTRO CADASTRO", "NAO LIDO", "VERIFICAR"], start=1):
                connection.execute("UPDATE pagina SET natureza = ? WHERE pagina = ?", (category, number))

    storage.create_schema()
    storage.create_schema()  # Startup must be idempotent.

    assert [storage.load_summary(f"job{i}")["status"] for i in range(4)] == [
        "completed", "processing", "error", "abandoned",
    ]
    summary = storage.load_summary("job0")
    assert (summary["processed_pages"], summary["matched"], summary["mismatched"]) == (4, 1, 2)
    report = storage.load_report("job0")
    assert [row["Overall Status"] for row in report] == ["OK", "ERROR", "ERROR", "END OF LIST"]
    assert report[1]["Code Status"] == "MISMATCH"
    assert report[1]["Amount Status"] == "MISMATCH"
    assert report[1]["Amount (Reference PDF)"] == "76,82"
    assert report[1]["Code (OCR Slips)"] == "113840"
    if has_category:
        assert [row["Category"] for row in report] == ["", "OTHER REGISTRATION", "UNREAD", "REVIEW"]
    else:
        assert all(row["Category"] is None for row in report)

    with storage._connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT payment_slips_file FROM audit WHERE job_id = 'job0'").fetchone()[0] == "original.pdf"
        connection.execute("DELETE FROM audit WHERE job_id = 'job0'")
    assert storage.load_report("job0") == []


def test_conflicting_schema_rolls_back_partial_renames(legacy_database):
    with sqlite3.connect(legacy_database) as connection:
        connection.execute("CREATE TABLE page (marker TEXT)")
        connection.execute("INSERT INTO auditoria VALUES ('saved', '2026-08-15', 'concluida', 500, NULL, NULL, 0)")

    with pytest.raises(sqlite3.DatabaseError, match="Both legacy and current tables"):
        storage.create_schema()

    with sqlite3.connect(legacy_database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == {"auditoria", "pagina", "page"}
        assert connection.execute("SELECT status FROM auditoria WHERE job_id = 'saved'").fetchone()[0] == "concluida"


def test_previous_english_schema_gains_metrics_and_ownership(tmp_path, monkeypatch):
    """Databases from the first English release keep their rows and gain the new columns."""
    path = tmp_path / "v1.db"
    monkeypatch.setattr(storage, "DATABASE_PATH", str(path))
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE audit (
                job_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, dpi INTEGER,
                payment_slips_file TEXT, reference_file TEXT, total_pages INTEGER
            );
            CREATE TABLE page (
                job_id TEXT NOT NULL REFERENCES audit(job_id) ON DELETE CASCADE, page INTEGER NOT NULL,
                reference_code TEXT, ocr_code TEXT, code_status TEXT, reference_amount TEXT, ocr_amount TEXT,
                amount_status TEXT, overall_status TEXT, category TEXT, PRIMARY KEY (job_id, page)
            );
            INSERT INTO audit VALUES ('old', '2026-09-30T10:00:00', 'completed', 500, 'b.pdf', 'c.pdf', 1);
            INSERT INTO page VALUES ('old', 1, '113640', '113640', 'OK', '76,82', '76,82', 'OK', 'OK', '');
        """)

    storage.create_schema()

    row = storage.load_report("old")[0]
    assert row["Overall Status"] == "OK"
    assert row["OCR ms"] is None and row["Review"] == ""
    assert storage.load_summary("old")["owner"] is None
    assert [item["job_id"] for item in storage.list_audits(owner="anyone")] == ["old"]
