# VidaPay Transfer Bot

Automated inventory reassignment via WhatsApp OCR. Monitors your GFH WhatsApp
groups for transfer requests, extracts IMEIs from message images with
Tesseract OCR, and runs the Inventory Reassignment in the VidaPay CRM - all in
a single browser session.

## Features

- Opens VidaPay CRM first, then WhatsApp Web in a second tab of the same Edge
  browser, so both sites run at the same time and stay open for review
- Reads transfer requests from the configured WhatsApp groups and extracts
  IMEIs from message screenshots via OCR
- Executes Inventory Reassignment transfers automatically and logs results
- Scheduled runs (configurable times) plus a manual Run Now button
- GFH branding (deep navy + signal red) with Light / Dark / System themes
- Auto-installs missing OCR dependencies (Tesseract OCR, Ghostscript, Python
  packages) on first launch

## Requirements

- Windows 10/11 (uses Microsoft Edge via Selenium)
- Python 3.9+

## Setup

    python -m pip install -r requirements.txt

...or double-click `install_deps.bat`. The bot also checks dependencies on
startup and offers to install Tesseract OCR / Ghostscript automatically when
they are missing.

## Usage

Run the file:

    python VidaPay_Transfer_Bot.pyw

(or double-click `VidaPay_Transfer_Bot.pyw`).

1. Enter your CRM Account ID, Username, and Password (saved locally in
   `%APPDATA%\VidaPay_Transfer_Bot\transfer_bot_config.json`, base64-encoded).
2. Add Store Name -> Account ID mappings, or import them from an Excel/CSV file.
3. Set the WhatsApp group names to monitor and a schedule (HH:MM).
4. Press **Run Now** or start the scheduler. Scan the WhatsApp QR code in the
   browser window the bot opens.

## Configuration

- Settings live in `%APPDATA%\VidaPay_Transfer_Bot\transfer_bot_config.json`.
- The browser profile lives in `%LOCALAPPDATA%\VidaPay_WA_Profile` so WhatsApp
  stays logged in between runs.
- Transfer requests are detected by the word "transfer" in group messages and
  matched against your store mappings.

## Notes

- The browser window stays open after a run for manual review; it is closed
  automatically at the start of the next run.
- Ghostscript is optional and only needed for certain image formats - PNG
  screenshots work without it.

## Brand assets

The GFH logo and app icon are embedded directly into the script (base64), so
the `.pyw` works as a single self-contained file. The high-resolution
originals used to build the embedded versions live in `assets/`:

- `assets/GFH_Telecom_Logo.png` - original logo (6512x2275, RGBA)
- `assets/gfh_telecom_llc_icon.ico` - window / taskbar icon
- `assets/gfh_header_logo_preview.png` - downscaled navy-background logo that is embedded in the bot
