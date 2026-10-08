"""Test synthetic sample generation and benchmark scoring without running OCR."""

import pypdf

from core import engine, samples
from tools import benchmark


def test_text_pdf_is_readable_by_the_reference_parser(tmp_path):
    path = tmp_path / "reference.pdf"
    samples.write_text_pdf(path, [["Registration  Amount", "11364-0   76,82"], ["5975-0   82,30"]])

    assert len(pypdf.PdfReader(path).pages) == 2
    assert engine.extract_reference_list(str(path)) == [
        {"code": "113640", "amount": "76,82"},
        {"code": "59750", "amount": "82,30"},
    ]


def test_dataset_contains_slips_reference_and_ground_truth(tmp_path):
    dataset = samples.generate_dataset(tmp_path, slips=6, seed=1)

    reference = engine.extract_reference_list(dataset["reference"])
    truth = samples.load_ground_truth(dataset["ground_truth"])

    assert len(reference) == 6
    assert len(truth) == dataset["pages"] == 5, "one slip is removed from the batch"
    assert len(pypdf.PdfReader(dataset["payment_slips"]).pages) == 5
    assert len(dataset["scenario"]) == 3


def test_dataset_without_discrepancies_matches_reference(tmp_path):
    dataset = samples.generate_dataset(tmp_path, slips=3, seed=2, discrepancies=False)

    reference = engine.extract_reference_list(dataset["reference"])
    truth = samples.load_ground_truth(dataset["ground_truth"])

    assert [truth[page] for page in sorted(truth)] == reference
    assert dataset["scenario"] == []


def test_batch_can_keep_every_slip(tmp_path):
    dataset = samples.generate_dataset(tmp_path, slips=4, seed=3, remove_slip=False)
    assert dataset["pages"] == 4
    assert not any("missing" in line for line in dataset["scenario"])


def test_same_seed_generates_same_contents(tmp_path):
    first = samples.generate_dataset(tmp_path / "a", slips=4, seed=9)
    second = samples.generate_dataset(tmp_path / "b", slips=4, seed=9)

    assert samples.load_ground_truth(first["ground_truth"]) == samples.load_ground_truth(second["ground_truth"])


REFERENCE = [
    {"code": "113640", "amount": "76,82"},
    {"code": "116110", "amount": "82,30"},
    {"code": "118870", "amount": "76,82"},
]


def _row(page, code, amount, status):
    return {"Page": page, "Code (OCR Slips)": code, "Amount (OCR Slips)": amount, "Overall Status": status}


def test_evaluation_separates_ocr_errors_from_real_discrepancies():
    truth = {
        1: {"code": "113640", "amount": "76,82"},
        2: {"code": "116110", "amount": "99,99"},  # the document really differs
    }
    rows = [
        _row(1, "113840", "76,82", "ERROR"),  # OCR misread a correct slip: false alarm
        _row(2, "116110", "99,99", "ERROR"),  # real discrepancy caught
    ]

    score = benchmark.evaluate(rows, [{"line": 3}], truth, REFERENCE)

    assert score["code_accuracy"] == 0.5
    assert score["amount_accuracy"] == 1.0
    assert score["real_discrepancies"] == 1
    assert score["caught"] == 1
    assert score["false_alarms"] == 1
    assert score["missed"] == 0
    assert (score["missing_found"], score["missing_expected"]) == (1, 1)


def test_evaluation_counts_missed_discrepancies():
    truth = {1: {"code": "113640", "amount": "99,99"}}
    rows = [_row(1, "113640", "76,82", "OK")]  # OCR read the expected amount instead of the real one

    score = benchmark.evaluate(rows, [], truth, REFERENCE)

    assert score["missed"] == 1
    assert score["caught"] == 0
