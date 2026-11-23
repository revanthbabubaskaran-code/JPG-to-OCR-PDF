@echo off
setlocal
cd /d "%~dp0"

title Searchable JPG to PDF

if not exist ".venv\Scripts\python.exe" goto create_venv
goto check_app

:create_venv
echo Setting up the Python environment...
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo Python 3.10 or newer was not found. Install Python, then run this file again.
goto failed

:use_py
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install_app

:use_python
python -m venv .venv
if errorlevel 1 goto failed

:install_app
echo Installing the converter and its Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed

:check_app
if exist ".venv\Scripts\searchable-pdf.exe" goto check_tesseract
echo Installing the converter command...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed

:check_tesseract
where tesseract >nul 2>nul
if not errorlevel 1 goto launch
if not "%ProgramFiles%"=="" if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" (
    set "PATH=%ProgramFiles%\Tesseract-OCR;%PATH%"
    goto launch
)
if not "%ProgramFiles(x86)%"=="" if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" (
    set "PATH=%ProgramFiles(x86)%\Tesseract-OCR;%PATH%"
    goto launch
)
echo Tesseract OCR was not found on PATH.
echo Install Tesseract OCR from the link in README.md, including English language data.
echo The launcher also checks the usual Program Files install folders automatically.
echo Setup instructions are in README.md.
goto failed

:launch
echo Starting the searchable JPG to PDF app...
".venv\Scripts\searchable-pdf.exe"
if errorlevel 1 goto failed
goto done

:failed
echo.
echo The app could not start. See the message above and README.md for setup help.
pause

:done
endlocal
