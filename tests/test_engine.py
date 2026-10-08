"""Test registration comparison, reference matching, OCR consensus, and reference parsing.

Known parser limitations are recorded explicitly to make behavior changes visible."""

import pytest

from core import engine

# ==========================================
# code_status
# ==========================================


@pytest.mark.parametrize("read, expected", [
    ("116530", "116530"),
    ("113640", "113640"),
    ("59750", "59750"),      # five-digit suffix
])
def test_identical_code_matches(read, expected):
    assert engine.code_status(read, expected) == "OK"


@pytest.mark.parametrize("read, expected", [
    ("113640", "113610"),   # 4 -> 1
    ("61990", "61890"),     # 9 -> 8
    ("116530", "116230"),   # 5 -> 2
    ("60700", "60900"),     # 7 -> 9
])
def test_unrelated_digit_changes_are_flagged(read, expected):
    assert engine.code_status(read, expected) == "MISMATCH"


@pytest.mark.parametrize("read, expected, reason", [
    ("0", "116530", "OCR could not read the slip"),
    ("", "116530", "empty reading"),
    ("116530", "N/A", "reference list ended before the slips"),
    ("116530", "", "empty expected value"),
])
def test_invalid_pairs_do_not_match(read, expected, reason):
    assert engine.code_status(read, expected) == "MISMATCH", reason


def test_codes_without_trailing_zero_do_not_match():
    """Codes without trailing zero do not match."""
    assert engine.code_status("12345", "12345") == "MISMATCH"


@pytest.mark.parametrize("read, expected, confusion", [
    ("116110", "116170", "1 read as 7"),
    ("113640", "113840", "6 read as 8"),
    ("119000", "119090", "0 read as 9"),
    ("116110", "116770", "two 1s read as 7"),
])
def test_ocr_digit_confusions_are_flagged(read, expected, confusion):
    """Ocr digit confusions are flagged."""
    assert engine.code_status(read, expected) == "MISMATCH", confusion


def test_distinct_registrations_never_match():
    """Distinct registrations never match."""
    from itertools import combinations

    codes = [
        "113640", "113840", "116110", "116170", "116770", "116250", "118250",
        "116700", "118700", "116810", "118870", "119000", "119090", "118680",
        "118860", "118880", "117510", "117570",
    ]
    collisions = [
        (a, b) for a, b in combinations(codes, 2)
        if engine.code_status(a, b) == "OK"
    ]
    assert collisions == []


# ==========================================
# classify_discrepancy
# ==========================================


def _result(code="115870", amount="76,82", code_status="OK", amount_status="OK", overall_status="ERROR"):
    return {
        "code": code,
        "amount": amount,
        "code_status": code_status,
        "amount_status": amount_status,
        "overall_status": overall_status,
    }


def test_matching_page_has_no_category():
    result = _result(overall_status="OK")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_CODE) == ""


def test_duplicate_is_prioritized():
    """A registration matched twice is reported as a duplicate, even with an unread amount."""
    result = _result(amount="0,00", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_DUPLICATE) == engine.CATEGORY_DUPLICATE


@pytest.mark.parametrize("code", ["0", "", None])
def test_missing_code_is_unread(code):
    result = _result(code=code, code_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_POSITION) == engine.CATEGORY_UNREAD


def test_unknown_code_with_matching_amount_is_likely_misread():
    """Wrong code, right amount: the slip expected here was probably misread."""
    result = _result(code="716110", code_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_POSITION) == engine.CATEGORY_LIKELY_MISREAD


def test_unknown_code_and_different_amount_requires_review():
    result = _result(code="716110", code_status="MISMATCH", amount="99,99", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_POSITION) == engine.CATEGORY_REVIEW


def test_missing_amount_with_matching_code_is_unread():
    result = _result(amount="0,00", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_CODE) == engine.CATEGORY_UNREAD


def test_amount_mismatch_requires_review():
    result = _result(amount="99,99", amount_status="MISMATCH")
    assert engine.classify_discrepancy(result, engine.MATCHED_BY_CODE) == engine.CATEGORY_REVIEW


# ==========================================
# ReferenceMatcher
# ==========================================

