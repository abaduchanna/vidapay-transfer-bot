@echo off
REM ==========================================================================
REM  GFH/VidaPay Bot — Pull + Clean + Build EXE
REM  Developed by Abad Umair Channa
REM
REM  This script:
REM    1. git pull — fetch latest code from GitHub
REM    2. Cleans previous build artifacts (build/, dist/, *.pyc)
REM    3. Installs/upgrade dependencies (requirements.txt + pyinstaller)
REM    4. Builds EVERY .spec file in the repo with PyInstaller
REM    5. Lists the produced .exe files in dist\
REM
REM  USAGE:
REM    1. Save this file in the repo root (next to the .spec files)
REM    2. Double-click build_exe.bat, OR run from Command Prompt
REM    3. The .exe files will appear in the dist\ folder
REM
REM  PREREQUISITES:
REM    - Python 3.11+ installed and in PATH
REM    - Git installed and in PATH
REM    - Run once: pip install pyinstaller -r requirements.txt
REM ==========================================================================

setlocal enabledelayedexpansion
title Pull + Clean + Build EXE

echo.
echo  ============================================================
echo   Pull Latest + Clean + Build EXE
echo  ============================================================
echo.

REM ── Step 0: Verify prerequisites ──
echo  Step 0: Checking prerequisites...

python --version >nul 2>&1
if errorlevel 1 (
    echo    ERROR: Python not found in PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)
echo    Python OK

git --version >nul 2>&1
if errorlevel 1 (
    echo    ERROR: Git not found in PATH. Install Git from https://git-scm.com
    pause
    exit /b 1
)
echo    Git OK

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo    PyInstaller not found. Installing...
    python -m pip install --upgrade pyinstaller
)
echo    PyInstaller OK
echo.

REM ── Step 1: Pull latest code from GitHub ──
echo  Step 1: Pulling latest code from GitHub...
git fetch origin
if errorlevel 1 (
    echo    WARNING: git fetch failed. Check your internet connection or GitHub token.
    echo    Continuing with local files...
) else (
    git pull origin main
    if errorlevel 1 (
        echo    WARNING: git pull failed (possible merge conflict).
        echo    Continuing with local files...
    ) else (
        echo    Latest code pulled successfully.
    )
)
echo.

REM ── Step 2: Clean previous build artifacts ──
echo  Step 2: Cleaning previous build artifacts...
if exist "build" (
    rmdir /s /q "build"
    echo    removed build\
)
if exist "dist" (
    rmdir /s /q "dist"
    echo    removed dist\
)
REM Clean __pycache__ dirs
for /d %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D"
)
REM Clean .pyc files
del /s /q *.pyc 2>nul
echo    cleaned __pycache__ and .pyc files
echo.

REM ── Step 3: Install/upgrade dependencies ──
echo  Step 3: Installing dependencies...
if exist "requirements.txt" (
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install --upgrade pyinstaller
    echo    dependencies installed
) else (
    echo    no requirements.txt found — installing minimal deps for v3.2
    python -m pip install --upgrade pip
    python -m pip install selenium beautifulsoup4 Pillow pytesseract pyinstaller
    echo    minimal dependencies installed
)
echo.

REM ── Step 4: List .spec files to build ──
echo  Step 4: Looking for .spec files to build...
set SPEC_COUNT=0
for %%S in (*.spec) do (
    set /a SPEC_COUNT+=1
)
if !SPEC_COUNT! equ 0 (
    echo    ERROR: No .spec files found in the repo root.
    echo    Create a .spec file first, or run PyInstaller manually:
    echo      pyinstaller --onefile --windowed --icon vidapay_icon.ico whatsapp_transfer_bot_v3_2_complete_fix.py
    pause
    exit /b 1
)
echo    Found !SPEC_COUNT! .spec file(s):
for %%S in (*.spec) do (
    echo      - %%S
)
echo.

REM ── Step 5: Build every .spec file ──
echo  Step 5: Building .exe files from .spec files...
set BUILD_COUNT=0
set FAIL_COUNT=0

REM ── Redirect PyInstaller workpath to system TEMP ──
REM   Avoids FileNotFoundError: base_library.zip when OneDrive
REM   syncs or AV scans the build folder mid-build.
set "WORKBASE=%TEMP%\pyi_build\vidapay-transfer-bot"
if exist "%WORKBASE%" rmdir /s /q "%WORKBASE%"
mkdir "%WORKBASE%" 2>nul

for %%S in (*.spec) do (
    echo.
    echo    ================================================================
    echo    Building: %%S
    echo    ================================================================
    python -m PyInstaller "%%S" --noconfirm --clean --workpath "%WORKBASE%" 2>&1
    if errorlevel 1 (
        echo.
        echo    FAILED: %%S
        set /a FAIL_COUNT+=1
    ) else (
        echo.
        echo    SUCCESS: %%S
        set /a BUILD_COUNT+=1
    )
)

echo.
echo  ============================================================
echo   Build Summary
echo  ============================================================
echo    Successful builds: !BUILD_COUNT!
echo    Failed builds:     !FAIL_COUNT!
echo.

REM ── Step 6: List produced .exe files ──
if exist "dist" (
    echo  Produced .exe files in dist\:
    dir /b "dist\*.exe" 2>nul
    echo.
    echo  Full paths:
    for %%E in (dist\*.exe) do (
        echo    %%~fE
    )
) else (
    echo  WARNING: dist\ folder not found — all builds may have failed
)

echo.
echo  ============================================================
echo   Done. Copy the .exe files from dist\ to distribute.
echo  ============================================================
echo.
pause
endlocal
