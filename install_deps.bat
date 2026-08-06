@echo off
REM ------------------------------------------------------------------
REM One-time setup for the VidaPay Transfer Bot
REM Installs the Python packages needed for OCR + browser automation.
REM ------------------------------------------------------------------
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Python packages installed.
echo Tesseract OCR / Ghostscript are checked and offered automatically
echo the first time the bot is launched.
echo.
pause
