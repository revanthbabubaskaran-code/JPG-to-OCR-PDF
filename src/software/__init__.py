"""Turn JPG scans into image-faithful, searchable PDFs."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def convert_image(image_path: str | Path, output_path: str | Path, language: str = "eng") -> int:
    """Create a PDF with the original image and an invisible, selectable OCR layer.

    Returns the number of OCR words placed in the PDF.
    """
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
    target = Path(output_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    if source.resolve() == target.resolve():
        raise ValueError("The PDF output path must be different from the image path.")
    # pytesseract uses this setting when Tesseract is installed in its common
    # Windows folder but that folder was not added to PATH.
    if os.name == "nt" and not shutil.which("tesseract"):
        install_roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"]
        for root in filter(None, install_roots):
            executable = Path(root) / "Tesseract-OCR" / "tesseract.exe"
            if executable.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(executable)
                break
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

    target.parent.mkdir(parents=True, exist_ok=True)
    # Fit the scan to an A4 page while preserving its aspect ratio. This makes
    # phone photos and screenshots print at a useful document size too.
    a4_width, a4_height = 595.28, 841.89
    if image.width > image.height:
        a4_width, a4_height = a4_height, a4_width
    scale = min(a4_width / image.width, a4_height / image.height)
    page_width, page_height = image.width * scale, image.height * scale
    pdf = canvas.Canvas(str(target), pagesize=(page_width, page_height), pageCompression=1)
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
    pdf.save()
    return count


def _run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Searchable JPG to PDF")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.grid(sticky="nsew")
    ttk.Label(frame, text="Create a searchable PDF from a JPG", font=("Segoe UI", 14, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
    )
    image_var, output_var, language_var = tk.StringVar(), tk.StringVar(), tk.StringVar(value="eng")

    def choose_image() -> None:
        chosen = filedialog.askopenfilename(
            title="Choose a JPG image",
            filetypes=[("JPEG images", "*.jpg *.jpeg"), ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp")],
        )
        if chosen:
            image_var.set(chosen)
            output_var.set(str(Path(chosen).with_suffix(".pdf")))

    def choose_output() -> None:
        chosen = filedialog.asksaveasfilename(
            title="Save searchable PDF", defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")]
        )
        if chosen:
            output_var.set(chosen)

    ttk.Label(frame, text="Input image").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=image_var, width=54).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 10))
    ttk.Button(frame, text="Browse…", command=choose_image).grid(row=2, column=2, padx=(8, 0), pady=(3, 10))
    ttk.Label(frame, text="Output PDF").grid(row=3, column=0, sticky="w")
    ttk.Entry(frame, textvariable=output_var, width=54).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(3, 10))
    ttk.Button(frame, text="Save as…", command=choose_output).grid(row=4, column=2, padx=(8, 0), pady=(3, 10))
    ttk.Label(frame, text="OCR language code (Tesseract)").grid(row=5, column=0, sticky="w")
    ttk.Entry(frame, textvariable=language_var, width=12).grid(row=6, column=0, sticky="w", pady=(3, 14))
    ttk.Label(frame, text="Example: eng for English; install that language in Tesseract.").grid(
        row=6, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(3, 14)
    )

    def convert() -> None:
        if not image_var.get() or not output_var.get():
            messagebox.showwarning("Choose files", "Choose an input image and output PDF first.", parent=root)
            return
        try:
            words = convert_image(image_var.get(), output_var.get(), language=language_var.get().strip() or "eng")
        except Exception as exc:
            messagebox.showerror("Conversion failed", f"{exc}\n\nCheck that Tesseract OCR is installed and on PATH.", parent=root)
            return
        messagebox.showinfo("PDF created", f"Saved searchable PDF with {words} recognized words:\n{output_var.get()}", parent=root)

    ttk.Button(frame, text="Convert to searchable PDF", command=convert).grid(row=7, column=0, columnspan=3, sticky="ew")
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a searchable PDF from a JPG or other image.")
    parser.add_argument("image", nargs="?", help="Input JPG/PNG/TIFF/BMP image; omit to open the desktop app")
    parser.add_argument("output", nargs="?", help="Output PDF path")
    parser.add_argument("--lang", default="eng", help="Tesseract language code (default: eng)")
    args = parser.parse_args()
    if not args.image:
        _run_gui()
        return
    if not args.output:
        parser.error("output is required when an input image is provided")
    try:
        words = convert_image(args.image, args.output, language=args.lang)
    except Exception as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Created {args.output} with {words} searchable words.")


__all__ = ["convert_image", "main"]