ENTRIES = [
    {"code": "113640", "amount": "76,82"},
    {"code": "116110", "amount": "82,30"},
    {"code": "118870", "amount": "76,82"},
    {"code": "59750", "amount": "64,15"},
]


def _pair(codes, entries=ENTRIES):
    """Match a sequence of OCR codes and return (reference line or None, matched_by) per page."""
    matcher = engine.ReferenceMatcher(entries)
    pairs = []
    for code in codes:
        index, matched_by = matcher.match(code)
        pairs.append((index + 1 if index is not None else None, matched_by))
    return pairs, [entry["line"] for entry in matcher.missing()]


def test_slips_in_reference_order_match_by_code():
    pairs, missing = _pair(["113640", "116110", "118870", "59750"])
    assert pairs == [(1, "CODE"), (2, "CODE"), (3, "CODE"), (4, "CODE")]
    assert missing == []


def test_missing_slip_does_not_shift_later_pages():
    """With positional matching, every page after a missing slip would mismatch."""
    pairs, missing = _pair(["113640", "118870", "59750"])
    assert pairs == [(1, "CODE"), (3, "CODE"), (4, "CODE")]
    assert missing == [2]


def test_swapped_slips_match_their_own_entries():
    pairs, missing = _pair(["116110", "113640", "118870", "59750"])
    assert [line for line, _ in pairs] == [2, 1, 3, 4]
    assert missing == []


def test_unreadable_code_falls_back_to_expected_position():
    pairs, _missing = _pair(["113640", "0", "118870"])
    assert pairs[1] == (2, "POSITION")


def test_misread_code_falls_back_to_expected_position():
    """Code wrong, amount right: the slip is still compared with the entry expected there."""
    pairs, missing = _pair(["113640", "716110", "118870", "59750"])
    assert pairs[1] == (2, "POSITION")
    assert missing == []


def test_repeated_registration_is_a_duplicate():
    pairs, missing = _pair(["113640", "113640", "116110"])
    assert pairs[1] == (1, "DUPLICATE")
    assert pairs[2] == (2, "CODE"), "a duplicate must not consume the expected position"
    assert missing == [3, 4]


def test_extra_slip_after_the_list_ends():
    pairs, _missing = _pair(["113640", "116110", "118870", "59750", "999990"])
    assert pairs[-1] == (None, "POSITION")


def test_positional_fallback_reuses_skipped_entries_at_the_end():
    pairs, missing = _pair(["116110", "118870", "59750", "0"])
    assert pairs[-1] == (1, "POSITION")
    assert missing == []


def test_repeated_codes_in_reference_are_used_in_order():
    entries = [{"code": "113640", "amount": "76,82"}, {"code": "113640", "amount": "82,30"}]
    pairs, missing = _pair(["113640", "113640"], entries)
    assert pairs == [(1, "CODE"), (2, "CODE")]
    assert missing == []


def test_entries_without_code_are_never_paired_or_missing():
    entries = [{"code": "113640", "amount": "76,82"}, {"code": "N/A", "amount": "99,99"}]
    pairs, missing = _pair(["113640", "0"], entries)
    assert pairs[1] == (None, "POSITION")
    assert missing == []


def test_known_pairs_ignore_order_and_usage():
    matcher = engine.ReferenceMatcher(ENTRIES)
    matcher.match("116110")
    assert matcher.is_known("116110", "82,30"), "the fast path must not depend on matching state"
    assert not matcher.is_known("116110", "76,82")
    assert not matcher.is_known("0", "76,82")


@pytest.mark.parametrize("reading, entry, matched_by, expected", [
    ({"code": "113640", "amount": "76,82"}, ENTRIES[0], "CODE", ("OK", "OK", "OK")),
    ({"code": "113640", "amount": "99,99"}, ENTRIES[0], "CODE", ("OK", "MISMATCH", "ERROR")),
    ({"code": "113640", "amount": "76,82"}, ENTRIES[0], "DUPLICATE", ("OK", "OK", "ERROR")),
    ({"code": "113640", "amount": "76,82"}, None, "POSITION", ("MISMATCH", "MISMATCH", "END OF LIST")),
])
def test_compare_reading(reading, entry, matched_by, expected):
    assert engine.compare_reading(reading, entry, matched_by) == expected


