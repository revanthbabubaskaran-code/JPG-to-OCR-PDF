"""Turn JPG scans into image-faithful, searchable PDFs."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


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


def _run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Searchable JPG to PDF")
    root.minsize(780, 590)
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
    language_var = tk.StringVar(value="eng")
    mode_var = tk.StringVar(value="single")
    destination_label_var = tk.StringVar(value="Output PDF")
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
        mode_var.set("single")
        destination_label_var.set("Output PDF (all images, one page each)")
        language_var.set("eng")
        status_var.set("List cleared. Add images to start again.")
        refresh_list()

    def change_mode() -> None:
        destination_var.set("")
        if mode_var.get() == "single":
            destination_label_var.set("Output PDF (all images, one page each)")
        else:
            destination_label_var.set("Output folder (one PDF per image)")

    def choose_destination() -> None:
        if mode_var.get() == "single":
            chosen = filedialog.asksaveasfilename(
                title="Save combined searchable PDF",
                defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf")],
                initialfile="searchable-images.pdf",
            )
        else:
            chosen = filedialog.askdirectory(title="Choose folder for separate PDFs", mustexist=True)
        if chosen:
            destination_var.set(chosen)

    ttk.Button(frame, text="Add images…", command=add_images).grid(row=3, column=0, sticky="w")
    ttk.Button(frame, text="Clear list / start over", command=clear_list).grid(row=3, column=1, sticky="w")
    ttk.Label(frame, text="Before conversion, choose the output format").grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(14, 3)
    )
    ttk.Radiobutton(
        frame, text="One combined PDF (one page per image)", variable=mode_var,
        value="single", command=change_mode,
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Radiobutton(
        frame, text="Separate PDFs (one PDF for each image)", variable=mode_var,
        value="separate", command=change_mode,
    ).grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Label(frame, textvariable=destination_label_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 2))
    ttk.Entry(frame, textvariable=destination_var).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    ttk.Button(frame, text="Browse…", command=choose_destination).grid(row=8, column=2, padx=(8, 0), pady=(2, 8))
    ttk.Label(frame, text="OCR language code (Tesseract)").grid(row=9, column=0, sticky="w")
    ttk.Entry(frame, textvariable=language_var, width=12).grid(row=10, column=0, sticky="w", pady=(3, 8))
    ttk.Label(frame, text="Example: eng; for multiple languages use eng+tam and install both data files.").grid(
        row=10, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(3, 8)
    )
    ttk.Label(frame, textvariable=status_var, wraplength=680).grid(row=11, column=0, columnspan=3, sticky="w", pady=(0, 8))

    def convert() -> None:
        if not image_paths:
            messagebox.showwarning("Choose images", "Add one or more input images first.", parent=root)
            return
        if not destination_var.get():
            messagebox.showwarning("Choose output destination", "Choose the output PDF or output folder first.", parent=root)
            return
        language = language_var.get().strip() or "eng"
        try:
            if mode_var.get() == "single":
                target = Path(destination_var.get())
                if target.exists() and not messagebox.askyesno(
                    "Replace existing PDF?", f"This file already exists:\n{target}\n\nReplace it?", parent=root
                ):
                    return
                status_var.set(f"Converting {len(image_paths)} image(s) into one PDF…")
                root.update_idletasks()
                words = convert_images(image_paths, target, language=language)
                output_summary = str(target)
            else:
                folder = Path(destination_var.get())
                output_paths: list[Path] = []
                used_names: set[str] = set()
                for image_path in image_paths:
                    stem = Path(image_path).stem
                    candidate = f"{stem}.pdf"
                    suffix = 2
                    while candidate.casefold() in used_names:
                        candidate = f"{stem} ({suffix}).pdf"
                        suffix += 1
                    used_names.add(candidate.casefold())
                    output_paths.append(folder / candidate)
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
                    words += convert_image(image_path, target, language=language)
                output_summary = str(folder)
        except Exception as exc:
            status_var.set("Conversion failed.")
            messagebox.showerror(
                "Conversion failed", f"{exc}\n\nCheck that Tesseract OCR and the selected language data are installed.", parent=root
            )
            return
        count = len(image_paths)
        description = "one searchable PDF" if mode_var.get() == "single" else f"{count} searchable PDF(s)"
        status_var.set(f"Finished: {count} image(s), {words} recognized words.")
        messagebox.showinfo(
            "PDF conversion complete",
            f"Created {description} with {words} recognized words.\n\nSaved to:\n{output_summary}",
            parent=root,
        )

    ttk.Button(frame, text="Convert", command=convert).grid(row=12, column=0, columnspan=3, sticky="ew", pady=(2, 0))
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
