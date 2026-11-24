"""Turn JPG scans into image-faithful, searchable PDFs."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def _configure_tesseract(pytesseract) -> None:
    """Find Tesseract in its usual Windows install locations when PATH lacks it."""
    if os.name == "nt" and not shutil.which("tesseract"):
        install_roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"]
        for root in filter(None, install_roots):
            executable = Path(root) / "Tesseract-OCR" / "tesseract.exe"
            if executable.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(executable)
                return


def _add_image_page(pdf, image_path: str | Path, language: str) -> int:
    """Add one image and its invisible OCR layer to an existing PDF canvas."""
    try:
        from PIL import Image, ImageOps
        import pytesseract
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("Dependencies are missing. Install them with: pip install -e .") from exc

    source = Path(image_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    _configure_tesseract(pytesseract)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    # OCR is run on the same pixels that are shown in the PDF, so the word boxes
    # line up with the page image without a second coordinate transform.
    try:
        data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR is not installed or could not be found. Install it from the setup link in README.md."
        ) from exc

    # Fit the scan to an A4 page while preserving its aspect ratio. This makes
    # phone photos and screenshots print at a useful document size too.
    a4_width, a4_height = 595.28, 841.89
    if image.width > image.height:
        a4_width, a4_height = a4_height, a4_width
    scale = min(a4_width / image.width, a4_height / image.height)
    page_width, page_height = image.width * scale, image.height * scale
    pdf.setPageSize((page_width, page_height))
    pdf.drawImage(ImageReader(image), 0, 0, width=page_width, height=page_height, preserveAspectRatio=False)

    # Prefer a Unicode font when available (for example, Nirmala UI on Windows
    # includes Tamil). Helvetica remains a dependency-free fallback for English.
    font_name = "Helvetica"
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font_candidates = [
        windows_fonts / "Nirmala.ttf",
        windows_fonts / "NirmalaB.ttf",
        windows_fonts / "Latha.ttf",
        windows_fonts / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    for font_path in font_candidates:
        if font_path.is_file():
            try:
                if "SearchableUnicode" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("SearchableUnicode", str(font_path)))
                font_name = "SearchableUnicode"
                break
            except Exception:
                continue

    text = pdf.beginText()
    text.setTextRenderMode(3)  # Invisible text: preserves the scan's appearance.
    count = 0
    for i, raw_word in enumerate(data["text"]):
        word = raw_word.strip()
        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not word or confidence < 0:
            continue
        left = int(data["left"][i]) * scale
        top = int(data["top"][i]) * scale
        width = max(1.0, int(data["width"][i]) * scale)
        height = max(1.0, int(data["height"][i]) * scale)
        font_size = max(1.0, height * 0.82)
        text.setFont(font_name, font_size)
        text.setTextOrigin(left, page_height - (top + height * 0.88))
        # Match the OCR box width to keep selection and search positions aligned.
        try:
            measured = pdf.stringWidth(word, font_name, font_size)
        except Exception:
            # Skip a glyph sequence unsupported by the installed fallback font
            # rather than failing the entire PDF conversion.
            continue
        if measured > 0:
            text.setHorizScale(max(20, min(300, width / measured * 100)))
        text.textOut(word)
        text.setHorizScale(100)
        count += 1
    pdf.drawText(text)
    pdf.showPage()
    return count


def convert_images(image_paths: list[str | Path], output_path: str | Path, language: str = "eng") -> int:
    """Create one multi-page searchable PDF from one or more images.

    Returns the total number of OCR words placed across all pages.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("Dependencies are missing. Install them with: pip install -e .") from exc

    sources = [Path(path).expanduser() for path in image_paths]
    if not sources:
        raise ValueError("Choose at least one image to convert.")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Image not found: {source}")

    target = Path(output_path).expanduser()
    if any(source.resolve() == target.resolve() for source in sources):
        raise ValueError("The PDF output path must be different from every input image.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}.", suffix=".pdf", dir=target.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        pdf = canvas.Canvas(str(temporary_path), pagesize=A4, pageCompression=1)
        word_count = sum(_add_image_page(pdf, source, language) for source in sources)
        pdf.save()
        temporary_path.replace(target)
        return word_count
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def convert_image(image_path: str | Path, output_path: str | Path, language: str = "eng") -> int:
    """Create a searchable PDF from one image. Returns its recognized word count."""
    return convert_images([image_path], output_path, language=language)


class _UnreadableCertificateFields(ValueError):
    def __init__(self, source: Path, record: dict[str, str], missing: list[str]):
        self.source = source
        self.record = record
        self.missing = missing
        super().__init__(f"Could not reliably read {', '.join(missing)} from {source.name}.")


def _transcription_record(
    image_path: str | Path, language: str, overrides: dict[str, str] | None = None
) -> dict[str, str]:
    """Read one photographed Tamil Nadu verification certificate for data entry."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("Dependencies are missing. Install them with: pip install -e .") from exc

    source = Path(image_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    _configure_tesseract(pytesseract)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    if image.width > image.height:
        image = image.rotate(90, expand=True)
    width, height = image.size

    def crop_ocr(box: tuple[float, float, float, float], scale: int, psm: int, enhance: bool = True) -> str:
        left, top, right, bottom = box
        crop = image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
        crop = ImageOps.autocontrast(crop.convert("L"))
        if enhance:
            crop = ImageEnhance.Contrast(crop).enhance(1.25)
        crop = crop.resize((crop.width * scale, crop.height * scale))
        return pytesseract.image_to_string(crop, lang=language, config=f"--psm {psm}")

    try:
        header = crop_ocr((0.62, 0.06, 0.98, 0.24), 3, 6)
        date_crop = crop_ocr((0.664, 0.103, 0.907, 0.134), 4, 6, enhance=False)
        trader_crop = crop_ocr((0.66, 0.12, 0.92, 0.16), 4, 6)
        body = crop_ocr((0.05, 0.31, 0.95, 0.43), 2, 6)
        table = crop_ocr((0.05, 0.39, 0.95, 0.62), 2, 6)
        fee_crop = crop_ocr((0.71, 0.46, 0.86, 0.52), 4, 6)
        type_cell = crop_ocr((0.59, 0.45, 0.65, 0.59), 6, 6)
        full_text = pytesseract.image_to_string(image, lang=language, config="--psm 3")
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR is not installed or could not be found. Install it from the setup link in README.md."
        ) from exc

    header_text = re.sub(r"(?<=\d)\s+(?=\d)", "", header)
    cv_match = re.search(
        r"\b([A-Z]{2,4})\s*[/|]\s*([0-9SO]{2,3})\s*[/|]\s*([0-9\s]{5,12})\b",
        header_text,
        re.I,
    )
    cv_number = ""
    if cv_match:
        prefix = cv_match.group(1).upper()
        division = cv_match.group(2).upper().replace("S", "5").replace("O", "0")
        serial = re.sub(r"\s+", "", cv_match.group(3))
        cv_number = f"{prefix}/{division}/{serial}"

    date_match = re.search(
        r"\bDate\s*[:.]?\s*(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(20\d{2})\b",
        date_crop + "\n" + header_text,
        re.I,
    )
    cv_date = ""
    if date_match:
        day, month, year = (int(part) for part in date_match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            cv_date = f"{day:02d}-{month:02d}-{year}"

    application = re.search(r"Application\s+Number\s*[:.]?\s*(\d{6,9})", header_text, re.I)
    trader = re.search(r"LABUID\s*/\s*TNLM\s*/\s*(\d{6,9})", trader_crop + "\n" + header_text, re.I)
    location = re.search(r"Locality\s*[:.]?\s*(.+?\b\d{6}\b)", body + "\n" + full_text, re.I | re.S)
    business_name = re.search(r"\bM\s*/\s*S\s+(.+?)\s*,\s*Locality\b", body + "\n" + full_text, re.I | re.S)
    if not business_name:
        business_name = re.search(r"\bMIS\s+(.+?)\s*,\s*Locality\b", body + "\n" + full_text, re.I | re.S)

    table_text = re.sub(r"(?<=\d)\s+(?=\d)", "", table)
    machine_numbers = set(re.findall(r"\b20\d{6}\b", table_text))
    if not machine_numbers:
        machine_numbers = set(re.findall(r"\b20\d{6}\b", full_text))
    make_match = re.search(r"\bMake\s*[:.]?\s*([A-Z][A-Z0-9-]{1,15})\b", table_text, re.I)
    max_match = re.search(r"\bMax\s*[:.]?\s*(\d+(?:[.,]\d+)?)\s*(kg|g)\b", table_text, re.I)
    min_match = re.search(r"\bMin\s*[:.]?\s*(\d+(?:[.,]\d+)?)\s*(kg|g)\b", table_text, re.I)
    interval_match = re.search(r"(?:e\s*/\s*d|e\s*d|eld)\s*[:.]?\s*(\d+(?:[.,]\d+)?)\s*(kg|g)\b", table_text, re.I)
    class_match = re.search(r"\bClass\s+([I1lL]{1,4})\b", table_text, re.I)
    suffix_match = re.search(r"\bMin\s*[:.]?\s*\d+(?:[.,]\d+)?\s*(?:kg|g)\s+([PC])\b", table_text, re.I)
    if not suffix_match:
        suffix_match = re.search(r"\b([PC])\b", type_cell, re.I)
    model = ""
    if suffix_match and make_match and make_match.group(1).upper() == "HMS":
        model = f"HMS-{suffix_match.group(1).upper()}"
    elif make_match:
        model_match = re.search(r"\bModel\s*[:.]?\s*([A-Z0-9/-]{2,20})", type_cell + "\n" + table_text, re.I)
        model = model_match.group(1).strip("-:/ ") if model_match else ""

    if class_match:
        class_value = class_match.group(1).upper().replace("1", "I").replace("L", "I")
    else:
        class_value = ""
    all_ocr_text = table_text + "\n" + full_text
    if re.search(r"\bElectronic\b", all_ocr_text, re.I):
        instrument_description = f"Non-Automatic Weighing Instruments - Electronic Class {class_value or 'III'} and IIII"
    elif re.search(r"\bMechanical\b|analogue|analog", all_ocr_text, re.I):
        instrument_description = f"Non-Automatic Weighing Instruments - Mechanical (analogue) Class {class_value or 'III'} and IIII"
    else:
        instrument_description = f"Non-Automatic Weighing Instruments - Class {class_value or 'III'}"
    fee_text = fee_crop + "\n" + table_text
    additional_fee_match = re.search(
        r"50\s*%\s*(?:Additional|Addl\w*)\s*Fee\s*[:.]?\s*(\d+(?:[.,]\d{1,2})?)",
        fee_text,
        re.I | re.S,
    )
    stamping_fee_match = re.search(r"Stamping\s*Fee\s*[:.]?\s*(\d+(?:[.,]\d{1,2})?)", fee_text, re.I | re.S)
    stamping_place = "Site" if re.search(r"Stamping\s+at\s+Site", header_text, re.I) else (
        "Office" if re.search(r"Stamping\s+at\s+Office", header_text, re.I) else ""
    )
    record = {
        "cv": cv_number,
        "date": cv_date,
        "application": application.group(1) if application else "",
        "trader": trader.group(1) if trader else "",
        "stamping_place": stamping_place,
        "business_name": re.sub(r"\s+", " ", business_name.group(1)).strip(" ,") if business_name else "",
        "address": re.sub(r"\s+", " ", location.group(1)).strip(" ,") if location else "",
        "machine": next(iter(machine_numbers)) if len(machine_numbers) == 1 else "",
        "make": make_match.group(1).upper() if make_match else "",
        "model": model,
        "max": f"{max_match.group(1).replace(',', '.')} {max_match.group(2).lower()}" if max_match else "",
        "min": f"{min_match.group(1).replace(',', '.')} {min_match.group(2).lower()}" if min_match else "",
        "interval": f"{interval_match.group(1).replace(',', '.')} {interval_match.group(2).lower()}" if interval_match else "",
        "class": class_value,
        "description": instrument_description,
        "additional_fee": additional_fee_match.group(1).replace(",", ".") if additional_fee_match else "",
        "stamping_fee": stamping_fee_match.group(1).replace(",", ".") if stamping_fee_match else "",
        "denomination": f"Weights: {max_match.group(1).replace(',', '.')} {max_match.group(2).lower()}" if max_match else "",
    }
    if overrides:
        record.update({key: value.strip() for key, value in overrides.items() if key in record})
    required = (
        "cv", "date", "application", "trader", "stamping_place", "business_name", "address",
        "machine", "make", "model", "max", "min", "interval", "denomination", "class",
        "additional_fee", "stamping_fee",
    )
    missing = [field for field in required if not record[field]]
    if missing:
        labels = {
            "cv": "CV number", "date": "CV date", "application": "application number", "trader": "trader number",
            "stamping_place": "stamping location", "business_name": "business name", "address": "address",
            "machine": "machine number", "make": "make", "model": "model", "max": "maximum capacity",
            "min": "minimum capacity", "interval": "verification interval", "denomination": "denomination",
            "class": "instrument class", "additional_fee": "additional fee", "stamping_fee": "stamping fee",
        }
        missing_labels = ", ".join(labels[field] for field in missing)
        raise _UnreadableCertificateFields(source, record, [labels[field] for field in missing])
    if len(machine_numbers) != 1:
        raise ValueError(f"Expected one machine record in {source.name}; found {len(machine_numbers)} machine numbers. Use one certificate per image.")
    return record


def _build_stamping_pdf(
    image_paths: list[str | Path], output_path: str | Path, language: str,
    on_unreadable=None,
) -> int:
    """Create an app-readable data transcription, visibly marked as non-official."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    except ImportError as exc:
        raise RuntimeError("Dependencies are missing. Install them with: pip install -e .") from exc

    sources = [Path(path).expanduser() for path in image_paths]
    if not sources:
        raise ValueError("Choose at least one image to convert.")
    records = []
    for source in sources:
        try:
            records.append(_transcription_record(source, language))
        except _UnreadableCertificateFields as exc:
            if on_unreadable is None:
                raise
            corrections = on_unreadable(exc)
            if corrections is None:
                raise ValueError("Conversion cancelled; unreadable fields were not supplied.") from exc
            records.append(_transcription_record(source, language, corrections))
    target = Path(output_path).expanduser()
    if any(source.resolve() == target.resolve() for source in sources):
        raise ValueError("The PDF output path must be different from every input image.")
    target.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TransTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=14, leading=17, alignment=1, spaceAfter=2, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransSub", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=1, spaceAfter=7))
    styles.add(ParagraphStyle(name="TransSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=9, leading=11, spaceBefore=5, spaceAfter=3, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransCell", parent=styles["Normal"], fontName="Helvetica", fontSize=7.4, leading=9, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransLabel", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.1, leading=8.5, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransSmall", parent=styles["Normal"], fontName="Helvetica", fontSize=7, leading=9, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransLine", parent=styles["Normal"], fontName="Helvetica", fontSize=7.2, leading=9, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransTableCell", parent=styles["Normal"], fontName="Helvetica", fontSize=5.4, leading=6.6, textColor=colors.black))
    styles.add(ParagraphStyle(name="TransTableHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=5.3, leading=6.2, alignment=1, textColor=colors.black))
    p = lambda value, style="TransCell": Paragraph(value, styles[style])
    with tempfile.NamedTemporaryFile(prefix=f".{target.stem}.", suffix=".pdf", dir=target.parent, delete=False) as tmp:
        temporary_path = Path(tmp.name)
    try:
        doc = SimpleDocTemplate(str(temporary_path), pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=12*mm, bottomMargin=12*mm, title="Stamping input data transcription", author="Data transcription")
        story = []
        for index, record in enumerate(records, start=1):
            story += [
                Paragraph("CERTIFICATE DATA TRANSCRIPTION", styles["TransTitle"]),
                Paragraph("FOR SOFTWARE DATA ENTRY - NOT AN OFFICIAL CERTIFICATE", styles["TransSub"]),
                p(f"Record {index} of {len(records)}. OCR transcription from the selected image. Verify every value against the source before submission.", "TransSmall"),
                p(f"Stamping at {record['stamping_place']}<br/>CV No.: {record['cv']}<br/>Date: {record['date']}<br/>Application Number: {record['application']}<br/>Trader No.:<br/>{record['trader']}", "TransLine"),
                Paragraph("Previous certificate and application identifiers", styles["TransSection"]),
            ]
            metadata = [
                [p("Previous CV number", "TransLabel"), p(record["cv"]), p("Previous CV date", "TransLabel"), p(record["date"])],
                [p("Application number", "TransLabel"), p(record["application"]), p("Trader / establishment number", "TransLabel"), p(record["trader"])],
                [p("Stamping at", "TransLabel"), p(record["stamping_place"]), p("Source image", "TransLabel"), p(f"{index} of {len(records)}")],
            ]
            metadata_table = Table(metadata, colWidths=[32*mm, 50*mm, 40*mm, 58*mm])
            metadata_table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.black),("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),("BACKGROUND",(2,0),(2,-1),colors.whitesmoke),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
            story += [metadata_table, Paragraph("Business and address", styles["TransSection"]), p(f"Record belonging to M/S {record['business_name']}, Locality {record['address']}"), Spacer(1,3)]
            headers = ["Items", "Quantity", "Denomination", "Measures", "Capacity", "Class", "Manufacturer", "Type / Model", "Machine identifier", "Verification Fee"]
            values = [
                record["description"], "1", record["denomination"], "-", "-", record["class"],
                f"Make: {record['make']}<br/>Max: {record['max']}<br/>Min: {record['min']}<br/>e/d: {record['interval']}",
                f"Model: {record['model']}", f"Machine No.: {record['machine']}",
                f"Stamping Fee: {record['stamping_fee'] or 'verify source'}<br/>50% Additional Fee: {record['additional_fee'] or 'verify source'}",
            ]
            widths = [25,12,20,12,17,10,27,17,18,22]
            machine_table = Table([[p(value,"TransTableHead") for value in headers],[p(value,"TransTableCell") for value in values]], colWidths=[value*mm for value in widths], repeatRows=1)
            machine_table.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.55,colors.black),("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
            story += [machine_table, Spacer(1,6), p("This transcription is for data entry and review only. It does not replace, certify, or reproduce the original record.", "TransSmall")]
            if index < len(records):
                story.append(PageBreak())
        doc.build(story)
        temporary_path.replace(target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return len(records)


def _run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title("Searchable JPG to PDF")
    root.minsize(780, 700)
    frame = ttk.Frame(root, padding=18)
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text="Create searchable PDFs from images", font=("Segoe UI", 14, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    image_paths: list[str] = []
    destination_var = tk.StringVar()
    output_name_var = tk.StringVar()
    language_var = tk.StringVar(value="eng")
    mode_var = tk.StringVar(value="single")
    format_var = tk.StringVar(value="ocr")
    count_var = tk.StringVar(value="No images selected")
    status_var = tk.StringVar(value="Add one or more JPG images to begin.")

    ttk.Label(frame, text="Input images").grid(row=1, column=0, sticky="w")
    ttk.Label(frame, textvariable=count_var).grid(row=1, column=1, columnspan=2, sticky="e")
    list_frame = ttk.Frame(frame)
    list_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(4, 5))
    frame.rowconfigure(2, weight=1)
    list_frame.columnconfigure(0, weight=1)
    list_frame.rowconfigure(0, weight=1)
    image_list = tk.Listbox(list_frame, height=10, selectmode=tk.EXTENDED, activestyle="dotbox")
    image_list.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=image_list.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    image_list.configure(yscrollcommand=scrollbar.set)

    def refresh_list() -> None:
        image_list.delete(0, tk.END)
        for path in image_paths:
            image_list.insert(tk.END, path)
        count_var.set(f"{len(image_paths)} image{'s' if len(image_paths) != 1 else ''} selected")

    def add_images() -> None:
        chosen = filedialog.askopenfilenames(
            title="Choose one or more images",
            filetypes=[("JPEG images", "*.jpg *.jpeg"), ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp")],
        )
        known = {str(Path(path).resolve()).casefold() for path in image_paths}
        for path in chosen:
            normalized = str(Path(path).resolve())
            if normalized.casefold() not in known:
                image_paths.append(path)
                known.add(normalized.casefold())
        refresh_list()

    def clear_list() -> None:
        image_paths.clear()
        destination_var.set("")
        output_name_var.set("")
        mode_var.set("single")
        format_var.set("ocr")
        language_var.set("eng")
        status_var.set("List cleared. Add images to start again.")
        refresh_list()

    def choose_destination() -> None:
        chosen = filedialog.askdirectory(title="Choose the PDF output folder", mustexist=True)
        if chosen:
            destination_var.set(chosen)

    def show_copyable_error(title: str, details: str) -> None:
        """Show conversion diagnostics in selectable text with a clipboard button."""
        dialog = tk.Toplevel(root)
        dialog.title(title)
        dialog.transient(root)
        dialog.resizable(True, True)
        dialog.geometry("620x260")
        container = ttk.Frame(dialog, padding=14)
        container.grid(row=0, column=0, sticky="nsew")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        ttk.Label(container, text="Copy these details and send them for help:").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        details_box = tk.Text(container, wrap="word", height=7, width=72)
        details_box.grid(row=1, column=0, sticky="nsew")
        details_box.insert("1.0", details)
        details_box.configure(state="disabled")
        buttons = ttk.Frame(container)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))

        def copy_details() -> None:
            dialog.clipboard_clear()
            dialog.clipboard_append(details)
            dialog.update()

        ttk.Button(buttons, text="Copy error", command=copy_details).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=1)
        dialog.bind("<Control-c>", lambda _event: copy_details())
        dialog.bind("<Control-C>", lambda _event: copy_details())

    def ask_for_unreadable_fields(error: _UnreadableCertificateFields) -> dict[str, str] | None:
        key_for_label = {
            "CV number": "cv", "CV date": "date", "application number": "application",
            "trader number": "trader", "stamping location": "stamping_place", "business name": "business_name",
            "address": "address", "machine number": "machine", "make": "make", "model": "model",
            "maximum capacity": "max", "minimum capacity": "min", "verification interval": "interval",
            "denomination": "denomination", "instrument class": "class", "additional fee": "additional_fee",
            "stamping fee": "stamping_fee",
        }
        corrections = {}
        for label in error.missing:
            value = simpledialog.askstring(
                "Check unreadable OCR field",
                f"OCR could not read the {label} in:\n{error.source.name}\n\nEnter it exactly as printed on the image:",
                parent=root,
            )
            if value is None or not value.strip():
                return None
            corrections[key_for_label[label]] = value.strip()
        return corrections

    ttk.Button(frame, text="Add images…", command=add_images).grid(row=3, column=0, sticky="w")
    ttk.Button(frame, text="Clear list / start over", command=clear_list).grid(row=3, column=1, sticky="w")
    ttk.Label(frame, text="Before conversion, choose the output format").grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(14, 3)
    )
    ttk.Radiobutton(
        frame, text="One combined PDF (one page per image)", variable=mode_var,
        value="single",
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Radiobutton(
        frame, text="Separate PDFs (one PDF for each image)", variable=mode_var,
        value="separate",
    ).grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Label(frame, text="PDF format").grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 2))
    ttk.Radiobutton(
        frame, text="Regular OCR PDF (original image with searchable text)", variable=format_var,
        value="ocr",
    ).grid(row=8, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Radiobutton(
        frame, text="Stamping input transcription (data table; not an official certificate)", variable=format_var,
        value="stamping",
    ).grid(row=9, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Label(frame, text="PDF name (you can enter it with or without .pdf)").grid(
        row=10, column=0, columnspan=3, sticky="w", pady=(10, 2)
    )
    ttk.Entry(frame, textvariable=output_name_var).grid(row=11, column=0, columnspan=3, sticky="ew", pady=(2, 8))
    ttk.Label(frame, text="Output folder").grid(row=12, column=0, columnspan=3, sticky="w")
    ttk.Entry(frame, textvariable=destination_var).grid(row=13, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    ttk.Button(frame, text="Browse…", command=choose_destination).grid(row=13, column=2, padx=(8, 0), pady=(2, 8))
    ttk.Label(frame, text="OCR language code (Tesseract)").grid(row=14, column=0, sticky="w")
    ttk.Entry(frame, textvariable=language_var, width=12).grid(row=15, column=0, sticky="w", pady=(3, 8))
    ttk.Label(frame, text="Example: eng; for multiple languages use eng+tam and install both data files.").grid(
        row=15, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(3, 8)
    )
    ttk.Label(frame, textvariable=status_var, wraplength=680).grid(row=16, column=0, columnspan=3, sticky="w", pady=(0, 8))

    def convert() -> None:
        if not image_paths:
            messagebox.showwarning("Choose images", "Add one or more input images first.", parent=root)
            return
        if not destination_var.get():
            messagebox.showwarning("Choose output folder", "Choose the folder where the PDF file(s) should be saved.", parent=root)
            return
        entered_name = output_name_var.get().strip()
        if entered_name.lower().endswith(".pdf"):
            entered_name = entered_name[:-4].rstrip()
        if not entered_name:
            messagebox.showwarning("Enter a PDF name", "Enter a name for the output PDF file(s).", parent=root)
            return
        if any(char in entered_name for char in '<>:"/\\|?*') or entered_name.endswith((" ", ".")):
            messagebox.showwarning(
                "Invalid PDF name", "Use a file name without folder paths or Windows-invalid characters.", parent=root
            )
            return
        device_name = entered_name.split(".", 1)[0]
        if re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", device_name):
            messagebox.showwarning("Invalid PDF name", "Choose a different name; Windows reserves that file name.", parent=root)
            return
        language = language_var.get().strip() or "eng"
        folder = Path(destination_var.get())
        try:
            if mode_var.get() == "single":
                target = folder / f"{entered_name}.pdf"
                if target.exists() and not messagebox.askyesno(
                    "Replace existing PDF?", f"This file already exists:\n{target}\n\nReplace it?", parent=root
                ):
                    return
                status_var.set(f"Converting {len(image_paths)} image(s) into one PDF…")
                root.update_idletasks()
                if format_var.get() == "stamping":
                    words = _build_stamping_pdf(
                        image_paths, target, language=language, on_unreadable=ask_for_unreadable_fields
                    )
                else:
                    words = convert_images(image_paths, target, language=language)
                output_summary = str(target)
            else:
                output_paths = [folder / f"{entered_name}_{index:03d}.pdf" for index in range(1, len(image_paths) + 1)]
                existing = [path for path in output_paths if path.exists()]
                if existing and not messagebox.askyesno(
                    "Replace existing PDFs?",
                    f"{len(existing)} output file(s) already exist in this folder. Replace them?",
                    parent=root,
                ):
                    return
                words = 0
                for index, (image_path, target) in enumerate(zip(image_paths, output_paths), start=1):
                    status_var.set(f"Converting image {index} of {len(image_paths)}…")
                    root.update_idletasks()
                    if format_var.get() == "stamping":
                        words += _build_stamping_pdf(
                            [image_path], target, language=language, on_unreadable=ask_for_unreadable_fields
                        )
                    else:
                        words += convert_image(image_path, target, language=language)
                output_summary = str(folder)
        except Exception as exc:
            status_var.set("Conversion failed.")
            show_copyable_error(
                "Conversion failed",
                str(exc),
            )
            return
        count = len(image_paths)
        description = "one PDF" if mode_var.get() == "single" else f"{count} PDF(s)"
        detail = f"{words} transcription record(s)" if format_var.get() == "stamping" else f"{words} recognized words"
        status_var.set(f"Finished: {count} image(s), {detail}.")
        messagebox.showinfo(
            "PDF conversion complete",
            f"Created {description} ({detail}).\n\nSaved to:\n{output_summary}",
            parent=root,
        )

    ttk.Button(frame, text="Convert", command=convert).grid(row=17, column=0, columnspan=3, sticky="ew", pady=(2, 0))
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an OCR PDF or stamping-input transcription from an image.")
    parser.add_argument("image", nargs="?", help="Input JPG/PNG/TIFF/BMP image; omit to open the desktop app")
    parser.add_argument("output", nargs="?", help="Output PDF path")
    parser.add_argument("--lang", default="eng", help="Tesseract language code (default: eng)")
    parser.add_argument(
        "--format", choices=("ocr", "stamping"), default="ocr",
        help="PDF format: image-faithful searchable OCR (default) or stamping-input transcription",
    )
    args = parser.parse_args()
    if not args.image:
        _run_gui()
        return
    if not args.output:
        parser.error("output is required when an input image is provided")
    try:
        if args.format == "stamping":
            words = _build_stamping_pdf([args.image], args.output, language=args.lang)
        else:
            words = convert_image(args.image, args.output, language=args.lang)
    except Exception as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    detail = f"{words} transcription record(s)" if args.format == "stamping" else f"{words} searchable words"
    print(f"Created {args.output} with {detail}.")


__all__ = ["convert_image", "convert_images", "main"]
