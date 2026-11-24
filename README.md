# Searchable JPG to PDF

This desktop app turns a scanned JPG into a PDF that looks like the original and includes an invisible OCR text layer. You can select and copy recognized words or search for them in a PDF reader. The input image stays as the visible page, so stamps, signatures, tables, and QR codes remain intact.

## Requirements

- Python 3.10 or newer
- Tesseract OCR installed on Windows, with English language data
- Tesseract language data for each language you want to recognize (English is `eng`)

Install Tesseract for Windows from the [UB Mannheim Tesseract builds](https://github.com/UB-Mannheim/tesseract/wiki). During installation, include the needed language data. The launcher detects the usual `C:\Program Files\Tesseract-OCR` location automatically. If you installed it elsewhere, add its folder to `PATH`.

## Install and open the app

After Python and Tesseract are installed, double-click **Run Searchable PDF.bat**. The first run creates a local virtual environment and installs the Python packages; later runs open the app directly.

In PowerShell, from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
searchable-pdf
```

Use **Add images…** to select one or more scans. Choose whether to save one combined PDF (one page per image) or a separate PDF for every image, then select the PDF format:

- **Regular OCR PDF** keeps the original scan visible and adds selectable/searchable OCR text.
- **Stamping input transcription** creates a black and white data-entry PDF with a structured machine table that the stamping application can read. Fee details are omitted because they are not requested by the new application. It is visibly marked as a transcription, not an official certificate. If OCR cannot read a required field, the app asks you to type it from the source image rather than guessing. Verify every value before using it. This format currently expects one certificate with one machine per image.

Enter a PDF name and choose the output folder. Combined mode saves as `<name>.pdf`; separate mode saves as `<name>_001.pdf`, `<name>_002.pdf`, and so on. **Clear list / start over** removes the selected images and resets the form.

If conversion fails, the error details appear in a selectable window with a **Copy error** button so they can be pasted into a support request.

## Command line

```powershell
searchable-pdf "C:\Scans\certificate.jpg" "C:\Scans\certificate-searchable.pdf" --lang eng
```

To create a stamping-input transcription instead, add `--format stamping`:

```powershell
searchable-pdf "C:\Scans\certificate.jpg" "C:\Scans\certificate-transcription.pdf" --lang eng --format stamping
```

Supported image formats include JPG, PNG, TIFF, and BMP. The image is scaled proportionally to fit an A4-sized page. To check whether OCR worked, open the PDF and try selecting or searching for a word. OCR quality depends on image clarity, orientation, language data, and document layout; proofread important text.

## Notes

- The original image remains visible in the PDF; OCR text is invisible and selectable.
- For multiple OCR languages, pass a plus-separated Tesseract code such as `eng+tam` and install both language data files.
- The included sample certificate image demonstrates the intended scan-to-searchable-PDF workflow.