# ==========================================
# OCR reading, confidence, and consensus
# ==========================================


def _tesseract_data(*words):
    """Build an image_to_data dictionary from (text, confidence, left) tuples."""
    return {
        "text": [text for text, _conf, _left in words],
        "conf": [conf for _text, conf, _left in words],
        "left": [left for _text, _conf, left in words],
        "top": [10 for _ in words],
        "width": [50 for _ in words],
        "height": [20 for _ in words],
    }


def test_fields_carry_confidence_and_location(monkeypatch):
    data = _tesseract_data(("", -1, 0), ("0000000000113640", 91.4, 5), ("76,82", "88", 300))
    monkeypatch.setattr(engine.pytesseract, "image_to_data", lambda *args, **kwargs: data)

    fields = engine.extract_image_fields(object())

    assert fields["code"] == "113640"
    assert fields["code_conf"] == 91
    assert fields["code_box"] == (5, 10, 55, 30)
    assert fields["amount"] == "76,82"
    assert fields["amount_conf"] == 88
    assert fields["amount_box"] == (300, 10, 350, 30)


def test_invalid_numeric_line_is_skipped_for_a_later_one(monkeypatch):
    data = _tesseract_data(("1234567890123457", 90, 0), ("0000000000113640", 80, 0))
    monkeypatch.setattr(engine.pytesseract, "image_to_data", lambda *args, **kwargs: data)
    assert engine.extract_image_fields(object())["code"] == "113640"


def test_tesseract_failure_returns_empty_fields(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("tesseract not found")

    monkeypatch.setattr(engine.pytesseract, "image_to_data", fail)
    fields = engine.extract_image_fields(object())
    assert fields["code"] is None and fields["amount"] is None


def _fields(code=None, amount=None, conf=80):
    return {"code": code, "amount": amount, "code_conf": conf, "amount_conf": conf,
            "code_box": (0, 0, 1, 1), "amount_box": (2, 2, 3, 3)}


def test_vote_counts_agreeing_strategies_and_averages_confidence():
    readings = [
        (0, _fields("113840", "76,82", conf=40)),
        (1, _fields("113640", "76,82", conf=90)),
        (2, _fields("113640", None, conf=70)),
    ]

    result = engine.vote_readings(readings)

    assert result["code"] == "113640"
    assert result["code_votes"] == 2
    assert result["code_strategy"] == 1
    assert result["code_conf"] == 80
    assert result["amount_votes"] == 2
    assert result["strategies_run"] == 3


def test_vote_without_readings_returns_sentinels():
    result = engine.vote_readings([(0, _fields())])
    assert result["code"] == engine.EMPTY_CODE
    assert result["amount"] == engine.EMPTY_AMOUNT
    assert result["code_votes"] == 0
    assert result["code_box"] is None


def _fake_strategies(monkeypatch, readings):
    """Make strategy i return readings[i]; returns a blank page and the strategies called."""
    from PIL import Image

    calls = []

    def fake_read(_img, index):
        calls.append(index)
        return readings[index] if index < len(readings) else _fields()

    monkeypatch.setattr(engine, "_read_with_strategy", fake_read)
    return Image.new("L", (10, 10)), calls


def test_known_first_reading_skips_consensus(monkeypatch):
    image, calls = _fake_strategies(monkeypatch, [_fields("113640", "76,82")])

    result = engine.read_page(image, lambda code, amount: (code, amount) == ("113640", "76,82"))

    assert calls == [0]
    assert result["strategies_run"] == 1
    assert result["code_votes"] == 1


def test_unknown_first_reading_runs_all_strategies(monkeypatch):
    readings = [_fields("113840", "76,82")] + [_fields("113640", "76,82")] * 7
    image, calls = _fake_strategies(monkeypatch, readings)

    result = engine.read_page(image, lambda code, amount: (code, amount) == ("113640", "76,82"))

    assert calls == list(range(8))
    assert result["code"] == "113640"
    assert result["code_votes"] == 7


def test_strategy_coordinates_map_back_to_the_original_page(monkeypatch):
    from PIL import Image

    zoom = [name for name, _apply in engine.STRATEGIES].index("5. Zoom 2x")
    monkeypatch.setattr(
        engine, "extract_image_fields", lambda _img: {**_fields("113640", "76,82"), "code_box": (40, 20, 80, 60)}
    )

    fields = engine._read_with_strategy(Image.new("L", (100, 100)), zoom)

    assert fields["code_box"] == (20, 10, 40, 30)


# ==========================================
# Tool paths
# ==========================================


def test_tesseract_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "/opt/ocr/tesseract")
    assert engine.default_tesseract_path() == "/opt/ocr/tesseract"


