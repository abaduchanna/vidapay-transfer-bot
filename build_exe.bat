@echo off
REM ------------------------------------------------------------------
REM Build a single-file, windowed EXE for the VidaPay Transfer Bot
REM Output: dist\VidaPayTransferBot.exe
REM ------------------------------------------------------------------
cd /d "%~dp0"

echo Installing build tools...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

echo Building VidaPayTransferBot.exe...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name VidaPayTransferBot ^
    --icon assets\gfh_bot_icon.ico ^
    --hidden-import selenium.webdriver.edge.webdriver ^
    --hidden-import selenium.webdriver.edge.service ^
    --hidden-import selenium.webdriver.edge.options ^
    VidaPay_Transfer_Bot.py

echo.
echo Done! The EXE is at: dist\VidaPayTransferBot.exe
pause
