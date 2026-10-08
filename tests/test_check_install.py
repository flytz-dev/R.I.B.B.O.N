"""Test the installation check used by setup.bat, without a real OCR toolchain."""

import pytest

from core import engine
from tools import check_install


def _page(status):
    return {"type": "page", "row": {"Overall Status": status}}


@pytest.fixture
def toolchain(monkeypatch):
    """Simulate a working Tesseract; tests override one piece at a time."""
    monkeypatch.setattr(engine.pytesseract, "get_tesseract_version", lambda: "5.5.0")
    monkeypatch.setattr(engine.pytesseract, "get_languages", lambda config="": ["eng", "osd"])
    monkeypatch.setattr(engine, "stream_audit", lambda *args, **kwargs: iter([_page("OK")]))
    return monkeypatch


def test_working_installation_passes(toolchain, capsys):
    assert check_install.main() == 0
    assert "OCR test passed" in capsys.readouterr().out


def test_missing_tesseract_fails(toolchain, capsys):
    def missing():
        raise OSError("tesseract is not installed")

    toolchain.setattr(engine.pytesseract, "get_tesseract_version", missing)

    assert check_install.main() == 1
    assert "Tesseract is not usable" in capsys.readouterr().out


def test_missing_english_data_fails(toolchain, capsys):
    toolchain.setattr(engine.pytesseract, "get_languages", lambda config="": ["osd"])

    assert check_install.main() == 1
    assert "eng.traineddata" in capsys.readouterr().out


def test_poppler_failure_is_reported(toolchain, capsys):
    def no_poppler(*_args, **_kwargs):
        raise RuntimeError("Unable to get page count. Is poppler installed and in PATH?")

    toolchain.setattr(engine, "stream_audit", no_poppler)

    assert check_install.main() == 1
    assert "Poppler" in capsys.readouterr().out


def test_wrong_reading_fails(toolchain, capsys):
    toolchain.setattr(engine, "stream_audit", lambda *args, **kwargs: iter([_page("ERROR")]))

    assert check_install.main() == 1
    assert "did not read the test slip" in capsys.readouterr().out