def test_tesseract_on_path_is_preferred_over_windows_default(monkeypatch):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(engine.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert engine.default_tesseract_path() == "/usr/bin/tesseract"


def test_poppler_on_path_needs_no_directory(monkeypatch):
    monkeypatch.delenv("POPPLER_PATH", raising=False)
    monkeypatch.setattr(engine.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert engine.default_poppler_path() == ""


# ==========================================
# normalize_amount
# ==========================================


@pytest.mark.parametrize("text, expected", [
    ("82,30", "82,30"),
    ("76.82", "76,82"),                 # period instead of comma
    ("Total due 82,30 by 21/11", "82,30"),  # extract from a longer line
    ("82‚30", "82,30"),            # low comma
    ("82’30", "82,30"),            # curly apostrophe
    ("82`30", "82,30"),                 # backtick
    ("82´30", "82,30"),            # acute accent
    ("999,99", "999,99"),               # accepted upper bound
])
def test_amount_is_normalized_to_report_format(text, expected):
    assert engine.normalize_amount(text) == expected


@pytest.mark.parametrize("text", [
    "1000,00",   # above the 999,99 limit
    "82,3",      # only one decimal place
    "abc",
    "",
    None,
])
def test_invalid_amount_returns_none(text):
    assert engine.normalize_amount(text) is None


def test_amount_below_ten_reais_is_not_recognized():
    """Known limitation: the regex requires two or three integer digits.

    Amounts below 10,00 are not recognized; keep this limitation explicit."""
    assert engine.normalize_amount("5,30") is None


# ==========================================
# extract_reference_list
# ==========================================


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, pages):
        self.pages = [_FakePage(text) for text in pages]


@pytest.fixture
def reference(monkeypatch):
    """Replace the PDF reader with fixed text to test parsing without private PDFs."""
    def _read(*pages):
        monkeypatch.setattr(engine.pypdf, "PdfReader", lambda _path: _FakeReader(pages))
        return engine.extract_reference_list("sample_reference.pdf")
    return _read


def test_reference_list_restores_trailing_zero(reference):
    """Reference list restores trailing zero."""
    assert reference("11364-0 76,82\n5975-0 82,30") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "59750", "amount": "82,30"},
    ]


def test_code_without_amount_gets_na(reference):
    assert reference("11364-0 5975-0 76,82") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "59750", "amount": "N/A"},
    ]


def test_amount_without_code_gets_na(reference):
    assert reference("11364-0 76,82 82,30") == [
        {"code": "113640", "amount": "76,82"},
        {"code": "N/A", "amount": "82,30"},
    ]


def test_page_without_text_is_skipped(reference):
    assert reference("", "11364-0 76,82") == [{"code": "113640", "amount": "76,82"}]


def test_unreadable_pdf_returns_empty_list(monkeypatch):
    """Unreadable pdf returns empty list."""
    def raise_read_error(_path):
        raise OSError("Corrupt PDF")

    monkeypatch.setattr(engine.pypdf, "PdfReader", raise_read_error)
    assert engine.extract_reference_list("sample.pdf") == []


def test_extra_amount_shifts_positional_pairing(reference):
    """Known limitation: reference fields are paired by position, not by line.

    An extra total shifts the code/amount pairing and creates an orphan amount."""
    assert reference("Total 99,99\n11364-0 76,82") == [
        {"code": "113640", "amount": "99,99"},
        {"code": "N/A", "amount": "76,82"},
    ]
