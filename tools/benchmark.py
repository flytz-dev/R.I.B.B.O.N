"""Compare resolutions on the same documents and measure OCR accuracy against a ground truth.

Examples (run from the project root):

    python -m tools.benchmark --samples 12
    python -m tools.benchmark --samples 6 --handwritten
    python -m tools.benchmark --payment-slips slips.pdf --reference reference.pdf --ground-truth truth.csv

The ground truth is a semicolon-separated file with columns page;code;amount describing
what is actually printed on each slip. It is what separates OCR errors from genuine
document discrepancies: without it, a mismatch could be either."""

import argparse
import csv
import os
import sys
import tempfile
import time

from core import engine, samples


def expected_outcomes(truth, reference_list):
    """Apply the engine's matching rules to the true slip contents.

    Returns the status each page should receive with perfect OCR, and the reference
    lines that really have no slip."""
    matcher = engine.ReferenceMatcher(reference_list)
    statuses = {}
    for page in sorted(truth):
        reading = truth[page]
        index, matched_by = matcher.match(reading["code"])
        entry = reference_list[index] if index is not None else None
        statuses[page] = engine.compare_reading(reading, entry, matched_by)[2]
    return statuses, {entry["line"] for entry in matcher.missing()}


def evaluate(rows, missing, truth, reference_list):
    """Score one audit: OCR field accuracy and discrepancy detection against the truth."""
    expected, expected_missing = expected_outcomes(truth, reference_list)
    scored = [row for row in rows if row["Page"] in truth]

    def flagged(status):
        return status != "OK"

    true_positive = sum(flagged(row["Overall Status"]) and flagged(expected[row["Page"]]) for row in scored)
    false_alarm = sum(flagged(row["Overall Status"]) and not flagged(expected[row["Page"]]) for row in scored)
    missed = sum(not flagged(row["Overall Status"]) and flagged(expected[row["Page"]]) for row in scored)

    return {
        "pages": len(scored),
        "code_accuracy": _ratio(sum(row["Code (OCR Slips)"] == truth[row["Page"]]["code"] for row in scored), len(scored)),
        "amount_accuracy": _ratio(sum(row["Amount (OCR Slips)"] == truth[row["Page"]]["amount"] for row in scored), len(scored)),
        "real_discrepancies": sum(flagged(status) for status in expected.values()),
        "caught": true_positive,
        "false_alarms": false_alarm,
        "missed": missed,
        "missing_expected": len(expected_missing),
        "missing_found": len(expected_missing & {entry["line"] for entry in missing}),
    }


def _ratio(part, total):
    return part / total if total else None


def run(payment_slips, reference, dpi):
    """Run one audit without previews and return its rows, missing slips, and timing."""
    rows, missing, elapsed = [], [], None
    started = time.perf_counter()
    for event in engine.stream_audit(payment_slips, reference, dpi=dpi, include_image=False):
        if event["type"] == "page":
            rows.append(event["row"])
        elif event["type"] == "end":
            missing = event["missing"]
            elapsed = event["elapsed_seconds"]
    elapsed = elapsed if elapsed is not None else time.perf_counter() - started
    timed = [row for row in rows if row["OCR ms"] is not None]
    return {
        "dpi": dpi,
        "rows": rows,
        "missing": missing,
        "seconds": elapsed,
        "seconds_per_page": elapsed / len(rows) if rows else None,
        "processing_ms_per_page": (
            sum(row["Render ms"] + row["OCR ms"] for row in timed) / len(timed) if timed else None
        ),
        "consensus_pages": sum((row["Strategies Run"] or 0) > 1 for row in rows),
        "flagged": sum(row["Overall Status"] != "OK" for row in rows),
    }


def _percent(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def _number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def print_table(results):
    header = ["DPI", "Total s", "s/page (wall)", "ms/page (work)", "Consensus", "Flagged"]
    with_truth = "evaluation" in results[0]
    if with_truth:
        header += ["Code acc.", "Amount acc.", "Caught", "False alarms", "Missed", "Missing found"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for result in results:
        cells = [
            str(result["dpi"]), _number(result["seconds"], 1), _number(result["seconds_per_page"]),
            _number(result["processing_ms_per_page"], 0),
            f"{result['consensus_pages']}/{len(result['rows'])}", str(result["flagged"]),
        ]
        if with_truth:
            score = result["evaluation"]
            cells += [
                _percent(score["code_accuracy"]), _percent(score["amount_accuracy"]),
                f"{score['caught']}/{score['real_discrepancies']}", str(score["false_alarms"]),
                str(score["missed"]), f"{score['missing_found']}/{score['missing_expected']}",
            ]
        print("| " + " | ".join(cells) + " |")


def write_csv(path, results):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        keys = ["dpi", "seconds", "seconds_per_page", "processing_ms_per_page", "consensus_pages", "flagged"]
        evaluation_keys = list(results[0].get("evaluation", {}))
        writer.writerow(keys + ["pages"] + evaluation_keys)
        for result in results:
            writer.writerow(
                [result[key] for key in keys] + [len(result["rows"])]
                + [result["evaluation"][key] for key in evaluation_keys]
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--payment-slips", help="Scanned payment-slip PDF.")
    parser.add_argument("--reference", help="Reference PDF.")
    parser.add_argument("--ground-truth", help="CSV with page;code;amount printed on each slip.")
    parser.add_argument("--samples", type=int, help="Generate this many synthetic slips instead of using PDFs.")
    parser.add_argument("--handwritten", action="store_true", help="Imitate handwritten fields in synthetic slips.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for synthetic slips.")
    parser.add_argument("--dpi", type=int, nargs="+", default=[200, 300, 500], help="Resolutions to compare.")
    parser.add_argument("--output", help="Optional CSV file for the summary.")
    args = parser.parse_args(argv)

    if args.samples:
        directory = tempfile.mkdtemp(prefix="ribbon_benchmark_")
        dataset = samples.generate_dataset(directory, args.samples, args.seed, handwritten=args.handwritten)
        payment_slips, reference, ground_truth = dataset["payment_slips"], dataset["reference"], dataset["ground_truth"]
        print(f"Synthetic dataset in {directory}")
        for line in dataset["scenario"]:
            print(f"  - {line}")
    elif args.payment_slips and args.reference:
        payment_slips, reference, ground_truth = args.payment_slips, args.reference, args.ground_truth
    else:
        parser.error("use --samples, or both --payment-slips and --reference")

    truth = samples.load_ground_truth(ground_truth) if ground_truth else None
    reference_list = engine.extract_reference_list(reference)

    results = []
    for dpi in args.dpi:
        print(f"\nRunning at {dpi} DPI with {engine.OCR_WORKERS} OCR workers...", file=sys.stderr)
        result = run(payment_slips, reference, dpi)
        if truth:
            result["evaluation"] = evaluate(result["rows"], result["missing"], truth, reference_list)
        results.append(result)

    print()
    print_table(results)
    if args.output:
        write_csv(args.output, results)
        print(f"\nSummary written to {os.path.abspath(args.output)}")
    return results


if __name__ == "__main__":
    main()
