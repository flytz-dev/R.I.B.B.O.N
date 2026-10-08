"""Synthetic payment slips for demonstrations, benchmarks, and the multi-user simulation.

The generated documents imitate the fields the engine reads (a 16-digit numeric line
ending in the registration code, and an amount) without any taxpayer data. A ground
truth file records what is printed on each slip, so OCR accuracy can be measured."""

import csv
import io
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Slips are "scanned" at a typical office-scanner resolution and stored as JPEG pages.
SCAN_DPI = 300
SLIP_SIZE_INCHES = (8.27, 5.83)  # A5 landscape

PRINTED_FONTS = ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc")
HANDWRITING_FONTS = ("Inkfree.ttf", "segoesc.ttf", "segoepr.ttf", "comic.ttf")

REFERENCE_LINES_PER_PAGE = 40


def _font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def handwriting_available() -> bool:
    """Whether a script font is installed to imitate handwritten slips."""
    for name in HANDWRITING_FONTS:
        try:
            ImageFont.truetype(name, 12)
            return True
        except OSError:
            continue
    return False


def _random_entry(rng, used_codes):
    while True:
        base = rng.randint(1000, 9999) if rng.random() < 0.2 else rng.randint(10000, 99999)
        code = f"{base}0"
        if code not in used_codes:
            used_codes.add(code)
            break
    # Standard fees repeat often in real batches, so several slips share an amount.
    amount = rng.choice(["76,82", "82,30", "64,15"]) if rng.random() < 0.5 else f"{rng.randint(10, 999)},{rng.randint(0, 99):02d}"
    return {"code": code, "amount": amount}


def _draw_handwritten(draw, position, text, font, rng):
    """Draw characters with small random offsets to imitate handwriting."""
    x, y = position
    for character in text:
        draw.text((x + rng.uniform(-3, 3), y + rng.uniform(-6, 6)), character, font=font, fill=rng.randint(0, 60))
        x += draw.textlength(character, font=font) * rng.uniform(0.95, 1.12)


def render_slip(code, amount, rng, handwritten=False):
    """Render one degraded, scanned-looking slip as a grayscale image."""
    width, height = (round(side * SCAN_DPI) for side in SLIP_SIZE_INCHES)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    margin = 110

    label = _font(PRINTED_FONTS, 34)
    title = _font(PRINTED_FONTS, 52)
    field = _font(HANDWRITING_FONTS, 92) if handwritten else _font(PRINTED_FONTS, 60)

    draw.rectangle((margin - 30, margin - 30, width - margin + 30, height - margin + 30), outline=0, width=4)
    draw.text((margin, margin), "PAYMENT SLIP - SYNTHETIC SAMPLE", font=title, fill=0)
    draw.text((margin, margin + 90), "Generated for testing. Contains no taxpayer data.", font=label, fill=70)

    numeric_line = code.zfill(16)
    draw.text((margin, margin + 230), "Numeric line", font=label, fill=60)
    draw.text((margin, margin + 520), "Amount due", font=label, fill=60)
    draw.text((width // 2 + 120, margin + 520), "Due date", font=label, fill=60)
    draw.text((width // 2 + 120, margin + 570), f"{rng.randint(10, 28)}/{rng.randint(10, 12)}/2026", font=field, fill=0)

    if handwritten:
        _draw_handwritten(draw, (margin, margin + 280), numeric_line, field, rng)
        _draw_handwritten(draw, (margin, margin + 570), amount, field, rng)
    else:
        draw.text((margin, margin + 280), numeric_line, font=field, fill=0)
        draw.text((margin, margin + 570), amount, font=field, fill=0)

    # Barcode-like stripes add realistic clutter around the fields.
    x = margin
    while x < width - margin:
        stripe = rng.choice((3, 5, 8))
        draw.rectangle((x, height - margin - 150, x + stripe, height - margin - 30), fill=0)
        x += stripe + rng.choice((4, 6, 9))

    # Imitate a scan: slight skew, blur, speckles, and JPEG compression.
    image = image.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255, expand=False)
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.1)))
    pixels = image.load()
    for _ in range(width * height // 900):
        pixels[rng.randrange(width), rng.randrange(height)] = rng.randint(0, 180)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=rng.randint(55, 80))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("L")


def _pdf_string(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_text_pdf(path, pages):
    """Write a minimal PDF with extractable Helvetica text; pages is a list of line lists."""
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    page_ids = []
    next_id = 4
    for lines in pages:
        content = "BT /F1 11 Tf 15 TL 60 790 Td " + " ".join(f"({_pdf_string(line)}) Tj T*" for line in lines) + " ET"
        content_id, page_id = next_id, next_id + 1
        next_id += 2
        objects[content_id] = f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>"

    output = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(output)
        output += f"{number} 0 obj\n{objects[number]}\nendobj\n".encode("latin-1")
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for number in range(1, len(objects) + 1):
        output += f"{offsets[number]:010d} 00000 n \n".encode()
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as file:
        file.write(output)


def generate_dataset(directory, slips=10, seed=0, handwritten=False, discrepancies=True, remove_slip=True):
    """Create payment_slips.pdf, reference.pdf, and ground_truth.csv in directory.

    With discrepancies, the batch includes a wrong amount on a slip, a missing slip
    (unless remove_slip is False), and two swapped slips, so the report exercises
    every matching path."""
    rng = random.Random(seed)
    used_codes = set()
    reference = [_random_entry(rng, used_codes) for _ in range(slips)]
    printed = [dict(entry) for entry in reference]
    scenario = []

    changed = None
    if discrepancies and slips >= 2:
        changed = rng.randrange(len(printed))
        printed[changed]["amount"] = f"{rng.randint(10, 999)},{rng.randint(0, 99):02d}"
        scenario.append(f"Slip for reference line {changed + 1} shows a different amount")
    if discrepancies and remove_slip and slips >= 4:
        index = rng.choice([i for i in range(1, len(printed) - 1) if i != changed])
        scenario.append(f"Slip for reference line {index + 1} is missing")
        del printed[index]
    if discrepancies and slips >= 6:
        index = rng.randrange(len(printed) - 1)
        printed[index], printed[index + 1] = printed[index + 1], printed[index]
        scenario.append(f"Slips on pages {index + 1} and {index + 2} are swapped")

    os.makedirs(directory, exist_ok=True)
    paths = {
        "payment_slips": os.path.join(directory, "payment_slips.pdf"),
        "reference": os.path.join(directory, "reference.pdf"),
        "ground_truth": os.path.join(directory, "ground_truth.csv"),
    }

    images = [render_slip(entry["code"], entry["amount"], rng, handwritten) for entry in printed]
    images[0].save(paths["payment_slips"], save_all=True, append_images=images[1:], resolution=SCAN_DPI)

    lines = ["Registration     Amount due"] + [f"{entry['code'][:-1]}-0     {entry['amount']}" for entry in reference]
    write_text_pdf(paths["reference"], [
        lines[start:start + REFERENCE_LINES_PER_PAGE]
        for start in range(0, len(lines), REFERENCE_LINES_PER_PAGE)
    ])

    with open(paths["ground_truth"], "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["page", "code", "amount"])
        for page, entry in enumerate(printed, start=1):
            writer.writerow([page, entry["code"], entry["amount"]])

    return {**paths, "pages": len(printed), "scenario": scenario}


def load_ground_truth(path):
    """Read page;code;amount rows describing what is printed on each slip."""
    with open(path, newline="", encoding="utf-8-sig") as file:
        return {
            int(row["page"]): {"code": row["code"].strip(), "amount": row["amount"].strip()}
            for row in csv.DictReader(file, delimiter=";")
        }
