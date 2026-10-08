"""Check that RIBBON can run on this computer.

Verifies the Python packages, Tesseract with English language data, and Poppler,
then reads one synthetic slip end to end. Run from the project root:

    python -m tools.check_install

Exit code 0 means the synthetic slip was read correctly."""

import sys
import tempfile


def main() -> int:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        from core import engine, samples
    except ImportError as e:
        print(f"Missing Python package: {e.name}")
        return 1

    tesseract = engine.pytesseract.pytesseract.tesseract_cmd
    try:
        version = engine.pytesseract.get_tesseract_version()
        languages = engine.pytesseract.get_languages(config="")
    except Exception as e:
        print(f"Tesseract is not usable ({tesseract}): {e}")
        return 1
    print(f"Tesseract {version}: {tesseract}")
    if "eng" not in languages:
        print("Tesseract has no English language data (eng.traineddata).")
        return 1
    print(f"Poppler: {engine.POPPLER_PATH or 'found on PATH'}")

    with tempfile.TemporaryDirectory(prefix="ribbon_check_") as directory:
        dataset = samples.generate_dataset(directory, slips=1, seed=1, discrepancies=False)
        try:
            events = list(engine.stream_audit(
                dataset["payment_slips"], dataset["reference"], dpi=200, include_image=False
            ))
        except Exception as e:
            print(f"Could not render the test PDF with Poppler: {type(e).__name__}: {e}")
            return 1

    rows = [event["row"] for event in events if event["type"] == "page"]
    if not rows or rows[0]["Overall Status"] != "OK":
        print("OCR ran but did not read the test slip correctly.")
        return 1
    print("OCR test passed: a synthetic slip was read correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
