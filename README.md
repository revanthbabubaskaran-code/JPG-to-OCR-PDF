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

Use **Add images…** to select one or more scans. Before converting, choose either one combined PDF (one page per image) or a separate PDF for every image. Enter a PDF name and choose the output folder. Combined mode saves as `<name>.pdf`; separate mode saves as `<name>_001.pdf`, `<name>_002.pdf`, and so on. **Clear list / start over** removes the selected images and resets the form.

## Command line

```powershell
searchable-pdf "C:\Scans\certificate.jpg" "C:\Scans\certificate-searchable.pdf" --lang eng
```

Supported image formats include JPG, PNG, TIFF, and BMP. The image is scaled proportionally to fit an A4-sized page. To check whether OCR worked, open the PDF and try selecting or searching for a word. OCR quality depends on image clarity, orientation, language data, and document layout; proofread important text.

## Notes

- The original image remains visible in the PDF; OCR text is invisible and selectable.
- For multiple OCR languages, pass a plus-separated Tesseract code such as `eng+tam` and install both language data files.
- The included sample certificate image demonstrates the intended scan-to-searchable-PDF workflow.
