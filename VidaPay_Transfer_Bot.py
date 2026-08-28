# VidaPay Automation Suite - Standalone Inventory Transfer Bot
# (No dependency on VidaPay_Device_Ordering_FULL.pyw)

import os
import re
import csv
import time
import json
import queue
import base64
import shutil
import socket
import subprocess
import sys
import urllib.request
import importlib.util
import threading
import tempfile
import requests
from pathlib import Path
try:
    import tkinter as tk
except ImportError:
    import sys
    print("ERROR: tkinter not available. Install Python from python.org (not Microsoft Store).")
    sys.exit(1)
from theme_manager import ThemeManager, apply_theme_to_window, get_copyright_year
from header_manager import FixedHeaderManager
from logo_handler import LogoHandler
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime, date

# Additional requirements
import schedule
import pytesseract
from PIL import Image

# BeautifulSoup for fast HTML parsing of WhatsApp Web / VidaPay CRM pages.
# Selenium's find_elements is slow (one WebDriver round-trip per call);
# BeautifulSoup parses driver.page_source once and extracts all texts in
# pure Python — 10-50x faster for scraping 11 WhatsApp groups.
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# Import the Edge driver class directly (not via the lazy `webdriver.Edge`
# attribute) so PyInstaller bundles selenium.webdriver.edge.webdriver; the
# lazy alias is invisible to static analysis and gets omitted from the EXE.
from selenium.webdriver.edge.webdriver import WebDriver as EdgeDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    WebDriverException,
    NoSuchWindowException,
    InvalidSessionIdException,
)

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

APP_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "VidaPay_Transfer_Bot")
if not os.path.exists(APP_DATA_DIR):
    os.makedirs(APP_DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(APP_DATA_DIR, "transfer_bot_config.json")

# VidaPay brand assets embedded as base64 (injected at build time) so the bot
# stays a single self-contained file. When empty, the PIL-rendered fallbacks
# are used instead.
EMBEDDED_LOGO_B64 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "embedded_logo_b64.txt"), "r").read().strip() if not getattr(sys, "frozen", False) else open(os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "embedded_logo_b64.txt"), "r").read().strip()
EMBEDDED_ICON_B64 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "embedded_icon_b64.txt"), "r").read().strip() if not getattr(sys, "frozen", False) else open(os.path.join(getattr(sys, "_MEIPASS", "."), "assets", "embedded_icon_b64.txt"), "r").read().strip()

# FIX #9: Match parent module brand colors
BRAND_NAVY = "#090d26"
BRAND_RED = "#f0541c"
BRAND_SURFACE = "#f6f7fb"
BRAND_WHITE = "#ffffff"

DEFAULT_GROUPS = (
    "gfh telecom arizona, gfh telecom houston, gfh telecom louisiana, "
    "gfh telecom colorado west, gfh telecom colorado east, gfh telecom tennessee, "
    "mrc, all district managment 2.0, gfh inventory, bo reportin issues, boost boys"
)

# FIX #10: Tesseract path with multiple fallbacks
_TESSERACT_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                 "Tesseract-OCR", "tesseract.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR",
                 "tesseract.exe"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Tesseract-OCR\tesseract.exe",
]

# FIX #12: On Windows Ghostscript ships as gswin64c.exe / gswin32c.exe, not
# "gs" (that name only exists on Unix), so a PATH lookup for "gs" fails on
# machines that have Ghostscript installed normally. Check the real names
# plus the default install layout C:\Program Files\gs\gs<ver>\bin\.
_GHOSTSCRIPT_EXES = ("gswin64c", "gswin32c", "gswin64", "gswin32", "gs")


def _is_tesseract_installed():
    """True when a usable tesseract binary already exists on this machine."""
    if shutil.which("tesseract"):
        return True
    return any(os.path.isfile(p) for p in _TESSERACT_CANDIDATES)


def _ghostscript_installed():
    """True when a Ghostscript binary exists somewhere the bot can use."""
    for name in _GHOSTSCRIPT_EXES:
        if shutil.which(name):
            return True
    if sys.platform.startswith("win"):
        for base in (r"C:\Program Files\gs", r"C:\Program Files (x86)\gs"):
            if not os.path.isdir(base):
                continue
            try:
                for ver_dir in os.listdir(base):
                    bin_dir = os.path.join(base, ver_dir, "bin")
                    if any(
                        os.path.isfile(
                            os.path.join(bin_dir, name + ".exe")
                        )
                        for name in _GHOSTSCRIPT_EXES
                    ):
                        return True
            except OSError:
                continue
    return False


def _locate_tesseract():
    """Return the best known path to tesseract.exe (first match wins)."""
    for p in _TESSERACT_CANDIDATES:
        if os.path.isfile(p):
            return p
    found = shutil.which("tesseract")
    return found if found else _TESSERACT_CANDIDATES[0]


pytesseract.pytesseract.tesseract_cmd = _locate_tesseract()

# Dedicated Edge automation profile + remote debugging, exactly like the
# VidaPay Ordering and Incentive Extractor tools: a REAL Edge window opens
# (installed extensions such as the USA PLANET VPN add-on show in the
# toolbar), you connect your VPN inside it, and Selenium attaches to the
# already-open browser through the remote debugging port.
# Distinct from Extractor (port 9222) and Ordering (port 9223) so
# running multiple VidaPay tools at once each gets its own Edge
# process/window instead of colliding on a shared profile+port and
# opening as tabs inside whichever tool launched first.
AUTOMATION_PROFILE_DIR = r"C:\VidaPay_Edge_Automation_Profile_TransferBot"
REMOTE_DEBUGGING_PORT = 9224
ATTACH_TO_OPEN_EDGE = True
PAGE_LOAD_TIMEOUT = 90

# "Wait for reply" feature: when a transfer request is detected, the bot
# waits this many seconds before processing it.  During the wait it re-scans
# the WhatsApp group for handling replies ("on it", "doing", etc.).  If a
# reply is found, the transfer is SKIPPED.  If NO reply is found by the end
# of the wait, the bot posts "on it" in the group itself and then processes
# the transfer.
WAIT_FOR_REPLY_SECONDS = 30
# Re-check interval during the wait period.
REPLY_CHECK_INTERVAL_SECONDS = 15
# The message the bot sends to claim a transfer when no human has replied.
BOT_CLAIM_REPLY = "on it"

# Human-verification wait: Cloudflare Turnstile / reCAPTCHA auto-solver loop
# polls every few seconds; 30 s is the upper bound before giving up.
HUMAN_VERIFY_WAIT_SECONDS = 30

CRM_MAIN_PANEL_URL = "https://www.vidapaycrm.com/Main%20Panel.aspx"
CRM_LOGIN_URL = "https://www.vidapaycrm.com/Login.aspx"

# ----------------------------------------------------------------------------
# VidaPay THEME PALETTES (light / dark) - brand: deep navy + signal red
# ----------------------------------------------------------------------------
THEMES = {
    "light": {
        "bg": "#f6f7fb",
        "panel": "#ffffff",
        "panel_alt": "#eef0f6",
        "text": "#16213a",
        "text_dim": "#5b6478",
        "input": "#ffffff",
        "border": "#d5d9e5",
        "navy": "#090d26",
        "red": "#f0541c",
        "log_bg": "#0f1830",
        "log_fg": "#e2e8f0",
    },
    "dark": {
        "bg": "#0b1020",
        "panel": "#141b38",
        "panel_alt": "#1c2447",
        "text": "#e8ecf7",
        "text_dim": "#9aa4c0",
        "input": "#1c2447",
        "border": "#2b3561",
        "navy": "#090d26",
        "red": "#f0541c",
        "log_bg": "#05070f",
        "log_fg": "#cbd5e1",
    },
}


# ----------------------------------------------------------------------------
# OCR DEPENDENCY AUTO-SETUP (Tesseract + Ghostscript + Python packages)
# ----------------------------------------------------------------------------
TESSERACT_URL = (
    "https://digi.bib.uni-mannheim.de/tesseract/"
    "tesseract-ocr-w64-setup-5.3.3.20231005.exe"
)
# import name -> pip distribution name
PIP_DEPENDENCIES = {
    "selenium": "selenium",
    "PIL": "pillow",
    "pytesseract": "pytesseract",
    "schedule": "schedule",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
}


def _tool_on_path(name):
    return shutil.which(name) is not None


def _safe_print(*args):
    """print() that never raises (sys.stdout is None in windowed exes)."""
    try:
        print(*args)
    except Exception:
        pass


def _pip_cmd():
    """Return a pip command usable in this Python, even inside a frozen
    PyInstaller exe where sys.executable points at the exe itself."""
    if getattr(sys, "frozen", False):
        for cand in ("python", "python3", "py"):
            found = shutil.which(cand)
            if found:
                return [found, "-m", "pip"]
        return None
    return [sys.executable, "-m", "pip"]


def _missing_python_packages():
    return [mod for mod in PIP_DEPENDENCIES
            if importlib.util.find_spec(mod) is None]


def _run_cmd_quiet(cmd, timeout=300):
    """Run a command and return (ok, output). Never raises."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode == 0, output
    except Exception as e:
        return False, str(e)


def _download_file(url, dest, log, timeout=240):
    try:
        log("Downloading %s ..." % os.path.basename(url))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(
            dest, "wb"
        ) as out:
            shutil.copyfileobj(resp, out)
        return os.path.isfile(dest) and os.path.getsize(dest) > 0
    except Exception as e:
        log("Download failed: %s" % e)
        return False


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        _safe_print(f"Error saving config: {e}")


def encode_pw(pw):
    return base64.b64encode(pw.encode()).decode() if pw else ""


def decode_pw(pw):
    try:
        return base64.b64decode(pw.encode()).decode() if pw else ""
    except Exception:
        return ""


# ============================================================================
# BOT LOGIC (WhatsApp & CRM)
# ============================================================================


def _inject_anti_detection(driver):
    """Remove webdriver flag so VidaPay / Cloudflare don't block the browser."""
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined, configurable: true
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5], configurable: true
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'], configurable: true
                    });
                    window.chrome = { runtime: {} };
                """
            }
        )
    except Exception:
        pass


def get_current_url_lower(driver):
    """Return the driver's current URL, lower-cased, never raising."""
    try:
        return (driver.current_url or "").lower()
    except Exception:
        return ""


def get_body_text_lower(driver):
    """Return the page body's text, lower-cased, never raising."""
    try:
        return (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        return ""


def _is_human_verification_page(driver):
    """
    Detect Cloudflare / reCAPTCHA / Turnstile human-verification pages.

    Uses the get_current_url_lower / get_body_text_lower helpers so that any
    Selenium exceptions (closed tab, lost session, half-loaded DOM) don't blow
    up the caller.  Mirrors is_human_verification_page() in VidaPay Device
    Ordering so behaviour stays identical across the two bots.
    """
    current_url = get_current_url_lower(driver)
    body_text = get_body_text_lower(driver)

    text_markers = [
        "verify you are human",
        "verify human",
        "confirm you are human",
        "checking your browser",
        "security check",
        "cloudflare",
        "cf-turnstile",
        "turnstile",
        "review the security of your connection",
        "i'm not a robot",
        "not a robot",
        "recaptcha",
    ]

    if any(marker in body_text for marker in text_markers):
        return True

    if "challenge" in current_url and ("cloudflare" in body_text or "verify" in body_text):
        return True

    try:
        return bool(
            driver.execute_script(
                """
                function visible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return (
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        style.opacity !== '0' &&
                        rect.width > 0 &&
                        rect.height > 0 &&
                        el.getClientRects().length > 0
                    );
                }

                const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                const hasVerifyText = bodyText.includes('verify') || bodyText.includes('human') || bodyText.includes('cloudflare') || bodyText.includes('robot');
                const visibleCheckbox = Array.from(document.querySelectorAll('input[type="checkbox"]')).some(visible);
                const turnstile = !!document.querySelector('[name="cf-turnstile-response"], .cf-turnstile, iframe[src*="turnstile"], iframe[src*="cloudflare"], iframe[src*="challenge"]');
                const recaptcha = !!document.querySelector('iframe[src*="recaptcha"], .g-recaptcha, #recaptcha-anchor, #rc-anchor-container');

                return (visibleCheckbox && hasVerifyText) || turnstile || recaptcha;
                """
            )
        )
    except Exception:
        return False


def _wait_for_human_verification_clear(driver, stop_event=None,
                                       timeout=HUMAN_VERIFY_WAIT_SECONDS,
                                       log=print, context=""):
    """
    Block until the human-verification page is gone or timeout.

    Enhanced version (mirrors VidaPay Device Ordering):
      - Detects Cloudflare Turnstile by CSS selector and waits 15 s for the
        iframe JS to fully load before clicking.
      - Calls try_auto_click_human_verification up to 3 times (Turnstile /
        reCAPTCHA audio solver / plain checkbox dispatcher).
      - Keeps retrying the auto-click every 5 s until either the page
        advances or HUMAN_VERIFY_WAIT_SECONDS elapses.
      - stop_event.is_set() is honoured so the user can cancel mid-solve.
    """
    if not _is_human_verification_page(driver):
        return True

    label = f" during {context}" if context else ""

    # Cloudflare Turnstile needs ~15 s to fully load its iframe JS before a
    # real mouse click will register.  reCAPTCHA handles its own timing inside
    # try_solve_recaptcha — no extra wait needed there.
    is_turnstile = False
    try:
        is_turnstile = bool(driver.find_elements(
            By.CSS_SELECTOR,
            "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile'], "
            ".cf-turnstile, [class*='turnstile'], #challenge-stage",
        ))
    except Exception:
        pass

    if is_turnstile:
        log(f"Cloudflare Turnstile detected{label}. Waiting 15 seconds for iframe to fully load...")
        for _i in range(15):
            if stop_event is not None and stop_event.is_set():
                return False
            time.sleep(1)
        # If it cleared on its own, continue.
        if not _is_human_verification_page(driver):
            log("Human verification cleared during wait. Continuing automation.")
            try:
                wait_for_body(driver, timeout=15)
            except Exception:
                pass
            return True
    else:
        log(f"Human verification detected{label}. Attempting auto-click...")

    # Try auto-clicking the widget up to 3 times with short delays.
    for attempt in range(3):
        if stop_event is not None and stop_event.is_set():
            return False
        if try_auto_click_human_verification(driver, log=log):
            time.sleep(2)
            if not _is_human_verification_page(driver):
                log("Human verification cleared after auto-click. Continuing automation.")
                try:
                    wait_for_body(driver, timeout=15)
                except Exception:
                    pass
                return True
        time.sleep(2)

    log("Auto-click did not clear human verification. Waiting for page to advance...")

    end_time = time.time() + timeout
    last_log = 0

    while time.time() < end_time:
        if stop_event is not None and stop_event.is_set():
            return False

        # Keep retrying auto-click every 5 seconds.
        try_auto_click_human_verification(driver, log=log)

        if not _is_human_verification_page(driver):
            log("Human verification cleared. Continuing automation.")
            try:
                wait_for_body(driver, timeout=15)
            except Exception:
                pass
            time.sleep(1)
            return True

        now = time.time()
        if now - last_log >= 10:
            remaining = int(end_time - now)
            log(f"Still on human verification page. Retrying auto-click. Time left: {remaining} seconds.")
            last_log = now

        time.sleep(5)

    log("Human verification did not clear in time. Stopping this run.")
    return False


# ----------------------------------------------------------------------------
# EDGE / VPN BROWSER HELPERS (ported from the VidaPay Ordering and Incentive
# Extractor bots)
#
# Instead of launching the browser with Selenium directly, these helpers open
# a REAL Microsoft Edge window (via subprocess + remote debugging on port
# 9222, using a dedicated automation profile). That way the user's installed
# extensions - including the USA PLANET VPN add-on - show in the toolbar, and
# the user can connect their VPN inside the plain browser window. Selenium
# then ATTACHES to the already-open browser through the remote debugging port,
# so the VPN connection and the visible extensions are kept.
# ----------------------------------------------------------------------------

def get_edge_exe_path():
    possible_paths = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]

    for path in possible_paths:
        if path and os.path.exists(path):
            return path

    return None


def is_port_open(host="127.0.0.1", port=REMOTE_DEBUGGING_PORT, timeout=1):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _edge_base_args(url):
    return [
        get_edge_exe_path(),
        f"--remote-debugging-port={REMOTE_DEBUGGING_PORT}",
        f"--user-data-dir={AUTOMATION_PROFILE_DIR}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def open_edge_url_in_real_tab(url="about:blank", log=print):
    """Force Edge to open a normal browser tab in the automation profile."""
    edge_path = get_edge_exe_path()

    if not edge_path:
        log("Microsoft Edge executable not found.")
        return False

    os.makedirs(AUTOMATION_PROFILE_DIR, exist_ok=True)

    try:
        subprocess.Popen(_edge_base_args(url))
        log(f"Opened Edge normal tab for URL: {url}")
        return True
    except Exception as e:
        log(f"Failed to open Edge normal tab: {e}")
        return False


def get_driver_handles(driver):
    try:
        return list(driver.window_handles)
    except (NoSuchWindowException, InvalidSessionIdException, WebDriverException):
        return []
    except Exception:
        return []


def is_browser_chrome_url(url):
    lower_url = (url or "").lower().strip()

    browser_chrome_prefixes = (
        "edge://",
        "chrome://",
        "devtools://",
        "edge-extension://",
        "chrome-extension://",
    )

    return lower_url.startswith(browser_chrome_prefixes)


def switch_to_first_live_content_tab(driver, log=print):
    handles = get_driver_handles(driver)

    if not handles:
        return False

    fallback_handle = None

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            try:
                current_url = driver.current_url or ""
            except Exception:
                current_url = ""

            if not fallback_handle:
                fallback_handle = handle

            if current_url and not is_browser_chrome_url(current_url):
                return True

            if current_url in ("about:blank", "data:,", ""):
                return True

        except Exception:
            continue

    if fallback_handle:
        try:
            driver.switch_to.window(fallback_handle)
            return True
        except Exception:
            return False

    return False


def open_blank_normal_tab(driver, log=print):
    """Open a plain web-content tab and switch Selenium to it."""
    before_handles = set(get_driver_handles(driver))

    if not switch_to_first_live_content_tab(driver, log=log):
        open_edge_url_in_real_tab("about:blank", log=log)
        time.sleep(1.5)

    try:
        driver.switch_to.new_window("tab")
        time.sleep(0.5)
        log("Created fresh Edge tab for automation.")
        return True
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd("Target.createTarget", {"url": "about:blank"})
        time.sleep(1)

        after_handles = get_driver_handles(driver)
        new_handles = [handle for handle in after_handles if handle not in before_handles]

        for handle in reversed(new_handles or after_handles):
            try:
                driver.switch_to.window(handle)
                log("Created fresh Edge tab through DevTools.")
                return True
            except Exception:
                continue
    except Exception:
        pass

    open_edge_url_in_real_tab("about:blank", log=log)
    time.sleep(1.5)

    return switch_to_first_live_content_tab(driver, log=log)


def prepare_edge_automation_tab(driver, log=print):
    if open_blank_normal_tab(driver, log=log):
        try:
            driver.get("about:blank")
        except Exception:
            pass
        return True

    log("Could not prepare a normal Edge tab for automation.")
    return False


def wait_for_body(driver, timeout=PAGE_LOAD_TIMEOUT):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


def open_url_in_edge_tab(driver, url, timeout=PAGE_LOAD_TIMEOUT, log=print):
    """Navigate a real Edge web tab to a URL, recovering from closed/UI targets."""
    if not switch_to_first_live_content_tab(driver, log=log):
        if not prepare_edge_automation_tab(driver, log=log):
            return False

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    if is_browser_chrome_url(current_url) or not current_url:
        if not prepare_edge_automation_tab(driver, log=log):
            return False

    log(f"Opening URL in Edge address bar: {url}")

    try:
        driver.get(url)
        wait_for_body(driver, timeout=timeout)
        return True
    except (NoSuchWindowException, InvalidSessionIdException, WebDriverException) as first_error:
        log(f"Direct navigation hit a closed/non-page target. Recovering: {first_error}")

        try:
            prepare_edge_automation_tab(driver, log=log)
            driver.get(url)
            wait_for_body(driver, timeout=timeout)
            return True
        except Exception as second_error:
            log(f"Recovered navigation failed: {second_error}")
            return False
    except Exception as e:
        log(f"Navigation failed: {e}")
        return False


def open_vpn_setup_browser(url=None, log=print):
    """Launch the dedicated Edge automation browser for VPN setup if not running."""
    if url is None:
        url = CRM_MAIN_PANEL_URL

    os.makedirs(AUTOMATION_PROFILE_DIR, exist_ok=True)

    if is_port_open():
        log("Automation Edge is already open and ready.")
        log("Use that Edge window. Confirm VPN is connected there.")
        return True

    edge_path = get_edge_exe_path()

    if not edge_path:
        log("Microsoft Edge executable not found.")
        return False

    try:
        subprocess.Popen(_edge_base_args(url))
        log("Opened dedicated Edge automation browser.")
        log("Connect VPN inside this Edge window.")
        log("Keep this Edge window open while running the bot.")

        for _ in range(20):
            if is_port_open():
                log("Automation Edge remote connection is ready.")
                return True

            time.sleep(0.5)

        log("Edge opened, but remote debugging port is not ready yet.")
        log("Wait a few seconds, then try again.")
        return False

    except Exception as e:
        log(f"Failed to open VPN setup browser: {e}")
        return False


def create_edge_driver(log=print, attach=None):
    """Start (or attach to) the automation Edge browser and return a Selenium driver.

    attach=True  → attach to the dedicated VPN-setup Edge window (remote debugging).
    attach=False → launch the automation browser directly, standalone.
    attach=None  → use the ATTACH_TO_OPEN_EDGE build constant.
    """
    if ATTACH_TO_OPEN_EDGE if attach is None else attach:
        if not is_port_open():
            log("Automation Edge is not open.")
            log("Opening VPN Browser Setup now.")
            open_vpn_setup_browser(log=log)

        if not is_port_open():
            raise WebDriverException(
                "Automation Edge is not available on remote debugging port. "
                "Open VPN Browser Setup first and keep that Edge window open."
            )

        options = Options()
        options.add_experimental_option(
            "debuggerAddress",
            f"127.0.0.1:{REMOTE_DEBUGGING_PORT}"
        )
        # Auto-dismiss any lingering JS confirm()/alert() dialogs (e.g. the
        # "Delete this SIM entry?" prompt that gets left behind after a
        # previous transfer attempt).  Without this, msedgedriver on Windows
        # crashes with a GetHandleVerifier stacktrace the moment driver.get()
        # is called while an alert is open.
        options.set_capability("unhandledPromptBehavior", "dismiss")
        options.set_capability("acceptInsecureCerts", True)

        driver = EdgeDriver(options=options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

        if not prepare_edge_automation_tab(driver, log=log):
            raise WebDriverException(
                "Edge is open, but Selenium could not attach to a normal browser tab. "
                "Close the Edge Downloads popup/flyout and try again."
            )

        _inject_anti_detection(driver)
        return driver

    # Standalone (non-attach) launch of the automation profile.
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument(f"--user-data-dir={AUTOMATION_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Auto-dismiss any lingering JS confirm()/alert() dialogs so that
    # navigation does not crash msedgedriver (see attach path for details).
    options.set_capability("unhandledPromptBehavior", "dismiss")
    options.set_capability("acceptInsecureCerts", True)

    driver = EdgeDriver(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    _inject_anti_detection(driver)
    return driver



def create_logo_label(frame, width=100, height=50):
    """Create logo label with fallback to text"""
    try:
        from PIL import Image, ImageTk
        logo_files = ["VidaPay_Logo.png", "vidapay_logo.png", "logo.png"]
        for logo_file in logo_files:
            if Path(logo_file).exists():
                img = Image.open(logo_file)
                img.thumbnail((width, height), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                label = tk.Label(frame, image=photo, bg=BRAND_NAVY)
                label.image = photo  # Keep reference
                return label
    except Exception:
        pass
    # Fallback to text
    return tk.Label(frame, text="VidaPay", font=("Segoe UI", 16, "bold"), fg=BRAND_RED, bg=BRAND_NAVY)


# ------------------------------------------------------------------------
# HUMAN-VERIFICATION AUTO-SOLVER (ported from VidaPay Device Ordering)
#
# Cloudflare Turnstile real-OS-mouse clicker (pyautogui) and Google
# reCAPTCHA v2 audio-challenge solver.  All JavaScript-driven so they
# are dual-monitor safe.  try_auto_click_human_verification is the
# dispatcher used by _wait_for_human_verification_clear above.
# ------------------------------------------------------------------------

def _pyautogui_click_turnstile(driver, log=print):
    """
    Click the Cloudflare Turnstile checkbox using a real OS-level mouse click
    via pyautogui.  This is the only method that works against the sandboxed
    cross-origin Turnstile iframe — JS events from the parent are blocked.

    Strategy:
      1. Focus the Edge browser window (so the click lands on the right window).
      2. Find the Turnstile iframe element.
      3. Get its viewport-relative rect via getBoundingClientRect().
      4. Get the browser window's screen position via CDP (LayoutMetrics /
         Window.getBounds) so we know where the viewport is on screen.
      5. Compute absolute screen coords of the checkbox (left edge + 24 px,
         vertically centred) and click with pyautogui.
    """
    try:
        import pyautogui as _pg
        _pg.FAILSAFE = False
    except ImportError:
        log("pyautogui not available — cannot click Turnstile via real mouse.")
        return False

    # ── Focus the Edge browser window BEFORE clicking ───────────────────────
    # pyautogui.click() sends a real OS mouse event. If Edge isn't the
    # foreground window, the click lands on whatever IS in front. This is
    # why the user had to manually click the window to make it work.
    try:
        if sys.platform == "win32":
            import ctypes
            import win32gui
            import win32con

            # Get the Edge window handle from the Selenium driver
            # Edge's window title contains the page title
            edge_window_title = ""
            try:
                edge_window_title = driver.title or ""
            except Exception:
                pass

            # Find the Edge window by title
            def _find_edge_window(hwnd, result):
                title = win32gui.GetWindowText(hwnd)
                if title and ("Edge" in title or edge_window_title in title):
                    if win32gui.IsWindowVisible(hwnd):
                        result.append(hwnd)
                return True

            windows = []
            win32gui.EnumWindows(_find_edge_window, windows)

            if windows:
                hwnd = windows[0]
                # Restore if minimized
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                # Bring to foreground
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.5)  # let the window fully focus
                log("Focused Edge window for Turnstile click.")
            else:
                # Fallback: use the driver's window handle directly
                try:
                    # Selenium 4: driver.service.process gives the process
                    # but we need the window. Use driver.current_window_handle
                    # to get the tab, then find its OS window.
                    pass
                except Exception:
                    pass
    except Exception as focus_err:
        log(f"Window focus failed (will try clicking anyway): {focus_err}")

    # Locate the Turnstile iframe
    _TURNSTILE_IFRAME_SELECTORS = [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='Widget']",
        "iframe[title*='verify']",
        "iframe[src*='cloudflare']",
    ]
    iframe_el = None
    for sel in _TURNSTILE_IFRAME_SELECTORS:
        try:
            iframe_el = driver.find_element(By.CSS_SELECTOR, sel)
            break
        except Exception:
            pass

    if iframe_el is None:
        # Also try any iframe that has a checkbox inside (switches context)
        log("No Turnstile iframe selector matched.")
        return False

    try:
        # Scroll the iframe into view
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", iframe_el)
        time.sleep(0.5)

        # Get iframe viewport rect
        rect = driver.execute_script(
            "const r = arguments[0].getBoundingClientRect();"
            "return {left: r.left, top: r.top, width: r.width, height: r.height};",
            iframe_el,
        )
        if not rect or rect["width"] == 0:
            log("Turnstile iframe has zero size — not yet rendered.")
            return False

        # Get the browser window's screen position.
        # CDP LayoutMetrics gives us the visual viewport offset inside the tab,
        # and window.screenX/Y gives us the window's OS position.
        win_pos = driver.execute_script(
            "return {x: window.screenX || window.screenLeft || 0,"
            "        y: window.screenY || window.screenTop  || 0,"
            "        dpr: window.devicePixelRatio || 1};"
        )

        # Edge/Chrome reports coordinates in CSS pixels; pyautogui works in
        # physical pixels.  On a 100% DPI display dpr=1 so this is a no-op.
        # On HiDPI (dpr=2) we must NOT scale because pyautogui already works
        # in logical (CSS) pixels on Windows.  Leave dpr out of the calc.

        # Estimate the browser chrome height (address bar + tabs).
        # We use the difference between the outer window height and the
        # inner viewport height.
        chrome_h = driver.execute_script(
            "return (window.outerHeight - window.innerHeight) || 100;"
        )

        # Checkbox sits ~24 px from the left edge, vertically centred in the iframe
        checkbox_vp_x = rect["left"] + 24
        checkbox_vp_y = rect["top"]  + rect["height"] / 2

        screen_x = int(win_pos["x"] + checkbox_vp_x)
        screen_y = int(win_pos["y"] + chrome_h + checkbox_vp_y)

        log(f"Turnstile iframe at viewport ({rect['left']:.0f},{rect['top']:.0f}) "
            f"size {rect['width']:.0f}×{rect['height']:.0f} | "
            f"window screen pos ({win_pos['x']},{win_pos['y']}) chrome_h={chrome_h} | "
            f"clicking screen ({screen_x},{screen_y})")

        _pg.moveTo(screen_x, screen_y, duration=0.3)
        time.sleep(0.1)
        _pg.click(screen_x, screen_y)
        log("pyautogui clicked Turnstile checkbox (real OS mouse click).")
        return True

    except Exception as exc:
        log(f"pyautogui Turnstile click error: {exc}")
        return False

def try_auto_click_human_verification(driver, log=print):
    """
    Click the visible human-verification widget.

    A. Google reCAPTCHA → audio solver (try_solve_recaptcha).
    B. Cloudflare Turnstile → real OS mouse click via pyautogui (only method
       that works against the sandboxed cross-origin Turnstile iframe).
    C. Plain visible <input type="checkbox"> in the main document → JS click.
    """

    # ------------------------------------------------------------------ #
    # A. Detect reCAPTCHA → audio solver                                  #
    # ------------------------------------------------------------------ #
    _RECAPTCHA_ANCHOR_SELECTORS = [
        "iframe[src*='recaptcha/api2/anchor']",
        "iframe[src*='recaptcha/enterprise/anchor']",
        "iframe[title*='reCAPTCHA']",
        "iframe[title*='not a robot']",
    ]
    for sel in _RECAPTCHA_ANCHOR_SELECTORS:
        try:
            if driver.find_element(By.CSS_SELECTOR, sel):
                log("reCAPTCHA anchor iframe detected — running audio solver directly.")
                return try_solve_recaptcha(driver, log=log)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # B. Cloudflare Turnstile → pyautogui real mouse click               #
    # ------------------------------------------------------------------ #
    _TURNSTILE_PRESENT_SELECTORS = [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='Widget']",
        ".cf-turnstile",
        "[class*='turnstile']",
        "#challenge-stage",
    ]
    for sel in _TURNSTILE_PRESENT_SELECTORS:
        try:
            if driver.find_element(By.CSS_SELECTOR, sel):
                log(f"Cloudflare Turnstile detected ({sel}) — using real mouse click.")
                return _pyautogui_click_turnstile(driver, log=log)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # C. Plain visible checkbox in main document                          #
    # ------------------------------------------------------------------ #
    try:
        clicked = driver.execute_script(
            """
            function visible(el) {
                if (!el) return false;
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' &&
                       s.opacity !== '0' && r.width > 0 && r.height > 0;
            }
            const box = Array.from(document.querySelectorAll('input[type="checkbox"]'))
                            .find(el => visible(el) && !el.checked);
            if (!box) return false;
            box.scrollIntoView({block:'center'});
            box.focus();
            box.checked = true;
            box.dispatchEvent(new Event('change', {bubbles:true}));
            box.dispatchEvent(new Event('input',  {bubbles:true}));
            box.click();
            return true;
            """
        )
        if clicked:
            log("JS-clicked plain checkbox in main document.")
            return True
    except Exception:
        pass

    return False

# ---------------------------------------------------------------------------
# reCAPTCHA v2 audio solver helpers
# ---------------------------------------------------------------------------

def _download_recaptcha_audio(driver, url, tmp_dir, log=print):
    """Download the reCAPTCHA audio MP3 using browser cookies."""
    try:
        import requests as _req
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        headers = {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Referer": driver.current_url,
        }
        resp = _req.get(url, cookies=cookies, headers=headers, timeout=25)
        resp.raise_for_status()
        mp3_path = os.path.join(tmp_dir, "rc_audio.mp3")
        with open(mp3_path, "wb") as fh:
            fh.write(resp.content)
        log(f"Downloaded reCAPTCHA audio ({len(resp.content):,} bytes).")
        return mp3_path
    except Exception as exc:
        log(f"Audio download failed: {exc}")
        return None

def _get_ffmpeg_exe(log=print):
    """
    Return a working ffmpeg executable path.
    Tries in order:
      1. 'ffmpeg' on PATH
      2. Common Windows install locations
      3. imageio-ffmpeg bundled binary (auto-installs the package if needed)
    """
    # 1. PATH + common locations
    candidates = ["ffmpeg", "ffmpeg.exe"]
    common = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        str(Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe"),
    ]
    for exe in candidates + common:
        try:
            r = subprocess.run([exe, "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return exe
        except Exception:
            pass

    # 2. imageio-ffmpeg — downloads a real static ffmpeg binary via pip
    try:
        try:
            import imageio_ffmpeg as _iio
        except ImportError:
            log("Installing imageio-ffmpeg (downloads real ffmpeg binary)...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "imageio-ffmpeg",
                 "--quiet", "--disable-pip-version-check"],
                capture_output=True, timeout=120,
            )
            import imageio_ffmpeg as _iio
        exe = _iio.get_ffmpeg_exe()
        r = subprocess.run([exe, "-version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            log(f"Using ffmpeg via imageio-ffmpeg: {exe}")
            return exe
    except Exception as exc:
        log(f"imageio-ffmpeg failed: {exc}")

    return None

def _mp3_to_wav(mp3_path, wav_path, log=print):
    """Convert MP3 to 16kHz mono WAV using the best available ffmpeg. Returns True on success."""
    exe = _get_ffmpeg_exe(log=log)
    if not exe:
        log("No ffmpeg executable found — cannot convert MP3 to WAV.")
        return False
    try:
        result = subprocess.run(
            [exe, "-y", "-i", mp3_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(wav_path):
            log("Converted MP3→WAV (16kHz mono).")
            return True
        log(f"ffmpeg returned {result.returncode}: {result.stderr[-200:]}")
    except Exception as exc:
        log(f"MP3→WAV conversion error: {exc}")
    return False

def _transcribe_wav_google(wav_path, log=print):
    """Transcribe a WAV file using Google Speech Recognition via SpeechRecognition."""
    try:
        import speech_recognition as _sr
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "SpeechRecognition",
                 "--quiet", "--disable-pip-version-check"],
                capture_output=True, timeout=60,
            )
            import speech_recognition as _sr
        except Exception as exc:
            log(f"Cannot install SpeechRecognition: {exc}")
            return None
    try:
        rec = _sr.Recognizer()
        with _sr.AudioFile(wav_path) as src:
            audio = rec.record(src)
        text = rec.recognize_google(audio).lower().strip()
        log(f"Transcribed: '{text}'")
        return text
    except Exception as exc:
        log(f"Google STT failed: {exc}")
        return None

def _transcribe_wav_whisper(wav_path, log=print):
    """
    Transcribe a WAV using OpenAI Whisper (local, no API key).
    Auto-installs 'openai-whisper' on first use (~40MB, one-time download).
    """
    try:
        try:
            import whisper as _whisper
        except ImportError:
            log("Installing openai-whisper (one-time, ~40 MB)...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "openai-whisper",
                 "--quiet", "--disable-pip-version-check"],
                capture_output=True, timeout=180,
            )
            import whisper as _whisper
        model = _whisper.load_model("tiny")   # smallest/fastest model
        result = model.transcribe(wav_path, language="en", fp16=False)
        text = result.get("text", "").lower().strip()
        if text:
            log(f"Whisper transcribed: '{text}'")
            return text
    except Exception as exc:
        log(f"Whisper transcription failed: {exc}")
    return None

def _transcribe_audio_mp3(mp3_path, log=print):
    """
    Transcribe a reCAPTCHA MP3 audio challenge.

    Flow:
      1. Convert MP3 → WAV using real ffmpeg (via imageio-ffmpeg auto-install).
      2. Transcribe WAV with Google Speech Recognition (SpeechRecognition library).
      3. If Google STT fails, transcribe with OpenAI Whisper (local, no API key).
    """
    wav_path = mp3_path.replace(".mp3", ".wav")
    text = None

    try:
        if _mp3_to_wav(mp3_path, wav_path, log=log):
            # Try Google STT first (fast, online)
            text = _transcribe_wav_google(wav_path, log=log)

            # Whisper fallback (local, offline)
            if not text:
                text = _transcribe_wav_whisper(wav_path, log=log)
        else:
            # No ffmpeg at all — try Whisper directly on the MP3
            log("No ffmpeg — trying Whisper directly on MP3...")
            text = _transcribe_wav_whisper(mp3_path, log=log)
    finally:
        for p in (mp3_path, wav_path):
            try:
                os.remove(p)
            except Exception:
                pass

    if not text:
        log("All transcription methods failed.")
    return text

def _js_click(driver, selector_or_id, by_id=False, log=print):
    """
    Click an element purely through JavaScript — no ActionChains, no physical
    mouse coordinates.  Safe on dual-monitor setups where ActionChains coords
    are offset by the secondary display position.

    Uses the full synthetic event chain that Google's reCAPTCHA widget listens to:
      pointerover → mouseover → pointermove → mousemove →
      pointerdown  → mousedown → pointerup → mouseup → click
    """
    js = """
    const sel = arguments[0];
    const byId = arguments[1];
    const el = byId ? document.getElementById(sel)
                    : document.querySelector(sel);
    if (!el) return 'NOT_FOUND';
    el.scrollIntoView({block: 'center', inline: 'center'});
    const rect  = el.getBoundingClientRect();
    const cx    = rect.left + rect.width  / 2;
    const cy    = rect.top  + rect.height / 2;
    const opts  = {bubbles: true, cancelable: true, view: window,
                   clientX: cx, clientY: cy};
    ['pointerover','mouseover','pointermove','mousemove',
     'pointerdown','mousedown','pointerup','mouseup','click'
    ].forEach(t => el.dispatchEvent(new MouseEvent(t, opts)));
    return 'CLICKED';
    """
    try:
        result = driver.execute_script(js, selector_or_id, by_id)
        if result == "CLICKED":
            return True
        log(f"_js_click: element not found — {'#' if by_id else ''}{selector_or_id}")
        return False
    except Exception as exc:
        log(f"_js_click error ({'#' if by_id else ''}{selector_or_id}): {exc}")
        return False

def _js_get_attr(driver, css_selector, attr):
    """Return the value of an attribute on the first matching element, or None."""
    try:
        return driver.execute_script(
            "const el = document.querySelector(arguments[0]);"
            "return el ? el.getAttribute(arguments[1]) || el[arguments[1]] : null;",
            css_selector, attr,
        )
    except Exception:
        return None

def _js_set_value(driver, element_id, text):
    """Set an input field value and fire input/change events — cross-origin safe."""
    try:
        driver.execute_script(
            """
            const f = document.getElementById(arguments[0]);
            if (!f) return false;
            f.focus();
            // Simulate real keystrokes so React/Angular state picks up the value
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(f, arguments[1]);
            f.dispatchEvent(new Event('input',  {bubbles: true}));
            f.dispatchEvent(new Event('change', {bubbles: true}));
            f.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
            return true;
            """,
            element_id, text,
        )
        return True
    except Exception:
        return False

def try_solve_recaptcha(driver, log=print):
    """
    Google reCAPTCHA v2 audio-challenge solver — dual-monitor safe.

    Uses ONLY JavaScript events (no ActionChains / physical mouse moves) so
    screen coordinates on secondary monitors never cause misclicks.

    Flow:
      1. Wait 10 s for the widget to fully render.
      2. Switch into anchor iframe → JS-click .recaptcha-checkbox-border.
      3. Switch back, wait 3 s for the challenge bframe popup.
      4. Switch into bframe → if an image grid appears, JS-click the
         headphone/audio button (#recaptcha-audio-button) to switch to audio.
      5. Wait for audio panel, read .rc-audiochallenge-tdownload-link href.
      6. Download + transcribe the MP3.
      7. Switch back into bframe → JS-set #audio-response → JS-click
         #recaptcha-verify-button.
      8. Switch to main doc → JS-click #btnClick (orange login button).
    """
    import tempfile

    _ANCHOR_CSS = [
        "iframe[src*='recaptcha/api2/anchor']",
        "iframe[src*='recaptcha/enterprise/anchor']",
        "iframe[title*='reCAPTCHA']",
        "iframe[title*='not a robot']",
    ]
    _BFRAME_CSS = [
        "iframe[src*='recaptcha/api2/bframe']",
        "iframe[src*='recaptcha/enterprise/bframe']",
        "iframe[title*='recaptcha challenge']",
        "iframe[title*='challenge expires']",
    ]

    # ---- Step 1: locate anchor iframe ----
    anchor_iframe = None
    for sel in _ANCHOR_CSS:
        try:
            anchor_iframe = driver.find_element(By.CSS_SELECTOR, sel)
            break
        except Exception:
            pass

    if anchor_iframe is None:
        return False  # no reCAPTCHA on this page

    log("reCAPTCHA v2 detected — starting audio solver...")
    time.sleep(2)

    try:
        # ---- Step 2: JS-click .recaptcha-checkbox-border inside anchor iframe ----
        driver.switch_to.frame(anchor_iframe)
        log("Switched into reCAPTCHA anchor iframe.")

        # Wait for checkbox to appear
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".recaptcha-checkbox-border"))
        )

        clicked_checkbox = _js_click(driver, ".recaptcha-checkbox-border", by_id=False, log=log)
        if not clicked_checkbox:
            # fallback to the wrapper span
            clicked_checkbox = _js_click(driver, "#recaptcha-anchor", by_id=True, log=log)

        if clicked_checkbox:
            log("Clicked reCAPTCHA checkbox (JS — dual-monitor safe).")
        else:
            log("Could not click reCAPTCHA checkbox.")
            driver.switch_to.default_content()
            return False

        driver.switch_to.default_content()
        log("Waiting 3 s for challenge popup to appear...")
        time.sleep(3)

        # ---- Step 3: locate bframe ----
        bframe = None
        for sel in _BFRAME_CSS:
            try:
                bframe = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                break
            except Exception:
                pass

        if bframe is None:
            log("No challenge popup — reCAPTCHA passed via checkbox alone.")
            _click_login_verify_button(driver, log=log)
            return True

        # ---- Step 4: switch into bframe, click audio button ----
        driver.switch_to.frame(bframe)
        log("Switched into reCAPTCHA challenge bframe.")
        time.sleep(1)

        # The bframe may show an image grid challenge first.
        # Click #recaptcha-audio-button (the headphone icon) to switch to audio.
        # Retry up to 5 times — the button may not exist until the grid is shown.
        audio_switched = False
        for attempt in range(5):
            # Check if audio button exists
            has_audio_btn = driver.execute_script(
                "return !!document.getElementById('recaptcha-audio-button');"
            )
            if has_audio_btn:
                ok = _js_click(driver, "recaptcha-audio-button", by_id=True, log=log)
                if ok:
                    log(f"Clicked #recaptcha-audio-button (attempt {attempt+1}) — switching to audio challenge.")
                    audio_switched = True
                    break
            # Also try the CSS class selector
            ok = _js_click(driver, ".rc-button-audio", by_id=False, log=log)
            if ok:
                log(f"Clicked .rc-button-audio (attempt {attempt+1}) — switching to audio challenge.")
                audio_switched = True
                break
            log(f"Audio button not yet visible (attempt {attempt+1}/5), waiting 2 s...")
            time.sleep(2)

        if not audio_switched:
            log("Could not click audio challenge button — manual solving required.")
            driver.switch_to.default_content()
            return False

        # Wait for the audio challenge panel to render
        log("Waiting 4 s for audio challenge panel to load...")
        time.sleep(4)

        # ---- Step 5: read .rc-audiochallenge-tdownload-link href ----
        audio_url = None
        for _attempt in range(15):
            # Try JS first (more reliable inside cross-origin frames)
            url = driver.execute_script(
                """
                const a = document.querySelector(
                    '.rc-audiochallenge-tdownload-link, a[href*="audio.mp3"], a[download]'
                );
                return a ? (a.href || a.getAttribute('href')) : null;
                """
            )
            if url:
                audio_url = url
                break
            time.sleep(1)

        # Switch back to main doc before downloading
        driver.switch_to.default_content()

        if not audio_url:
            log("Could not find audio download link — manual solving required.")
            return False

        log(f"Audio URL: {audio_url[:100]}...")

        # ---- Step 6: download + transcribe ----
        tmp_dir = tempfile.mkdtemp(prefix="vp_rc_")
        mp3_path = _download_recaptcha_audio(driver, audio_url, tmp_dir, log=log)
        if not mp3_path:
            log("Audio download failed.")
            return False

        answer = _transcribe_audio_mp3(mp3_path, log=log)
        if not answer:
            log("Audio transcription failed.")
            return False

        log(f"Transcribed answer: '{answer}'")

        # ---- Step 7: switch back into bframe, fill answer, verify ----
        bframe = None
        for sel in _BFRAME_CSS:
            try:
                bframe = driver.find_element(By.CSS_SELECTOR, sel)
                break
            except Exception:
                pass

        if bframe is None:
            log("bframe disappeared after download — manual solving required.")
            return False

        driver.switch_to.frame(bframe)
        log("Switched back into bframe to submit answer.")

        # Wait for #audio-response to exist
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "audio-response"))
        )

        # Set the field value via JS (cross-origin + dual-monitor safe)
        set_ok = _js_set_value(driver, "audio-response", answer)
        if set_ok:
            log(f"Set #audio-response value: '{answer}'")
        else:
            log("Could not set #audio-response value — manual solving required.")
            driver.switch_to.default_content()
            return False

        time.sleep(0.5)

        # Click #recaptcha-verify-button via JS
        verify_ok = _js_click(driver, "recaptcha-verify-button", by_id=True, log=log)
        if verify_ok:
            log("Clicked #recaptcha-verify-button via JS.")
        else:
            log("Could not click #recaptcha-verify-button — manual solving required.")
            driver.switch_to.default_content()
            return False

        log("Waiting 4 s for reCAPTCHA to validate answer...")
        time.sleep(4)

        driver.switch_to.default_content()

        # ---- Step 8: click orange #btnClick login button ----
        _click_login_verify_button(driver, log=log)
        return True

    except Exception as exc:
        log(f"reCAPTCHA solver error: {exc}")
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return False

def _click_login_verify_button(driver, log=print):
    """
    Click the orange Verify / login submit button (#btnClick) on the VidaPay
    login page after reCAPTCHA is solved.

    Pure JS — no ActionChains.  Dual-monitor safe.
    Force-removes the disabled attribute first because VidaPay keeps it disabled
    until the reCAPTCHA token propagates (can take 1-3 s).
    """
    _SELECTORS = [
        "#btnClick",
        "button[data-test-id='verify']",
        "button[data-callback='formSubmit']",
        "button.btn-orange[value='login']",
    ]

    end_time = time.time() + 15
    while time.time() < end_time:
        for sel in _SELECTORS:
            try:
                result = driver.execute_script(
                    """
                    const el = document.querySelector(arguments[0]);
                    if (!el) return 'NOT_FOUND';
                    el.removeAttribute('disabled');
                    el.classList.remove('disabled');
                    el.scrollIntoView({block: 'center', inline: 'center'});
                    const rect = el.getBoundingClientRect();
                    const cx = rect.left + rect.width  / 2;
                    const cy = rect.top  + rect.height / 2;
                    const opts = {bubbles:true, cancelable:true, view:window, clientX:cx, clientY:cy};
                    ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(
                        t => el.dispatchEvent(new MouseEvent(t, opts))
                    );
                    return 'CLICKED';
                    """,
                    sel,
                )
                if result == "CLICKED":
                    log(f"Clicked login Verify button ({sel}) via JS.")
                    return True
            except Exception:
                continue
        time.sleep(0.5)

    log("Could not click login Verify button within 15 seconds.")
    return False


class VidapayTransferSystem:
    """Standalone CRM logic for Inventory Reassignment with proper login flow."""

    def __init__(self, account_id, username, password, log_callback,
                 stop_event, vpn_pause_cb=None):
        self.account_id = account_id
        self.username = username
        self.password = password
        self.log = log_callback
        self.stop_event = stop_event
        # Called after the browser opens, before navigating to the CRM
        # login, so the user can connect their VPN first (matches the
        # VidaPay Ordering / Incentive Extractors behavior).
        self.vpn_pause_cb = vpn_pause_cb
        self.driver = None
        self.wait = None

    def should_stop(self):
        return self.stop_event.is_set()

    def start_browser_and_login(self):
        self.log("Starting Edge Browser for CRM...")
        try:
            # Same browser mechanism as the VidaPay Ordering and Incentive
            # Extractor tools: open a REAL Edge window (subprocess + remote
            # debugging on port 9222, dedicated automation profile) so the
            # user's installed extensions - including the USA PLANET VPN
            # add-on - show in the toolbar. The user connects the VPN inside
            # that plain browser window, then Selenium attaches to it.
            if not open_vpn_setup_browser(log=self.log):
                self.log("Could not open the Edge automation browser.")
                return False

            # Pause here so the user can connect their VPN inside the open
            # Edge window before the bot navigates to the CRM site (same as
            # the VidaPay Ordering and Incentive Extractor tools). Skipping
            # this caused page-load failures like "CRM Login failed: Message:"
            # with no text.
            if self.vpn_pause_cb is not None:
                self.log(
                    "Browser opened - connect your VPN, then confirm to continue..."
                )
                if not self.vpn_pause_cb():
                    self.log("VPN connect cancelled. Aborting run.")
                    return False

            # Attach Selenium to the already-open Edge window through the
            # remote debugging port (extensions and the VPN stay visible).
            self.driver = create_edge_driver(log=self.log)
            self.wait = WebDriverWait(self.driver, 30)
            # Record the VidaPay tab; WhatsApp Web opens next to it in a
            # second tab of this same browser window.
            self.main_window = self.driver.current_window_handle

            self.log("Navigating to VidaPay Login...")
            # Retry the load: right after a VPN connects, the tunnel can
            # still be settling and the first navigation may fail.
            for attempt in range(1, 4):
                try:
                    self.driver.get(CRM_LOGIN_URL)
                    break
                except Exception as nav_err:
                    if attempt == 3:
                        raise
                    self.log(
                        f"Login page load failed (attempt {attempt}/3): "
                        f"{nav_err}"
                    )
                    time.sleep(5)
            time.sleep(3)

            if self.should_stop():
                return False

            # If already logged in, skip login form
            if self._is_main_panel_ready():
                self.log("Already logged in. Continuing.")
                return True

            # FIX #1: Correct element IDs matching the real VidaPay CRM
            account_field = self.wait.until(
                EC.presence_of_element_located((By.ID, "AccountId"))
            )
            username_field = self.driver.find_element(By.ID, "Username")
            password_field = self.driver.find_element(By.ID, "Password")

            account_field.clear()
            account_field.send_keys(self.account_id)
            self.log(f"Account ID entered: {self.account_id}")

            username_field.clear()
            username_field.send_keys(self.username)
            self.log(f"Username entered: {self.username}")

            password_field.clear()
            password_field.send_keys(self.password)
            self.log("Password entered.")

            time.sleep(1)

            # FIX #1: Correct login button selectors
            login_btn = None
            for selector in ["#LoginButton", "input[type='submit']", "button[type='submit']"]:
                try:
                    candidate = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if candidate.is_displayed() and candidate.is_enabled():
                        login_btn = candidate
                        break
                except Exception:
                    continue

            if login_btn:
                login_btn.click()
                self.log("Login button clicked.")
            else:
                password_field.send_keys(Keys.RETURN)
                self.log("Enter key submitted login form.")

            if self.should_stop():
                return False

            # FIX #2: Proper sign-in flow handling
            return self._complete_signin_flow()

        except Exception as e:
            self.log(f"CRM Login failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Sign-in flow (mirrors parent module's complete_vidapay_signin_flow)
    # ------------------------------------------------------------------

    def _is_main_panel_ready(self):
        try:
            url = (self.driver.current_url or "").lower()
            if "main%20panel" in url or "main panel" in url:
                return True
            body = (self.driver.find_element(By.TAG_NAME, "body").text or "")
            return "main panel" in body.lower()
        except Exception:
            return False

    def _get_visible_h3_texts(self):
        try:
            h3s = self.driver.find_elements(By.TAG_NAME, "h3")
            return [h3.text.strip() for h3 in h3s if h3.is_displayed() and h3.text.strip()]
        except Exception:
            return []

    def _page_has_h3(self, text):
        return any(text.lower() == t.lower() for t in self._get_visible_h3_texts())

    def _click_new_sign_in_next(self):
        """Click 'Next' on New Sign In page — same selectors as Device Ordering."""
        locators = [
            (By.XPATH, "//button[contains(@onclick, 'goToTwoFactorCheck') and normalize-space()='Next']"),
            (By.CSS_SELECTOR, "button[onclick*='goToTwoFactorCheck']"),
            (By.XPATH, "//button[contains(@class, 'btn') and normalize-space()='Next']"),
            (By.ID, "btnNext"),
        ]
        for by, value in locators:
            try:
                btns = self.driver.find_elements(by, value)
                for b in btns:
                    if b.is_displayed() and b.is_enabled():
                        self.driver.execute_script("arguments[0].click();", b)
                        return True
            except Exception:
                continue
        return False

    def _click_trust_radio(self):
        """Click 'Trust This Device' radio — same selectors as Device Ordering."""
        locators = [
            (By.ID, "trustRadio"),
            (By.CSS_SELECTOR, "input[name='TrustedDevice'][value='True']"),
        ]
        for by, value in locators:
            try:
                elements = self.driver.find_elements(by, value)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        if not element.is_selected():
                            self.driver.execute_script("arguments[0].click();", element)
                        return True
            except Exception:
                continue
        return False

    def _click_setup_next(self, label_hint=""):
        """Click 'Next' on setup pages — same selectors as Device Ordering."""
        locators = [
            (By.ID, "setupNextBtn"),
            (By.XPATH, "//button[@id='setupNextBtn' and normalize-space()='Next']"),
            (By.XPATH, "//button[contains(@onclick, 'submitThisForm') and normalize-space()='Next']"),
            (By.XPATH, "//button[contains(@class, 'btn') and normalize-space()='Next']"),
        ]
        for by, value in locators:
            try:
                btns = self.driver.find_elements(by, value)
                for b in btns:
                    if b.is_displayed() and b.is_enabled():
                        self.driver.execute_script("arguments[0].click();", b)
                        return True
            except Exception:
                continue
        return False

    def _click_ready_to_go_continue(self):
        """Click 'Continue' on Ready to Go page — same selectors as Device Ordering."""
        locators = [
            (By.XPATH, "//button[contains(@onclick, 'vidapayAutomaticSignIn') and normalize-space()='Continue']"),
            (By.CSS_SELECTOR, "button[onclick*='vidapayAutomaticSignIn']"),
            (By.XPATH, "//button[contains(@class, 'btn') and normalize-space()='Continue']"),
        ]
        for by, value in locators:
            try:
                btns = self.driver.find_elements(by, value)
                for b in btns:
                    if b.is_displayed() and b.is_enabled():
                        self.driver.execute_script("arguments[0].click();", b)
                        return True
            except Exception:
                continue
        return False

    def _complete_signin_flow(self, timeout_seconds=420):
        """Handle the full VidaPay post-login sign-in flow."""
        self.log("Checking VidaPay sign-in state...")

        started_at = time.time()
        last_state = None
        two_factor_logged = False
        trust_clicked = False
        setup_next_clicks = 0
        ready_clicked = False

        while time.time() - started_at < timeout_seconds:
            if self.should_stop():
                self.log("Sign-in flow stopped by user.")
                return False

            if self._is_main_panel_ready():
                self.log("Main Panel detected. Logged in successfully.")
                return True

            # Human verification (Cloudflare / Turnstile / reCAPTCHA)
            if _is_human_verification_page(self.driver):
                self.log("Human verification detected during sign-in. Auto-solving...")
                _wait_for_human_verification_clear(
                    self.driver,
                    stop_event=self.stop_event,
                    log=self.log,
                    context="sign-in flow",
                )
                continue

            h3_texts = self._get_visible_h3_texts()
            current_state = " | ".join(h3_texts) if h3_texts else "No known heading"
            if current_state != last_state:
                self.log(f"Sign-in state: {current_state}")
                last_state = current_state

            if self._page_has_h3("New Sign In"):
                self.log("New Sign In page detected.")
                if self._click_new_sign_in_next():
                    time.sleep(3)
                    continue

            # Trust This Device
            trust_radio_visible = False
            try:
                trust_radio_visible = any(
                    el.is_displayed()
                    for el in self.driver.find_elements(By.ID, "trustRadio")
                )
            except Exception:
                pass

            if trust_radio_visible and not trust_clicked:
                self.log("Trust This Device page detected.")
                if self._click_trust_radio():
                    trust_clicked = True
                    time.sleep(0.5)
                    if self._click_setup_next("Trust This Device Next"):
                        setup_next_clicks += 1
                        time.sleep(3)
                        continue

            # Additional setup pages after trust
            if trust_clicked and setup_next_clicks < 3:
                if self._click_setup_next("Additional setup Next"):
                    setup_next_clicks += 1
                    time.sleep(3)
                    continue

            # Ready to Go
            if self._page_has_h3("Ready to Go") or not ready_clicked:
                if self._click_ready_to_go_continue():
                    ready_clicked = True
                    time.sleep(5)
                    continue

            # 2FA
            if self._page_has_h3("2-Factor Authentication"):
                if not two_factor_logged:
                    self.log(
                        "2-Factor Authentication detected. "
                        "Please approve in IBM Verify / authenticator app."
                    )
                    two_factor_logged = True
                time.sleep(2)
                continue

            # If we've been looping with no state change, just wait
            time.sleep(2)

        self.log("Sign-in flow timed out.")
        return False

    # ------------------------------------------------------------------
    # Transfer logic
    # ------------------------------------------------------------------

    def _focus_main_window(self):
        """Bring the VidaPay tab back to the foreground."""
        try:
            if (
                getattr(self, "main_window", None)
                and self.main_window in self.driver.window_handles
            ):
                self.driver.switch_to.window(self.main_window)
        except Exception:
            pass

    def _dismiss_pending_alerts(self):
        """Dismiss any lingering JS alert()/confirm()/prompt() dialogs.

        VidaPay's InventoryReassignmentTool.aspx pops a ``confirm("Delete this
        SIM entry?")`` dialog whenever the user (or the bot) clicks the "X"
        button next to a SIM row.  If that dialog is left open — e.g. a
        previous run was killed mid-click, or the auto-dismiss capability
        did not catch it — every subsequent ``driver.get()`` will hang and
        msedgedriver on Windows will crash with a ``GetHandleVerifier``
        stacktrace.

        This helper runs before each navigation step so we never enter
        ``driver.get()`` with an alert on top of the page.
        """
        for _ in range(3):  # alerts can stack — dismiss up to 3 in a row
            try:
                WebDriverWait(self.driver, 0.5).until(EC.alert_is_present())
            except Exception:
                return  # no alert present — done
            try:
                alert = self.driver.switch_to.alert
                alert_text = ""
                try:
                    alert_text = alert.text
                except Exception:
                    pass
                alert.dismiss()
                self.log(
                    f"Dismissed lingering browser alert"
                    + (f": {alert_text[:80]}" if alert_text else "")
                )
                time.sleep(0.5)
            except Exception:
                return

    def navigate_to_transfer_tool(self):
        self.log("Navigating to Inventory Reassignment Tool...")

        # ── Why we use window.open() instead of driver.get() ────────────
        # msedgedriver on Windows crashes with a GetHandleVerifier stacktrace
        # when driver.get() is called on a tab that has WhatsApp Web's SPA
        # loaded.  The crash happens in msedgedriver's handle-verification
        # layer, NOT in our Python code — so retrying driver.get() just
        # crashes again.
        #
        # FIX: use JavaScript's window.open() to create a BRAND NEW tab
        # AND navigate it to the VidaPay URL in one atomic step.  This
        # bypasses both switch_to.new_window() (which can fail silently on
        # attached Edge) and driver.get() (which triggers the crash).
        #
        # The new tab starts with a clean browser context — no SPA state,
        # no onbeforeunload handlers, no stale JavaScript — so the VidaPay
        # page loads cleanly.
        # ─────────────────────────────────────────────────────────────────

        TARGET_URL = "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx"

        # First dismiss any lingering JS alert() / confirm() dialogs.
        try:
            self._dismiss_pending_alerts()
        except Exception:
            pass

        for attempt in range(3):
            try:
                # ── Step 1: Open a new tab with the target URL via JS ──
                # window.open() creates a new tab AND navigates it in one
                # step.  This avoids the GetHandleVerifier crash that
                # driver.get() triggers on tabs with SPA state.
                before_handles = set(self.driver.window_handles)
                new_tab_handle = None
                try:
                    self.driver.execute_script(
                        f"window.open('{TARGET_URL}', '_blank');"
                    )
                    self.log("Opened new tab via window.open() with VidaPay URL.")
                    # Poll for the new tab handle to appear — it can take
                    # 1-5 seconds to register in attached Edge mode.
                    for _ in range(10):
                        time.sleep(0.5)
                        after_handles = set(self.driver.window_handles)
                        new_handles = after_handles - before_handles
                        if new_handles:
                            new_tab_handle = new_handles.pop()
                            break
                    if new_tab_handle:
                        self.log(f"New tab handle detected: {new_tab_handle[:12]}...")
                    else:
                        self.log(
                            "window.open() did not create a detectable new tab "
                            "after 5s. Falling back to switch_to.new_window()."
                        )
                except Exception as open_err:
                    self.log(
                        f"window.open() failed ({open_err}); "
                        f"trying switch_to.new_window() fallback."
                    )

                # ── Step 2: Switch to the new tab ──
                if new_tab_handle:
                    try:
                        self.driver.switch_to.window(new_tab_handle)
                        self.log("Switched to new CRM tab.")
                    except Exception as switch_err:
                        self.log(
                            f"Could not switch to new tab ({switch_err}); "
                            f"trying new_window fallback."
                        )
                        new_tab_handle = None  # force fallback below

                if not new_tab_handle:
                    # Fallback: use switch_to.new_window() + driver.get()
                    # This MAY trigger the GetHandleVerifier crash on some
                    # Edge builds, but it's better than switching to the
                    # wrong tab (the login page) which definitely crashes.
                    try:
                        before_handles = set(self.driver.window_handles)
                        self.driver.switch_to.new_window("tab")
                        time.sleep(1)
                        # Find the new tab
                        after_handles = set(self.driver.window_handles)
                        new_handles = after_handles - before_handles
                        if new_handles:
                            self.driver.switch_to.window(new_handles.pop())
                            self.log("Switched to new tab via new_window().")
                        # Navigate via JS (avoids driver.get() crash)
                        self.driver.execute_script(
                            f"window.location.href = '{TARGET_URL}';"
                        )
                        time.sleep(3)
                    except Exception as nw_err:
                        self.log(f"new_window fallback failed: {nw_err}")
                        raise

                # Dismiss any alert that may have popped up during navigation.
                self._dismiss_pending_alerts()

                # ── Step 3: Verify we're on the right page ──
                # Wait for the VidaPay page to fully load and verify the
                # Account input is present.  If not, we're on the wrong tab
                # (e.g. the login page) — bail out and retry.
                try:
                    current_url = self.driver.current_url or ""
                except Exception:
                    current_url = ""

                if "vidapaycrm.com" not in current_url.lower():
                    self.log(
                        f"⚠️ Wrong page after navigation: '{current_url[:80]}'. "
                        f"Expected VidaPay CRM. Retrying."
                    )
                    raise Exception(
                        f"Navigation landed on wrong page: {current_url[:100]}"
                    )

                self.log(f"Current URL: {current_url[:80]}")

                # ── Step 3: Wait for the VidaPay page to fully load ──
                # Use WebDriverWait instead of a fixed sleep so we don't
                # wait longer than necessary.
                try:
                    self.wait.until(
                        EC.presence_of_element_located(
                            (By.ID, "rcbAccount_Input")
                        )
                    )
                except Exception:
                    # If the Account input isn't found, we might be on an
                    # Application Error page.  Check for that and recover.
                    try:
                        error_el = self.driver.find_element(
                            By.CSS_SELECTOR, ".error-container, .error-title"
                        )
                        if error_el and error_el.is_displayed():
                            self.log(
                                "⚠️ VidaPay Application Error detected. "
                                "Navigating to Main Panel via JS..."
                            )
                            self.driver.execute_script(
                                "window.location.href = "
                                "'https://www.vidapaycrm.com/Main%20Panel.aspx';"
                            )
                            time.sleep(3)
                            self._dismiss_pending_alerts()
                            # Now navigate to the Transfer Tool via JS
                            self.driver.execute_script(
                                f"window.location.href = '{TARGET_URL}';"
                            )
                            time.sleep(3)
                            self._dismiss_pending_alerts()
                            # Wait again for the Account input
                            self.wait.until(
                                EC.presence_of_element_located(
                                    (By.ID, "rcbAccount_Input")
                                )
                            )
                    except Exception:
                        pass  # No error page — re-raise the original timeout

                time.sleep(1)
                # Remember this tab so _navigate_back_to_main_panel can
                # close it via JS when the transfer is done.
                self._crm_tab_handle = self.driver.current_window_handle
                self.log("VidaPay Reassignment Tool ready.")
                return True

            except Exception as e:
                err_str = str(e)[:200]
                self.log(f"Navigation attempt {attempt+1}/3 failed: {err_str}")
                # If the failure was caused by an alert, dismiss it before
                # retrying — otherwise the next attempt will crash the same way.
                try:
                    self._dismiss_pending_alerts()
                except Exception:
                    pass
                if attempt < 2:
                    self.log("Retrying in 10 seconds...")
                    time.sleep(10)
                    # Re-attach to Edge with a fresh driver
                    try:
                        self.driver = create_edge_driver(log=self.log)
                        self.wait = WebDriverWait(self.driver, 30)
                        self.main_window = self.driver.current_window_handle
                        self.log("Re-attached to Edge browser.")
                        time.sleep(2)
                        self._dismiss_pending_alerts()
                    except Exception as re_err:
                        self.log(f"Re-attach failed: {re_err}")
        self.log("All navigation attempts failed.")
        return False

    def execute_transfer(self, target_account_id, imeis):
        """Transfer IMEIs to the given VidaPay account.

        Flow:
            1. Fill the target Account ID.
            2. If more than 2 IMEIs are supplied, attempt a CSV bulk upload.
               If it succeeds, skip the one-by-one entry loop.
            3. Otherwise (or if CSV upload failed), enter each IMEI one-by-one
               with per-IMEI error checking (Invalid Sim dialog → screenshot,
               log, remove errored row, continue).
            4. Click Next (force-removing the disabled attribute first).
            5. Click Submit.
            6. Navigate back to the Main Panel.

        Returns:
            True if the transfer was submitted (including partial success
            when some IMEIs failed but at least one succeeded), False if the
            whole transfer failed (no IMEIs added, navigation error, etc.).

        Per-IMEI error screenshots / messages are collected on
        ``self._error_screenshots`` for later WhatsApp reporting.
        """
        if not imeis:
            self.log("No IMEIs to transfer. Skipping.")
            return False

        self.log(
            f"Initiating transfer to Account ID: {target_account_id} "
            f"for {len(imeis)} devices."
        )

        # Make sure the error-screenshot buffer exists (even if this run has
        # no errors) so callers can safely inspect ``self._error_screenshots``.
        if not hasattr(self, "_error_screenshots"):
            self._error_screenshots = []

        try:
            # 0. Pre-flight check: verify we're on the Reassignment Tool page
            # BEFORE waiting for the account input.  If we're on the wrong
            # page (e.g. login page), the 30s WebDriverWait would time out
            # and msedgedriver crashes with GetHandleVerifier when responding
            # to the timeout query.
            try:
                current_url = self.driver.current_url or ""
            except Exception:
                current_url = ""
            if "InventoryReassignmentTool" not in current_url:
                self.log(
                    f"⚠️ Wrong page for transfer: '{current_url[:80]}'. "
                    f"Expected Inventory Reassignment Tool. Aborting."
                )
                if not hasattr(self, "_error_screenshots"):
                    self._error_screenshots = []
                self._error_screenshots.append({
                    "imei": "N/A",
                    "error": f"Wrong page: {current_url[:100]}",
                    "screenshot": None,
                })
                return False

            # Also verify the account input exists.  VidaPay's ASP.NET page
            # can take 25-30s to fully render (RadComboBox controls load
            # asynchronously), so we use a 45s timeout.
            #
            # We do NOT use the old 30s self.wait.until() because that
            # triggered the GetHandleVerifier crash on timeout.  Instead
            # we poll document.readyState first (fast, no crash risk),
            # then look for the element with explicit error handling.
            try:
                # Phase 1: wait for page to finish loading (readyState=complete)
                for _ in range(60):  # up to 30s (60 x 0.5s)
                    try:
                        ready = self.driver.execute_script(
                            "return document.readyState"
                        )
                        if ready == "complete":
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)

                # Phase 2: wait for the account input to be clickable.
                # Use 20s — the page is loaded, the RadComboBox just
                # needs to initialize.
                account_input = WebDriverWait(self.driver, 20).until(
                    EC.element_to_be_clickable(
                        (By.ID, "rcbAccount_Input")
                    )
                )
            except Exception:
                self.log(
                    "⚠️ Account input not found — page may not have loaded "
                    "properly or VidaPay is slow. Aborting transfer."
                )
                if not hasattr(self, "_error_screenshots"):
                    self._error_screenshots = []
                self._error_screenshots.append({
                    "imei": "N/A",
                    "error": "Account input not found (page load timeout)",
                    "screenshot": None,
                })
                return False

            # 1. Enter Target Account ID
            account_input.clear()
            account_input.send_keys(target_account_id)
            time.sleep(1)
            account_input.send_keys(Keys.ENTER)
            time.sleep(3)

            # 1b. Check if the account was actually selected.
            # If the dropdown still shows "Select Account..." or the
            # account ID we typed is still in the input but not selected,
            # the store is likely locked or temporarily suspended.
            try:
                current_val = account_input.get_attribute("value") or ""
                if ("select account" in current_val.lower()
                    or current_val.strip() == ""
                    or (target_account_id not in current_val
                        and "select account" not in current_val.lower()
                        and not current_val.strip())):
                    # Check if the RadComboBox dropdown has any items
                    items = self.driver.find_elements(
                        By.CSS_SELECTOR,
                        ".rcbListItem, .rcbList > li"
                    )
                    if not items:
                        self.log(
                            f"❌ Account ID '{target_account_id}' not found — "
                            f"store is LOCKED or temporarily suspended. Skipping."
                        )
                        return False
            except Exception:
                pass

            # 2. Bulk IMEIs (>2) → CSV upload path.  Falls back silently to
            #    one-by-one entry below if the upload element is missing or
            #    the upload button click fails.
            csv_uploaded = False
            if len(imeis) > 2:
                csv_uploaded = self._upload_imeis_csv(imeis)

            # 3. One-by-one entry with per-IMEI error checking.
            successful_imeis = []
            failed_imeis = []

            if not csv_uploaded:
                sim_input = self.driver.find_element(
                    By.ID, "txtSimEntry"
                )
                add_btn = self.driver.find_element(
                    By.ID, "btnAddSimEntry"
                )

                for imei in imeis:
                    if self.should_stop():
                        self.log("Process stopped by user.")
                        return False

                    # Retry loop for StaleElementReferenceException on the
                    # sim_input / add_btn handles (page re-renders the
                    # RadComboBox pane between clicks).
                    added = False
                    for _attempt in range(3):
                        try:
                            sim_input.clear()
                            sim_input.send_keys(imei)
                            time.sleep(0.5)
                            self.driver.execute_script(
                                "arguments[0].click();", add_btn
                            )
                            time.sleep(1)
                            self.log(f"Added IMEI to batch: {imei}")
                            added = True
                            break
                        except StaleElementReferenceException:
                            self.log(
                                f"Stale element for IMEI {imei}, "
                                f"re-fetching..."
                            )
                            sim_input = self.driver.find_element(
                                By.ID, "txtSimEntry"
                            )
                            add_btn = self.driver.find_element(
                                By.ID, "btnAddSimEntry"
                            )
                            time.sleep(1)
                    if not added:
                        self.log(
                            f"Failed to add IMEI {imei} after retries."
                        )
                        failed_imeis.append(imei)
                        continue

                    # Check for Invalid Sim error image / dialog.  If found,
                    # the helper screenshots it, dismisses the dialog, removes
                    # the errored row from the batch, and records the error.
                    if self._check_and_handle_imei_error(imei):
                        failed_imeis.append(imei)
                    else:
                        successful_imeis.append(imei)

                # If every single IMEI errored, the batch is empty → there
                # is nothing to submit.
                if not successful_imeis:
                    self.log(
                        f"All {len(imeis)} IMEIs failed to add — aborting "
                        f"transfer."
                    )
                    return False

            # 4. Proceed to Next (force-enable first — VidaPay leaves the
            #    button disabled until the page's own JS flips it, but we've
            #    already populated everything we need).
            next_btn = self.driver.find_element(By.ID, "btnNext")
            try:
                self.driver.execute_script(
                    "arguments[0].removeAttribute('disabled');", next_btn
                )
            except Exception:
                pass
            self.driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(2)

            # 5. Submit Transfer
            submit_btn = self.wait.until(
                EC.element_to_be_clickable(
                    (By.ID, "MainContent_submitButton")
                )
            )
            self.driver.execute_script(
                "arguments[0].click();", submit_btn
            )
            self.log(
                f"Transfer submitted successfully to {target_account_id}."
            )

            # 6. Navigate back to the Main Panel so the next transfer (if
            #    any) starts from a clean slate.
            time.sleep(3)
            self._navigate_back_to_main_panel()

            # 7. Report partial-success status if some IMEIs failed.
            if not csv_uploaded and failed_imeis:
                self.log(
                    f"Transfer completed with {len(failed_imeis)} failed "
                    f"IMEI(s): {failed_imeis}"
                )
            return True

        except Exception as e:
            err_str = str(e)[:300]
            self.log(f"Error during CRM transfer: {err_str}")
            # Store the actual error so _process_one_task can report it
            # accurately instead of the misleading "store may be LOCKED"
            # message (which is only true when the account ID is not found
            # in the dropdown — not when the page failed to load or the
            # input element couldn't be located).
            if not hasattr(self, "_error_screenshots"):
                self._error_screenshots = []
            self._error_screenshots.append({
                "imei": "N/A",
                "error": f"CRM error: {err_str}",
                "screenshot": None,
            })
            return False

    # ------------------------------------------------------------------
    # execute_transfer helpers
    # ------------------------------------------------------------------

    def _upload_imeis_csv(self, imeis):
        """Bulk-upload IMEIs via the CSV template.

        Returns True if the upload succeeded and we should skip the
        one-by-one entry loop; returns False on any failure so the caller
        can fall back to per-IMEI entry.
        """
        self.log(
            f"Bulk transfer: {len(imeis)} IMEIs — using CSV upload."
        )

        try:
            csv_path = os.path.join(
                tempfile.gettempdir(),
                f"transfer_imeis_{int(time.time())}.csv",
            )
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["SIM_ID"])
                for imei in imeis:
                    writer.writerow([imei])
            self.log(f"Created CSV: {csv_path}")
        except Exception as csv_err:
            self.log(
                f"CSV file creation failed: {csv_err} — falling back to "
                f"one-by-one."
            )
            return False

        try:
            file_inputs = self.driver.find_elements(
                By.CSS_SELECTOR, "input[type='file']"
            )
            if not file_inputs:
                self.log(
                    "No file input found — falling back to one-by-one "
                    "entry."
                )
                return False

            file_inputs[0].send_keys(csv_path)
            time.sleep(2)
            self.log("CSV file uploaded.")
        except Exception as up_err:
            self.log(
                f"CSV upload failed: {up_err} — falling back to "
                f"one-by-one."
            )
            return False

        # Click Upload button (force-enable first, same pattern as Next).
        try:
            upload_btn = self.driver.find_element(
                By.ID, "btnUploadFile"
            )
            try:
                self.driver.execute_script(
                    "arguments[0].removeAttribute('disabled');", upload_btn
                )
            except Exception:
                pass
            self.driver.execute_script(
                "arguments[0].click();", upload_btn
            )
            time.sleep(3)
            self.log("Upload button clicked.")
            return True
        except Exception as upload_err:
            self.log(f"Upload button error: {upload_err}")
            return False

    def _check_and_handle_imei_error(self, imei):
        """Detect & handle the Invalid Sim error that may appear after Add.

        Returns True if an error was detected and handled (the caller should
        treat the IMEI as failed); returns False if no error was present
        (the IMEI was added cleanly).
        """
        # Look for the Invalid Sim icon that VidaPay renders next to a bad
        # row.  Try multiple selectors — the ID pattern includes the row
        # index (ctl00, ctl01, ctl02, ...) so we can't use a fixed ID.
        error_img = None
        try:
            # Strategy 1: by src attribute (most reliable)
            error_img = self.driver.find_element(
                By.CSS_SELECTOR,
                "img[src*='Invalid%20Sim.png'], img[src*='Invalid']",
            )
        except Exception:
            try:
                # Strategy 2: by ID pattern (ctl00_MainContent_rgSims_ctl00_ctlNN_imgErrorMessageImageUrl)
                error_img = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "img[id*='imgErrorMessageImageUrl']",
                )
            except Exception:
                pass

        if not error_img:
            return False

        # Click the error image to open the error dialog.
        try:
            self.driver.execute_script(
                "arguments[0].click();", error_img
            )
        except Exception:
            pass
        time.sleep(1)

        # Read the error text from the jQuery UI dialog.
        error_text = "Unknown error"
        try:
            dialog = self.driver.find_element(By.ID, "planNameModal")
            if dialog:
                txt = dialog.text.strip()
                if txt:
                    error_text = txt
        except Exception:
            pass

        self.log(f"❌ IMEI {imei} error: {error_text}")

        # Take a screenshot of the error dialog for later reporting.
        screenshot_path = None
        try:
            screenshot_path = os.path.join(
                os.environ.get("TEMP", tempfile.gettempdir()),
                f"transfer_error_{imei}_{int(time.time())}.png",
            )
            self.driver.save_screenshot(screenshot_path)
            self.log(f"Screenshot saved: {screenshot_path}")
        except Exception as ss_err:
            self.log(f"Could not save screenshot: {ss_err}")

        # Close the dialog.  Try the labelled Close button first, then fall
        # back to the jQuery UI titlebar close icon.  (We use JS because the
        # :contains() pseudo-selector in the original spec is jQuery-only
        # and not valid CSS, so Selenium would throw on it.)
        try:
            self.driver.execute_script(
                """
                var btns = document.querySelectorAll(
                    '.ui-dialog .ui-button, .ui-dialog button'
                );
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].textContent || '').trim().toLowerCase();
                    if (t.indexOf('close') >= 0) {
                        btns[i].click();
                        return;
                    }
                }
                var tbc = document.querySelector('.ui-dialog-titlebar-close');
                if (tbc) tbc.click();
                """
            )
        except Exception:
            # Final fallback: call jQuery directly (loaded by jQuery UI).
            try:
                self.driver.execute_script(
                    "if (window.jQuery) {"
                    " jQuery('.ui-dialog-titlebar-close').click();"
                    "}"
                )
            except Exception:
                pass
        time.sleep(0.5)

        # Remove the errored IMEI from the batch (click its row's X button).
        try:
            x_buttons = self.driver.find_elements(
                By.CSS_SELECTOR,
                "input[value='X'][onclick*='Delete'], "
                "input[value='x'][onclick*='Delete'], "
                "input[onclick*='Delete']",
            )
            if x_buttons:
                # Click the last X button — that's the most recently added
                # row, which is the one that just errored.
                self.driver.execute_script(
                    "arguments[0].click();", x_buttons[-1]
                )
                time.sleep(1)
                # Handle the JavaScript confirm() dialog ("Delete this SIM
                # entry?") that the page pops up after clicking X.  Use the
                # robust helper — a bare switch_to.alert.accept() will silently
                # no-op if the alert takes >0 ms to appear, leaving the dialog
                # open and crashing the next driver.get() with a
                # GetHandleVerifier stacktrace.
                try:
                    self._dismiss_pending_alerts()
                except Exception:
                    pass
                self.log(
                    f"Removed errored IMEI {imei} from the list."
                )
        except Exception as del_err:
            self.log(f"Could not remove errored IMEI: {del_err}")

        # Store for later sending (e.g. WhatsApp notification).
        self._error_screenshots.append(
            {
                "imei": imei,
                "error": error_text,
                "screenshot": screenshot_path,
            }
        )
        return True

    def _navigate_back_to_main_panel(self):
        """After Submit, close the temp CRM tab (opened in
        navigate_to_transfer_tool) and switch back to the main CRM tab.

        We close the temp tab via window.close() in JavaScript — NOT via
        driver.close(), which would kill the msedgedriver session.
        """
        self.log("Transfer submitted. Closing temp CRM tab...")
        crm_tab = getattr(self, "_crm_tab_handle", None)

        # Try to close the temp tab via JS (safe — only works on windows
        # opened by script, which is the case for switch_to.new_window("tab")).
        try:
            if crm_tab and crm_tab in self.driver.window_handles:
                # Make sure we're on the temp tab before calling window.close().
                self.driver.switch_to.window(crm_tab)
                self.driver.execute_script("window.close();")
                time.sleep(1)
                self.log("Closed temp CRM tab via JS.")
        except Exception as close_err:
            self.log(f"Could not close temp tab via JS: {close_err}")

        # Switch back to the main CRM window.
        try:
            if (
                getattr(self, "main_window", None)
                and self.main_window in self.driver.window_handles
            ):
                self.driver.switch_to.window(self.main_window)
                self.log("Switched back to main CRM tab.")
                return
        except Exception:
            pass

        # Fallback: navigate directly to the Main Panel URL on whatever
        # tab is currently active.
        try:
            self.driver.get(
                "https://www.vidapaycrm.com/Main%20Panel.aspx"
            )
            time.sleep(2)
            self.log("Navigated to Main Panel via URL.")
        except Exception as nav_err:
            self.log(f"Could not navigate back to Main Panel: {nav_err}")


class WhatsAppScraper:
    def __init__(self, raw_groups, log_callback, stop_event):
        if isinstance(raw_groups, str):
            self.groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
        else:
            self.groups = list(raw_groups)
        self.log = log_callback
        self.stop_event = stop_event
        self.driver = None
        # Whether this scraper launched its own browser (True) or is reusing
        # the browser session already opened for VidaPay (False).
        self.owns_driver = False

    def _wa_page_state(self):
        """Return a string describing what WhatsApp Web is showing right now.

        Possible return values:
          'qr'          - QR code visible, needs scan
          'loading'     - page still loading / spinner
          'logged_in'   - chat list visible, ready to go
          'unknown'     - could not determine state
        """
        try:
            # Quick check: if the page title contains a number in parentheses
            # like "(18) WhatsApp" or just "WhatsApp" (not "WhatsApp Web"),
            # the user is logged in. This is the most reliable check because
            # WhatsApp changes its DOM structure frequently.
            try:
                title = self.driver.title or ""
                # "WhatsApp Web" = pre-login page title
                # "WhatsApp" or "(N) WhatsApp" = logged in
                if "whatsapp web" not in title.lower() and "whatsapp" in title.lower():
                    return "logged_in"
            except Exception:
                pass

            return self.driver.execute_script("""
                // 1. Check for QR code (drawn on <canvas> in the login pane)
                const canvases = document.querySelectorAll('canvas');
                for (const c of canvases) {
                    if (c.offsetWidth > 80 && c.offsetHeight > 80) {
                        const parentVisible = c.closest('[class]') &&
                            getComputedStyle(c.closest('[class]')).display !== 'none';
                        if (parentVisible) return 'qr';
                    }
                }

                // 2. Check for loading spinner
                const spinners = document.querySelectorAll(
                    'svg[role="img"], div[role="progressbar"], '
                  + 'div[class*="spinner"], div[class*="loading"]'
                );
                for (const s of spinners) {
                    if (s.offsetWidth > 0 && s.offsetHeight > 0) return 'loading';
                }

                // 3. Check for the chat list / sidebar
                const chatItems = document.querySelectorAll(
                    '[data-id], [role="listitem"]'
                );
                const header = document.querySelector(
                    'header, [class*="header"][class*="app"]'
                );
                if (chatItems.length > 0 && header
                    && header.offsetWidth > 0) {
                    return 'logged_in';
                }

                // 4. Fallback: any contenteditable div means the UI rendered
                const editables = document.querySelectorAll('[contenteditable="true"]');
                if (editables.length > 0) {
                    return 'logged_in';
                }

                return 'unknown';
            """)
        except Exception:
            return 'unknown'

    def _wa_find_search_box_js(self):
        """Use JavaScript to locate the search input."""
        return self.driver.execute_script("""
            // Strategy 1: Standard input elements (WhatsApp's newer UI)
            const inputs = document.querySelectorAll(
                'input[type="text"], input:not([type]), '
              + 'input[placeholder*="search" i], input[placeholder*="chat" i], '
              + 'input[aria-label*="search" i], input[aria-label*="chat" i]'
            );
            for (const el of inputs) {
                if (el.offsetWidth > 0 && el.offsetHeight > 0
                    && getComputedStyle(el).display !== 'none'
                    && getComputedStyle(el).visibility !== 'hidden') {
                    // Prefer search inputs in the header area (top of page)
                    const rect = el.getBoundingClientRect();
                    if (rect.top < 300) return el;
                }
            }

            // Strategy 2: contenteditable divs (WhatsApp's older UI)
            const candidates = document.querySelectorAll(
                'header [contenteditable="true"], '
              + '[class*="search"] [contenteditable="true"], '
              + 'div[contenteditable="true"][data-tab], '
              + 'div[contenteditable="true"][title], '
              + 'div[contenteditable="true"][aria-label]'
            );
            for (const el of candidates) {
                if (el.offsetWidth > 0 && el.offsetHeight > 0
                    && getComputedStyle(el).display !== 'none'
                    && getComputedStyle(el).visibility !== 'hidden') {
                    return el;
                }
            }

            // Strategy 3: p element with role="textbox" (newer WhatsApp)
            const textboxes = document.querySelectorAll(
                'p[role="textbox"], div[role="textbox"], '
              + '[data-testid="search-input"], '
              + 'span[role="textbox"]'
            );
            for (const el of textboxes) {
                if (el.offsetWidth > 0 && el.offsetHeight > 0) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < 300) return el;
                }
            }

            // Strategy 4: ANY visible contenteditable div near top of page
            const all = document.querySelectorAll('[contenteditable="true"]');
            for (const el of all) {
                if (el.offsetWidth > 0 && el.offsetHeight > 0
                    && getComputedStyle(el).display !== 'none'
                    && getComputedStyle(el).visibility !== 'hidden'
                    && el.tagName === 'DIV') {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < 200) return el;
                }
            }

            return null;
        """)

    def start_whatsapp(self, shared_driver=None):
        self.log("Starting WhatsApp Web...")

        # Same-browser mode: reuse the browser session already opened for
        # VidaPay instead of launching a second, separate browser. WhatsApp
        # opens in its OWN real tab (created via the robust Selenium-native
        # new_window helper with CDP fallback) so it never loads in the same
        # tab as VidaPay; the VidaPay tab stays open behind it.
        if shared_driver is not None:
            self.driver = shared_driver
            self.owns_driver = False
            self.wait = WebDriverWait(self.driver, 30)
            self.log(
                "Opening WhatsApp Web in a separate tab "
                "(the VidaPay tab stays open)..."
            )
            if not open_blank_normal_tab(self.driver, log=self.log):
                self.log("Could not create a separate WhatsApp tab.")
                return False
            self.wa_window = self.driver.current_window_handle
            open_url_in_edge_tab(
                self.driver, "https://web.whatsapp.com", log=self.log
            )
            return self._wait_for_whatsapp_session()

        # Standalone mode: open (or attach to) the same dedicated Edge
        # automation browser used for VidaPay, so the WhatsApp Web login
        # state lives in the same profile and the VPN/extensions stay
        # visible in the toolbar.
        self.owns_driver = True
        try:
            # If the automation Edge isn't open yet, this opens a real Edge
            # window (profile with the user's extensions) and the attach step
            # below connects Selenium right through the debugging port.
            open_vpn_setup_browser(url="about:blank", log=self.log)
            self.driver = create_edge_driver(log=self.log)
            self.wait = WebDriverWait(self.driver, 30)
            open_url_in_edge_tab(
                self.driver, "https://web.whatsapp.com", log=self.log
            )
            self.wa_window = self.driver.current_window_handle
            return self._wait_for_whatsapp_session()
        except Exception as e:
            self.log(f"WhatsApp initialization failed: {e}")
            return False

    def _wait_for_whatsapp_session(self):
        self.log(
            "Waiting for WhatsApp Web session/login (up to 120s)..."
        )
        start_time = time.time()
        last_logged_state = ""
        qr_seen = False

        while time.time() - start_time < 120:
            if self.stop_event.is_set():
                return False

            state = self._wa_page_state()

            # Log state changes for diagnostics
            if state != last_logged_state:
                self.log(f"  WA page state: {state}")
                last_logged_state = state

            if state == "qr":
                if not qr_seen:
                    self.log(
                        "QR code detected. Please scan it in the "
                        "browser window that just opened."
                    )
                    qr_seen = True
                time.sleep(3)
                continue

            if state == "loading":
                time.sleep(2)
                continue

            if state == "logged_in":
                # Found the page, now locate the search box
                search_box = self._wa_find_search_box_js()
                if search_box:
                    self.log(
                        "WhatsApp Web authenticated and search box found."
                    )
                    return True
                # Logged in but search box not found yet - rare, retry
                self.log(
                    "Logged in but search box not located yet, retrying..."
                )
                time.sleep(2)
                continue

            # state == 'unknown' - page still loading
            time.sleep(2)

        self.log("WhatsApp Web login timeout reached.")
        # Diagnostic: dump page title and URL
        try:
            self.log(
                f"  Page title: {self.driver.title}"
            )
            self.log(
                f"  Page URL: {self.driver.current_url}"
            )
        except Exception:
            pass
        return False

    def _find_search_box(self):
        """Locate the search box. Tries JS first, then keyboard shortcut."""
        # Try JS-based search first
        box = self._wa_find_search_box_js()
        if box:
            return box

        # Fallback: use Ctrl+/ keyboard shortcut to focus the search bar
        # (WhatsApp Web's built-in shortcut)
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            self.driver.switch_to.active_element
            ActionChains(self.driver).key_down(Keys.CONTROL).send_keys('/').key_up(Keys.CONTROL).perform()
            time.sleep(1)
            # Now try to find the focused element
            box = self._wa_find_search_box_js()
            if box:
                return box
            # Last resort: return the active element
            active = self.driver.switch_to.active_element
            if active and active.tag_name in ('input', 'div', 'p'):
                return active
        except Exception:
            pass

        return None

    def _focus_wa_window(self):
        """Bring the WhatsApp tab back to the foreground."""
        try:
            if (
                getattr(self, "wa_window", None)
                and self.wa_window in self.driver.window_handles
            ):
                self.driver.switch_to.window(self.wa_window)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # BeautifulSoup-based message extraction (much faster than Selenium
    # find_elements — one page_source fetch vs N WebDriver round-trips).
    # ------------------------------------------------------------------
    def _extract_messages_bs4(self):
        """Parse the currently-visible WhatsApp chat with BeautifulSoup.

        Returns a list of dicts: [{"text": str, "is_incoming": bool, "sender": str}, ...]
        ordered top-to-bottom as they appear in the chat.

        Falls back to [] if BeautifulSoup isn't available or parsing fails.
        """
        if not BS4_AVAILABLE:
            return []
        try:
            html = self.driver.page_source
        except Exception:
            return []
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        messages = []

        # WhatsApp Web renders each message as a <div data-id="..."> row.
        # The text lives inside nested <span class="selectable-text"> elements.
        # We pick the most specific selector that works on the current WA build.
        #
        # Strategy A: message rows identified by data-id (most reliable).
        msg_rows = soup.select("div[data-id]")
        if not msg_rows:
            # Strategy B: rows identified by role=row.
            msg_rows = soup.select("div[role='row']")
        if not msg_rows:
            # Strategy C: any element that looks like a message bubble.
            msg_rows = soup.select(
                "div.message-in, div[class*='message-in'], "
                "div[class*='msg'], div.copyable-text"
            )

        for row in msg_rows:
            try:
                # Concatenate all visible text in the row (sender + body + time).
                text = row.get_text(separator="\n", strip=True)
                if not text or len(text) < 3:
                    continue

                # Heuristic: WhatsApp marks incoming messages with class
                # "message-in" and outgoing with "message-out". If neither
                # matches, fall back to checking for a "You" sender prefix.
                row_class = " ".join(row.get("class", []))
                is_incoming = (
                    "message-in" in row_class
                    or "incoming" in row_class.lower()
                )
                if not is_incoming:
                    # Outgoing messages typically start with "You:" or have
                    # a "message-out" class. Treat everything else as incoming.
                    is_incoming = not (
                        text.lower().startswith("you:")
                        or "message-out" in row_class
                    )

                # Try to extract the sender name (first non-empty text line
                # that is NOT the message body).
                sender = ""
                first_span = row.select_one(
                    "span[role='aria'], span[dir='auto'], "
                    "span.selectable-text"
                )
                if first_span:
                    sender = first_span.get_text(strip=True)

                messages.append({
                    "text": text,
                    "is_incoming": is_incoming,
                    "sender": sender,
                    # Keep a reference to the row for screenshot/OCR fallback.
                    "_row_idx": len(messages),
                })
            except Exception:
                continue

        return messages

    def check_group_notifications(self):
        """Check WhatsApp Web for groups with unread message badges.
        Also ensures notification settings are enabled.
        Returns a list of group names that have new (unread) messages."""
        try:
            self._focus_wa_window()
            time.sleep(0.5)

            # First, ensure WhatsApp notification settings are ON
            self._ensure_notification_settings_on()

            # JavaScript to find all chat list items with unread badges.
            # WhatsApp's badge classes are obfuscated (x1xxx...) so we can't
            # match by class name. Instead, we find span elements whose text
            # content is a small number (1-99) and whose parent has a green
            # background (the unread badge circle).
            unread_groups = self.driver.execute_script("""
                const groupsWithUnread = [];

                // Strategy 1: Find spans containing small numbers inside
                // elements with green background (the unread badge circle)
                const allSpans = document.querySelectorAll('span');
                for (const span of allSpans) {
                    // Must be visible
                    if (span.offsetWidth === 0 || span.offsetHeight === 0) continue;
                    
                    // Text must be a small number (1-99 = unread count)
                    const text = span.textContent.trim();
                    if (!text || !/^\\d{1,2}$/.test(text)) continue;
                    
                    // Check if this span or its parent has a green-ish background
                    // (WhatsApp unread badges are green: #25D366 or similar)
                    let el = span;
                    for (let i = 0; i < 3 && el; i++) {
                        const bg = getComputedStyle(el).backgroundColor;
                        // Green colors: rgb(37, 211, 102) = #25D366
                        // Also check for any non-transparent, non-white, non-dark bg
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(0, 0, 0)'
                            && bg !== 'rgb(255, 255, 255)' && bg !== 'transparent') {
                            // Check if it looks green (G > R and G > B)
                            const match = bg.match(/\\d+/g);
                            if (match && match.length >= 3) {
                                const r = parseInt(match[0]);
                                const g = parseInt(match[1]);
                                const b = parseInt(match[2]);
                                if (g > r && g > b) {
                                    // Found a green badge! Walk up to find group name.
                                    let parent = el.parentElement;
                                    let group_name = '';
                                    for (let j = 0; j < 15 && parent; j++) {
                                        const nameEl = parent.querySelector(
                                            'span[title], span[aria-label], '
                                          + 'div[title], div[aria-label]'
                                        );
                                        if (nameEl) {
                                            group_name = nameEl.getAttribute('title')
                                                || nameEl.getAttribute('aria-label')
                                                || nameEl.textContent || '';
                                            if (group_name.trim() && group_name.length > 1) break;
                                        }
                                        parent = parent.parentElement;
                                    }
                                    if (group_name.trim()) {
                                        groupsWithUnread.push(group_name.trim());
                                    }
                                    break; // Don't check more parents for this span
                                }
                            }
                        }
                        el = el.parentElement;
                    }
                }

                // Strategy 2: Fallback — look for aria-label containing "unread"
                const ariaUnread = document.querySelectorAll(
                    '[aria-label*="unread" i], [aria-label*="message" i]'
                );
                for (const el of ariaUnread) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    const label = el.getAttribute('aria-label') || '';
                    if (label.toLowerCase().includes('unread')) {
                        // Walk up to find group name
                        let parent = el.parentElement;
                        for (let j = 0; j < 10 && parent; j++) {
                            const nameEl = parent.querySelector('span[title], div[title]');
                            if (nameEl && nameEl.getAttribute('title')) {
                                groupsWithUnread.push(nameEl.getAttribute('title').trim());
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }
                }

                // Deduplicate
                return [...new Set(groupsWithUnread)];
            """)

            return unread_groups or []
        except Exception as e:
            self.log(f"Notification check error: {e}")
            return []

    def _ensure_notification_settings_on(self):
        """Ensure WhatsApp Web notification settings are enabled.
        Opens Settings → Notifications and turns ON:
        - Message notifications
        - Show previews
        - Show reaction notifications
        - Background sync
        Only runs once per session (tracked via self._notif_checked)."""
        # Only check once per session
        if getattr(self, '_notif_checked', False):
            return
        self._notif_checked = True

        try:
            # Click the Settings (menu) button — the three dots or gear icon
            # Try multiple selectors for the settings button
            settings_clicked = False
            for selector in [
                'span[data-testid="menu"]',
                'div[aria-label="Menu"]',
                'button[aria-label="Menu"]',
                'span[aria-label="Settings"]',
                'div[role="button"][aria-label*="Menu"]',
                '#side > header div[role="button"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        settings_clicked = True
                        break
                except Exception:
                    continue

            if not settings_clicked:
                return  # Can't open settings — skip

            # Click "Settings" in the dropdown menu
            for selector in [
                'li[role="menuitem"] div[title="Settings"]',
                'div[role="menuitem"][title="Settings"]',
                'li span[title="Settings"]',
                'div[aria-label="Settings"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        break
                except Exception:
                    continue

            # Click "Notifications" in settings
            for selector in [
                'div[role="listitem"] span[title="Notifications"]',
                'div[role="listitem"] div[title="Notifications"]',
                'span[title="Notifications"]',
                'div[aria-label="Notifications"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        break
                except Exception:
                    continue

            # Now we should be on the Notifications settings page.
            # Find all toggle switches and ensure they're ON.
            # WhatsApp Web toggles are typically div[role="checkbox"] or
            # span[role="button"] with aria-checked attribute.
            toggles = self.driver.find_elements(
                By.CSS_SELECTOR,
                'div[role="checkbox"], span[role="button"][aria-checked], '
                'input[type="checkbox"], div[role="switch"]'
            )

            turned_on = 0
            for toggle in toggles:
                try:
                    if not toggle.is_displayed():
                        continue
                    # Check if it's OFF (aria-checked="false" or not checked)
                    checked = toggle.get_attribute("aria-checked")
                    is_selected = toggle.is_selected()
                    if checked == "false" or (checked is None and not is_selected):
                        self.driver.execute_script("arguments[0].click();", toggle)
                        time.sleep(0.5)
                        turned_on += 1
                except Exception:
                    continue

            if turned_on > 0:
                self.log(f"✅ Turned ON {turned_on} notification setting(s).")
            else:
                self.log("✅ All notification settings already ON.")

            # Go back to the chat list (press Escape or click back)
            self.driver.execute_script("window.history.back();")
            time.sleep(1)
            # Press Escape to close any open menus
            from selenium.webdriver.common.keys import Keys
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)

        except Exception as e:
            self.log(f"Notification settings check failed (non-critical): {e}")
            # Try to go back to chat list
            try:
                self.driver.execute_script("window.history.back();")
                time.sleep(1)
            except Exception:
                pass

    def find_and_read_groups(self, mappings, trigger_words_str="", only_unread=False):
        transfer_tasks = []

        # Parse trigger words/sentences (comma-separated, case-insensitive)
        if trigger_words_str.strip():
            trigger_words = [w.strip().lower() for w in trigger_words_str.split(",") if w.strip()]
        else:
            trigger_words = ["transfer"]  # default fallback
        self.log(f"Trigger words: {trigger_words}")

        # Make sure the WhatsApp tab is the active one before scraping
        self._focus_wa_window()

        # If only_unread mode, check which groups have new messages
        unread_groups = []
        if only_unread:
            raw_unread = self.check_group_notifications()
            # Filter to ONLY return actual configured group names
            # (the notification detection can return garbage like
            # "3 unread messages" or "WhatsApp" — ignore those)
            configured_lower = [g.lower() for g in self.groups]
            for ug in raw_unread:
                ug_lower = ug.lower().strip()
                # Skip obvious garbage
                if "unread" in ug_lower or "message" in ug_lower:
                    continue
                if ug_lower == "whatsapp" or len(ug_lower) < 3:
                    continue
                # Only keep if it matches a configured group
                for cfg in configured_lower:
                    if cfg in ug_lower or ug_lower in cfg:
                        unread_groups.append(ug)
                        break
            if unread_groups:
                self.log(f"Groups with unread: {unread_groups}")

        for group_name in self.groups:
            if self.stop_event.is_set():
                break

            # Skip groups that don't have unread messages (only_unread mode)
            if only_unread:
                has_unread = any(
                    group_name.lower() in ug.lower() or ug.lower() in group_name.lower()
                    for ug in unread_groups
                )
                if not has_unread:
                    continue

            self.log(f"Searching for group: '{group_name}'")
            try:
                search_box = self._find_search_box()

                if not search_box:
                    self.log(
                        f"Could not find search box for group '{group_name}'"
                    )
                    continue

                # Clear existing search text and type group name
                self.driver.execute_script(
                    "arguments[0].focus();", search_box
                )
                time.sleep(0.3)
                search_box.send_keys(Keys.CONTROL + "a")
                search_box.send_keys(Keys.BACKSPACE)
                time.sleep(0.5)
                search_box.send_keys(group_name)
                time.sleep(2.5)
                search_box.send_keys(Keys.ENTER)
                time.sleep(3)

                # Find all message elements in the chat.
                # WhatsApp Web changes their DOM frequently, so try multiple selectors.
                #
                # PRIMARY PATH (BeautifulSoup): parse driver.page_source once
                # and extract all message texts in pure Python.  This is 10-50x
                # faster than calling find_elements 9 times per group.
                messages = []
                bs4_messages = []
                try:
                    bs4_messages = self._extract_messages_bs4()
                except Exception as bs4_err:
                    self.log(f"  BS4 extraction failed: {bs4_err}")

                if bs4_messages:
                    self.log(
                        f"  BS4 extracted {len(bs4_messages)} messages "
                        f"(skipped Selenium selector fallback)."
                    )

                    # Wrap each bs4 dict in a tiny shim that mimics the
                    # Selenium WebElement API the rest of find_and_read_groups
                    # depends on (.text, .screenshot(), .find_elements()).
                    class _BS4Msg:
                        def __init__(self, data, driver):
                            self.text = data["text"]
                            self._driver = driver
                            self._is_incoming = data["is_incoming"]
                            self._sender = data.get("sender", "")

                        def screenshot(self, path):
                            # BS4 messages have no live element to screenshot.
                            # The OCR fallback path will just no-op.
                            pass

                        def find_elements(self, by, selector):
                            # BS4 messages can't run CSS queries on a stale
                            # soup tree — return [] so the caller falls through
                            # to the JS-walker / OCR path.
                            return []

                    messages = [_BS4Msg(m, self.driver) for m in bs4_messages]
                else:
                    # FALLBACK PATH (Selenium): use the original multi-selector
                    # approach when BS4 returns nothing (e.g. WA changed its
                    # DOM, or BS4 isn't installed).
                    for selector in [
                        "div.message-in",                                          # older WhatsApp
                        "div[data-id] > div > div",                                # message rows
                        "div[role='row']",                                         # newer WhatsApp
                        "div[class*='message-in']",                                # class contains
                        "div[class*='message'] div[class*='bubble']",              # bubble containers
                        "span.selectable-text",                                    # text spans
                        "div.copyable-text",                                       # copyable text
                        "[data-testid='conversation-panel-messages'] div",         # test-id based
                        "div[class*='msg']",                                       # any msg class
                    ]:
                        try:
                            found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                            if found:
                                messages = found
                                self.log(f"  Using selector: {selector} ({len(messages)} elements)")
                                break
                        except Exception:
                            continue

                    # If still no messages, try a broader approach: get all text
                    # from the chat panel via JavaScript
                    if not messages:
                        try:
                            # Get the chat panel and extract all text blocks
                            chat_texts = self.driver.execute_script("""
                                // Find the chat messages panel
                                const panels = document.querySelectorAll(
                                    '[data-testid="conversation-panel-messages"], '
                                  + 'div[class*="message-list"], '
                                  + 'div[class*="chat-body"], '
                                  + 'div[role="application"] div[role="list"]'
                                );
                                if (panels.length === 0) return [];

                                const panel = panels[panels.length - 1];
                                // Get all text nodes that look like messages
                                const textNodes = [];
                                const walker = document.createTreeWalker(
                                    panel,
                                    NodeFilter.SHOW_TEXT,
                                    null,
                                    false
                                );
                                while (walker.nextNode()) {
                                    const text = walker.currentNode.textContent.trim();
                                    if (text.length > 3) {
                                        textNodes.push(text);
                                    }
                                }
                                return textNodes;
                            """)
                            if chat_texts:
                                self.log(f"  Found {len(chat_texts)} text blocks via JS walker.")
                                # Create a simple wrapper for each text block
                                class _TextMsg:
                                    def __init__(self, text):
                                        self.text = text
                                    def screenshot(self, path):
                                        pass  # no screenshot for text-only
                                messages = [_TextMsg(t) for t in chat_texts]
                        except Exception as js_err:
                            self.log(f"  JS text walker failed: {js_err}")
                self.log(
                    f"[{group_name}] Found {len(messages)} recent incoming messages."
                )

                for msg_idx, msg in enumerate(messages):
                    if self.stop_event.is_set():
                        break

                    text_content = msg.text.lower()

                    # Check if ANY trigger word/sentence is in the message.
                    # Use word-boundary matching for short trigger words like
                    # 't to' to avoid false matches inside other words.
                    # 'transfer to' and 'trf to' are long enough to be safe
                    # as substrings, but 't to' can match inside phrases
                    # like '...what to do' which is NOT a transfer request.
                    import re as _re_trigger
                    triggered = False
                    for tw in trigger_words:
                        if len(tw) <= 5:
                            # Short trigger: require word boundaries on both sides
                            # to avoid matching inside other words.
                            if _re_trigger.search(
                                r'\b' + _re_trigger.escape(tw) + r'\b',
                                text_content
                            ):
                                triggered = True
                                break
                        else:
                            # Long trigger: substring match is fine
                            if tw in text_content:
                                triggered = True
                                break
                    if not triggered:
                        continue

                    self.log(f"Trigger word matched in message: {msg.text[:80]}...")

                    # Look for a store name or alias in the message using
                    # smart matching with aliases.
                    import re as _re
                    target_account = None
                    target_store = None
                    best_match_len = 0

                    for store_name, store_data in mappings.items():
                        sn = store_name.lower()
                        if isinstance(store_data, str):
                            acc_id = store_data
                            aliases = []
                        else:
                            acc_id = store_data.get("account_id", "")
                            aliases = store_data.get("aliases", [])
                        
                        all_names = [sn] + [a.lower() for a in aliases]
                        
                        for name in all_names:
                            # Skip very short aliases (1-2 chars) to avoid
                            # matching numbers inside phone numbers like
                            # "+1 (786) 585-3556" matching "35"
                            if len(name) < 3:
                                continue
                                
                            is_match = False
                            # Strategy A: Full name in message (but NOT inside
                            # a phone number — check that the match isn't
                            # preceded by digits/parens/plus)
                            if name in text_content:
                                # Use regex to ensure it's not part of a
                                # phone number (preceded by digit/paren/+)
                                phone_check = _re.search(
                                    r'[\d\+\(\-]\s*' + _re.escape(name) + r'\b',
                                    text_content
                                )
                                if not phone_check:
                                    is_match = True
                            
                            # Strategy B: Word-boundary match (rejects "new" prefix)
                            if not is_match:
                                pattern = r'(?<!\bnew\s)\b' + _re.escape(name) + r'\b'
                                m = _re.search(pattern, text_content)
                                if m:
                                    # Check it's not inside a phone number
                                    start = m.start()
                                    before = text_content[max(0,start-3):start]
                                    if not _re.search(r'[\d\+\(\-]\s*$', before):
                                        is_match = True
                                if not is_match and ("old " + name) in text_content:
                                    is_match = True
                            
                            if is_match and len(name) > best_match_len:
                                best_match_len = len(name)
                                target_account = acc_id
                                target_store = store_name

                    if target_account:
                        self.log(
                            f"Transfer request detected in '{group_name}' "
                            f"for '{target_store}' -> Account: {target_account}"
                        )
                        
                        # Step 1: Try extracting IMEIs from THIS message's text
                        imeis = self._extract_imeis_from_text(text_content)
                        
                        if imeis:
                            self.log(f"Extracted {len(imeis)} IMEIs from trigger message text.")
                        else:
                            # Step 2: Try OCR on THIS message (might have an image)
                            img_path = os.path.join(
                                os.environ.get("TEMP", ""), f"wa_msg_{msg_idx}.png"
                            )
                            msg.screenshot(img_path)
                            imeis = self._extract_imeis_from_image(img_path)
                            if imeis:
                                self.log(f"Extracted {len(imeis)} IMEIs via OCR from trigger message image.")
                        
                        # Also check for <img> tags inside the trigger message
                        # (WhatsApp images are loaded as blob: URLs)
                        if not imeis:
                            try:
                                imeis = self._extract_imeis_from_msg_images(msg)
                                if imeis:
                                    self.log(f"Extracted {len(imeis)} IMEIs from trigger message images.")
                            except Exception:
                                pass
                        
                        if not imeis:
                            # Step 3: Check messages ABOVE (previous messages)
                            self.log("No IMEIs in trigger message — checking adjacent messages...")
                            for check_idx in range(msg_idx - 1, max(msg_idx - 4, -1), -1):
                                if check_idx < 0 or check_idx >= len(messages):
                                    continue
                                adj_msg = messages[check_idx]
                                adj_text = adj_msg.text.lower()
                                
                                # Try text first
                                imeis = self._extract_imeis_from_text(adj_text)
                                if imeis:
                                    self.log(f"Extracted {len(imeis)} IMEIs from message above (index {check_idx}).")
                                    break
                                
                                # Try extracting from <img> tags in the message
                                try:
                                    imeis = self._extract_imeis_from_msg_images(adj_msg)
                                    if imeis:
                                        self.log(f"Extracted {len(imeis)} IMEIs from images in message above (index {check_idx}).")
                                        break
                                except Exception:
                                    pass
                                
                                # Try OCR on screenshot
                                adj_img = os.path.join(
                                    os.environ.get("TEMP", ""), f"wa_adj_{check_idx}.png"
                                )
                                adj_msg.screenshot(adj_img)
                                imeis = self._extract_imeis_from_image(adj_img)
                                if imeis:
                                    self.log(f"Extracted {len(imeis)} IMEIs via OCR from message above (index {check_idx}).")
                                    break
                        
                        if not imeis:
                            # Step 4: Check messages BELOW (next messages)
                            for check_idx in range(msg_idx + 1, min(msg_idx + 4, len(messages))):
                                adj_msg = messages[check_idx]
                                adj_text = adj_msg.text.lower()
                                
                                # Try text first
                                imeis = self._extract_imeis_from_text(adj_text)
                                if imeis:
                                    self.log(f"Extracted {len(imeis)} IMEIs from message below (index {check_idx}).")
                                    break
                                
                                # Try extracting from <img> tags in the message
                                try:
                                    imeis = self._extract_imeis_from_msg_images(adj_msg)
                                    if imeis:
                                        self.log(f"Extracted {len(imeis)} IMEIs from images in message below (index {check_idx}).")
                                        break
                                except Exception:
                                    pass
                                
                                # Try OCR on screenshot
                                adj_img = os.path.join(
                                    os.environ.get("TEMP", ""), f"wa_adj_{check_idx}.png"
                                )
                                adj_msg.screenshot(adj_img)
                                imeis = self._extract_imeis_from_image(adj_img)
                                if imeis:
                                    self.log(f"Extracted {len(imeis)} IMEIs via OCR from message below (index {check_idx}).")
                                    break
                                    break
                        
                        if imeis:
                            transfer_tasks.append({
                                "group": group_name,
                                "store": target_store,
                                "account_id": target_account,
                                "imeis": imeis,
                                # Save a short snippet of the trigger message
                                # so the "wait for reply" feature can find
                                # it again when re-scanning the group.
                                "trigger_text": msg.text[:60],
                                "detected_at": time.time(),
                            })
                        else:
                            self.log(
                                "No valid IMEIs found in trigger message or adjacent messages."
                            )

                search_box.send_keys(Keys.ESCAPE)
                time.sleep(1)

            except Exception as e:
                self.log(f"Error scanning group '{group_name}': {e}")

        return transfer_tasks

    def _extract_imeis_from_text(self, text):
        """Extract IMEI numbers from message text.
        IMEIs are strictly 15-digit numbers starting with 35 or 01.
        Also handles space/dash separated formats like '357 612 117 960 162'."""
        import re as _re
        imeis = set()
        
        # Pattern 1: Standard 15-digit IMEI (strictly starts with 35 or 01)
        for match in _re.finditer(r'\b((?:35|01)\d{13})\b', text):
            imeis.add(match.group(1))
        
        # Pattern 2: Space/dash separated IMEIs like "357 612 117 960 162"
        # or "357-612-117-960-162" — strip separators and validate
        for match in _re.finditer(r'\b((?:35|01)[\d\s\-]{14,30})', text):
            cleaned = _re.sub(r'[\s\-]', '', match.group(1))
            if len(cleaned) == 15 and cleaned.isdigit() and cleaned[:2] in ('35', '01'):
                imeis.add(cleaned)
        
        return list(imeis)

    def _extract_imeis_from_image(self, img_path):
        try:
            # Preprocess image for better OCR accuracy
            img = Image.open(img_path)
            img = img.convert("L")  # grayscale
            img = img.resize(
                (img.width * 2, img.height * 2), Image.Resampling.LANCZOS
            )

            custom_config = r"--oem 3 --psm 6"
            text = pytesseract.image_to_string(img, config=custom_config)

            # Search both stripped and raw text
            cleaned = re.sub(r"[\s\-\.]+", "", text)
            imeis_from_cleaned = re.findall(r"(?:35|01)\d{13}", cleaned)
            imeis_from_raw = re.findall(r"\b(?:35|01)\d{13}\b", text)
            all_imeis = list(set(imeis_from_cleaned + imeis_from_raw))
            return all_imeis
        except Exception as e:
            self.log(f"OCR Error: {e}")
            return []

    def _extract_imeis_from_msg_images(self, msg_element):
        """Find all <img> tags inside a WhatsApp message element,
        download each image, and OCR it for IMEIs."""
        all_imeis = set()
        try:
            # Find all image elements inside the message
            imgs = msg_element.find_elements(By.CSS_SELECTOR, "img[src*='blob:'], img[src*='http'], img[data-src]")
            if not imgs:
                # Also try background images (WhatsApp sometimes uses CSS background)
                imgs = msg_element.find_elements(By.CSS_SELECTOR, "img")
            
            for img_el in imgs:
                try:
                    if not img_el.is_displayed():
                        continue
                    # Get the image source
                    src = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                    if not src or len(src) < 50:
                        continue
                    
                    # Download the image
                    img_path = os.path.join(
                        os.environ.get("TEMP", tempfile.gettempdir()),
                        f"wa_img_{int(time.time()*1000)}.png"
                    )
                    
                    if src.startswith("blob:"):
                        # Blob URLs need to be fetched via JS
                        img_data = self.driver.execute_script("""
                            return new Promise((resolve, reject) => {
                                const xhr = new XMLHttpRequest();
                                xhr.open('GET', arguments[0], true);
                                xhr.responseType = 'blob';
                                xhr.onload = function() {
                                    const reader = new FileReader();
                                    reader.onloadend = function() {
                                        resolve(reader.result);
                                    };
                                    reader.readAsDataURL(xhr.response);
                                };
                                xhr.onerror = reject;
                                xhr.send();
                            });
                        """, src)
                        if img_data and img_data.startswith("data:"):
                            # Strip the data: prefix
                            header, b64data = img_data.split(",", 1)
                            with open(img_path, "wb") as f:
                                f.write(base64.b64decode(b64data))
                        else:
                            continue
                    elif src.startswith("http"):
                        # Regular URL — download with requests
                        import requests as _req
                        resp = _req.get(src, timeout=15)
                        with open(img_path, "wb") as f:
                            f.write(resp.content)
                    else:
                        continue
                    
                    # OCR the downloaded image
                    if os.path.exists(img_path) and os.path.getsize(img_path) > 100:
                        imeis = self._extract_imeis_from_image(img_path)
                        if imeis:
                            self.log(f"  Found {len(imeis)} IMEIs in image: {imeis}")
                            all_imeis.update(imeis)
                        
                        # Clean up
                        try:
                            os.unlink(img_path)
                        except:
                            pass
                except Exception as img_err:
                    self.log(f"  Image extraction error: {img_err}")
                    continue
        except Exception as e:
            self.log(f"Image scan error: {e}")
        
        return list(all_imeis)

    # ------------------------------------------------------------------
    # "Wait for reply" feature
    # ------------------------------------------------------------------
    # When a transfer request is detected, the bot does NOT process it
    # immediately.  Instead it waits WAIT_FOR_REPLY_SECONDS (default 30s)
    # and re-scans the group for replies that look like someone is
    # already handling the transfer ("on it", "doing", "will do", etc.).
    # If such a reply is found, the transfer is SKIPPED.
    # If no reply is found within the timeout, the bot posts "on it" in
    # the group to claim the transfer and then processes it.

    # Phrases that indicate someone is handling the transfer manually.
    # Matched as substrings (case-insensitive) against message text.
    REPLY_HANDLING_PHRASES = [
        "on it",
        "doing",
        "on the way",
        "will do",
        "handling",
        "i got it",
        "i got this",
        "got it",
        "got this",
        "taking care",
        "let me do",
        "i'll do",
        "ill do",
        "i am doing",
        "im doing",
        "i'm doing",
        "ok doing",
        "sure doing",
        "yes doing",
        "doing it",
        "already done",
        "already transferred",
        "already sent",
        "done already",
        # Additional common reply variants
        "working on it",
        "working on",
        "on it now",
        "doing now",
        "doing it now",
        "will do it",
        "i'll do it",
        "ill do it",
        "let me handle",
        "i'll handle",
        "ill handle",
        "i'll take care",
        "ill take care",
        "i'll check",
        "ill check",
        "checking now",
        "check now",
        "received",
        "noted",
        "copy that",
        "roger",
        "ack",
        "acknowledged",
    ]

    def check_for_reply_in_group(self, group_name, trigger_text_snippet):
        """Re-scan a group for messages AFTER a trigger message and check
        whether anyone has replied with a handling phrase like "on it" or
        "doing".

        Uses JavaScript innerText extraction instead of BS4 — simpler and
        more reliable.  BS4 was failing because:
          1. div[data-id] selector picks up sidebar chat list items too
          2. Quoted replies confuse trigger-text position detection (the
             trigger snippet appears in BOTH the original message AND
             inside the quoted reply)
          3. WhatsApp's obfuscated DOM breaks BS4 parsing frequently

        The JS approach gets the exact text the user sees, in order.

        Args:
            group_name: WhatsApp group to scan.
            trigger_text_snippet: A short substring of the original trigger
                message.  Used to find where the trigger ends so we only
                look at text AFTER it.

        Returns:
            (True, reply_text) if a handling reply was found,
            (False, "") if no reply was found.
        """
        if not self.driver:
            return False, ""

        try:
            self._focus_wa_window()
            time.sleep(0.5)

            search_box = self._find_search_box()
            if not search_box:
                self.log("  [reply-check] No search box found.")
                return False, ""

            # Search for the group
            self.driver.execute_script("arguments[0].focus();", search_box)
            time.sleep(0.3)
            search_box.send_keys(Keys.CONTROL + "a")
            search_box.send_keys(Keys.BACKSPACE)
            time.sleep(0.5)
            search_box.send_keys(group_name)
            time.sleep(2.5)
            search_box.send_keys(Keys.ENTER)
            time.sleep(3)

            # ── Extract ALL visible chat text via JavaScript innerText ──
            # This is much more reliable than BS4 parsing.  We get the
            # exact text the user sees, in order, including quoted replies.
            chat_text = ""
            try:
                chat_text = self.driver.execute_script("""
                    // Try multiple selectors for the chat message panel.
                    // WhatsApp Web changes these frequently.
                    var selectors = [
                        '[data-testid="conversation-panel-messages"]',
                        'div[class*="message-list"]',
                        'div[class*="chat-body"]',
                        'div[role="application"] div[role="list"]',
                        'main div[role="application"] div:last-of-type'
                    ];
                    for (var i = 0; i < selectors.length; i++) {
                        var el = document.querySelector(selectors[i]);
                        if (el && el.innerText && el.innerText.length > 10) {
                            return el.innerText;
                        }
                    }
                    // Fallback: get the main panel's text
                    var main = document.querySelector('main');
                    if (main && main.innerText) {
                        return main.innerText;
                    }
                    return "";
                """)
            except Exception as js_err:
                self.log(f"  [reply-check] JS text extraction failed: {js_err}")
                chat_text = ""

            if not chat_text or len(chat_text) < 10:
                self.log(
                    f"  [reply-check] No chat text found in '{group_name}'."
                )
                try:
                    search_box.send_keys(Keys.ESCAPE)
                except Exception:
                    pass
                return False, ""

            self.log(
                f"  [reply-check] Got {len(chat_text)} chars of chat text "
                f"from '{group_name}'."
            )

            # ── Find the trigger message in the chat text ──
            # The trigger_text_snippet includes the full message text (sender
            # name + phone + body + timestamp).  We only need the KEY part —
            # the transfer keyword + store name.  Extract it by looking for
            # common trigger phrases in the snippet.
            chat_lower = chat_text.lower()
            snippet_lower = trigger_text_snippet.lower()

            # Try to extract just the "transfer to <store>" or "t to <store>"
            # part from the snippet — this is more reliable than using the
            # full snippet (which includes sender name + phone number that
            # may not match exactly in the re-scanned text).
            #
            # Capture up to 4 words after "transfer to" / "t to" so multi-
            # word store names like "New Colfax" or "N 35th Store" are
            # matched correctly (old regex only caught the first word).
            import re as _re
            trigger_key = ""
            for pattern in [
                r'(transfer to \S+(?:\s+\S+){0,3})',
                r'(t to \S+(?:\s+\S+){0,3})',
                r'(trf to \S+(?:\s+\S+){0,3})',
                r'(tt\.?\s*t\s*t \S+(?:\s+\S+){0,3})',
            ]:
                m = _re.search(pattern, snippet_lower)
                if m:
                    trigger_key = m.group(1).strip()
                    break

            if trigger_key:
                self.log(
                    f"  [reply-check] Looking for trigger key: "
                    f"'{trigger_key}' in chat text."
                )
                # Use the LAST occurrence in case the trigger text also
                # appears in a quoted reply above the actual trigger.
                trigger_pos = chat_lower.rfind(trigger_key)
            else:
                # Fallback: use the first 30 chars of the snippet
                trigger_key = snippet_lower.strip()[:30]
                trigger_pos = chat_lower.rfind(trigger_key)

            if trigger_pos < 0:
                # Trigger not found — it may have scrolled off.
                # Scan ALL text for handling phrases (safe choice).
                self.log(
                    f"  [reply-check] Trigger not found in chat text. "
                    f"Scanning ALL text for handling phrases..."
                )
                text_to_check = chat_lower
            else:
                # Only check text AFTER the trigger message.
                text_to_check = chat_lower[trigger_pos + len(trigger_key):]
                self.log(
                    f"  [reply-check] Trigger found at char {trigger_pos}. "
                    f"Checking {len(text_to_check)} chars after trigger."
                )

            # ── Check for handling phrases in the relevant text ──
            for phrase in self.REPLY_HANDLING_PHRASES:
                if phrase in text_to_check:
                    # Found a handling reply — skip this transfer.
                    # Try to extract the reply text for logging.
                    phrase_pos = text_to_check.find(phrase)
                    # Get a window of text around the match for context
                    start = max(0, phrase_pos - 20)
                    end = min(len(text_to_check), phrase_pos + len(phrase) + 40)
                    if trigger_pos >= 0:
                        offset = trigger_pos + len(trigger_key)
                        reply_excerpt = chat_text[offset + start : offset + end].strip()
                    else:
                        reply_excerpt = chat_text[start:end].strip()
                    self.log(
                        f"  [reply-check] ✓ Handling reply detected in "
                        f"'{group_name}' (matched: '{phrase}'): "
                        f"\"{reply_excerpt[:100]}\""
                    )
                    try:
                        search_box.send_keys(Keys.ESCAPE)
                    except Exception:
                        pass
                    return True, reply_excerpt

            self.log(
                f"  [reply-check] ✗ No handling reply found in "
                f"'{group_name}'."
            )
            try:
                search_box.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            return False, ""

        except Exception as e:
            self.log(f"  [reply-check] Error scanning '{group_name}': {e}")
            return False, ""

    def close(self):
        # Only quit the browser if this scraper launched it. When reusing the
        # VidaPay browser session, quitting here would kill the shared window.
        # In ATTACH_TO_OPEN_EDGE mode the dedicated Edge window (profile,
        # extensions, VPN) stays open between runs, so we just detach and
        # never force-quit it.
        if self.driver:
            if self.owns_driver and not ATTACH_TO_OPEN_EDGE:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            self.driver = None

    def send_reply(self, group_name, message_text):
        """DISABLED — bot no longer sends ANY text to WhatsApp.

        The user explicitly requested: 'stop bot to send text to whatsapp.
        no text never.'

        This method is kept as a no-op so all existing call sites continue
        to work without errors, but NO message is ever typed or sent.

        The bot STILL checks for replies (check_for_reply_in_group is
        unaffected) so it will still skip transfers when someone replies
        'on it' / 'doing' / etc.  It just won't send any text itself.

        To re-enable WhatsApp sending in the future, restore the original
        implementation from git history.
        """
        self.log(
            f"WhatsApp text sending is DISABLED — would have sent to "
            f"'{group_name}': {message_text[:80]}"
        )
        return True  # return True so callers don't treat it as an error


# ============================================================================
# GUI APPLICATION
# ============================================================================


class VidaPayTransferApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VidaPay Inventory Transfer Bot (Standalone)")
        self.geometry("1020x850")
        self.configure(bg=BRAND_SURFACE)

        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.scheduler_running = False
        self.scheduler_thread = None
        self.retained_driver = None

        self.config_data = load_config()
        self.mappings = self.config_data.get("transfer_mappings", {})
        self.wa_group = self.config_data.get(
            "transfer_wa_group", DEFAULT_GROUPS
        )
        self.schedule_times = self.config_data.get(
            "transfer_schedule", "09:00, 14:00, 17:00"
        )
        self.history = self.config_data.get("transfer_history", [])

        # VidaPay theme: 'system' follows Windows, otherwise explicit light/dark
        self.theme_setting = str(
            self.config_data.get("theme", "system")
        ).lower()
        if self.theme_setting not in ("system", "light", "dark"):
            self.theme_setting = "system"
        self.colors = THEMES[self._resolve_theme()]

        self.images = {}
        self._load_brand_assets()

        # Create ThemeManager BEFORE _build_ui (add_theme_toggle needs it)
        from theme_manager import ThemeManager
        self.theme_manager = ThemeManager(app_name="vidapay-transfer-bot")
        # Sync it to the app's real saved theme setting so the header's
        # sun/moon icon starts in the correct state (ThemeManager keeps
        # its own separate theme.json otherwise, which can disagree with
        # self.theme_setting loaded from this app's own config above).
        self.theme_manager.current_theme = self._resolve_theme()

        self._build_ui()
        self._apply_theme()
        self.after(100, self._process_log_queue)
        # Check-and-install OCR dependencies in the background
        threading.Thread(target=self._auto_setup_deps, daemon=True).start()

    def _build_ui(self):
        # ---- VidaPay header (FixedHeaderManager) ----
        self.header_mgr = FixedHeaderManager(self, title="VidaPay Transfer Bot")
        # Tag header as protected (immune to theme toggle)
        if hasattr(self.header_mgr, 'header_frame'):
            self.header_mgr.header_frame._tag = "header"
            for child in self.header_mgr.header_frame.winfo_children():
                child._tag = "header"
                for grandchild in child.winfo_children():
                    grandchild._tag = "header_label"
        # Load logo
        try:
            _lp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VidaPay_Logo.png")
            if not os.path.exists(_lp) and hasattr(sys, '_MEIPASS'):
                _lp = os.path.join(sys._MEIPASS, "VidaPay_Logo.png")
            if os.path.exists(_lp):
                self.header_mgr.set_logo(logo_path=_lp, text="VidaPay")
        except Exception:
            pass
        # Add theme toggle
        self.header_mgr.add_theme_toggle(self.theme_manager, callback=self._on_header_theme_toggle)

        # Theme toggle — attach to the header frame created by FixedHeaderManager
        # Theme toggle is handled by header_mgr.add_theme_toggle() above.
        # The old manual theme_box/theme_btn block has been removed to
        # eliminate the duplicate "Theme / Switch to Dark" button that was
        # appearing alongside the header's sun/moon toggle.

        # ---- Body: LEFT (config + controls) | RIGHT (log panel) ----
        # Two-column layout so the log panel is always visible on the right
        # side, wide enough to read full log lines while the config tabs and
        # buttons are on the left.
        body_frame = tk.Frame(self, bg=self.colors["bg"])
        body_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        # ── LEFT side: notebook (tabs) + buttons + progress ──
        left_frame = tk.Frame(body_frame, bg=self.colors["bg"])
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        self.notebook = ttk.Notebook(left_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_config = ttk.Frame(self.notebook)
        self.tab_history = ttk.Frame(self.notebook)

        self.notebook.add(
            self.tab_config, text="  Transfer Configuration "
        )
        self.notebook.add(
            self.tab_history, text="  Transfer Logs "
        )

        self._build_config_tab()
        self._build_history_tab()

        # Controls (Run / Schedule / Stop) below the notebook on the left
        ctrl_frame = tk.Frame(left_frame, bg=self.colors["bg"])
        ctrl_frame.pack(fill=tk.X, pady=(8, 5))

        self.btn_run = self._make_btn(
            ctrl_frame,
            text="Run Now",
            icon_key="play",
            bg=self.colors["red"],
            fg="#ffffff",
            command=self.run_manual,
            tag="run",
        )
        self.btn_run.pack(side=tk.LEFT, padx=5)

        self.btn_sched = self._make_btn(
            ctrl_frame,
            text="Start Scheduler",
            icon_key="clock",
            bg=self.colors["navy"],
            fg="#ffffff",
            command=self.toggle_scheduler,
            tag="sched",
        )
        self.btn_sched.pack(side=tk.LEFT, padx=5)

        self.btn_stop = self._make_btn(
            ctrl_frame,
            text="Stop",
            icon_key="stop",
            bg="#6b7280",
            fg="#ffffff",
            command=self.stop_bot,
            tag="stop",
            state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        self.progress = ttk.Progressbar(
            left_frame, mode="indeterminate"
        )
        self.progress.pack(fill=tk.X, pady=(5, 0))

        # ── RIGHT side: log panel (always visible) ──
        # Wide enough to read full log lines without horizontal scrolling.
        # Tall enough to see many lines of history at once.
        log_frame = tk.Frame(body_frame, bg=self.colors["bg"])
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Log header with label + clear button
        log_header = tk.Frame(log_frame, bg=self.colors["bg"])
        log_header.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            log_header,
            text="📋 Activity Log",
            font=("Segoe UI", 10, "bold"),
            bg=self.colors["bg"],
            fg=self.colors.get("text", "#ffffff"),
        ).pack(side=tk.LEFT)

        # Clear log button
        def _clear_log():
            try:
                self.log_area.delete("1.0", tk.END)
            except Exception:
                pass

        clr_btn = tk.Button(
            log_header,
            text="Clear",
            font=("Segoe UI", 8),
            bg=self.colors.get("panel", "#2a2d3e"),
            fg=self.colors.get("text", "#ffffff"),
            activebackground=self.colors.get("red", "#cc3333"),
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=_clear_log,
        )
        clr_btn.pack(side=tk.RIGHT)

        # Log text area — wider and taller than before, fills the right column
        self.log_area = scrolledtext.ScrolledText(
            log_frame,
            width=70,  # wider — fits full log lines
            font=("Consolas", 9),
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground=self.colors["log_fg"],
            wrap=tk.WORD,  # wrap long lines instead of horizontal scroll
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Tag for colored log levels (optional — used by log_msg if it
        # inserts with tags like "INFO", "ERROR", "SUCCESS").
        try:
            self.log_area.tag_configure("ERROR", foreground="#ff6b6b")
            self.log_area.tag_configure("SUCCESS", foreground="#51cf66")
            self.log_area.tag_configure("WARNING", foreground="#ffd43b")
            self.log_area.tag_configure("INFO", foreground=self.colors["log_fg"])
        except Exception:
            pass

        # Copyright footer
        _cbar = tk.Frame(self, bg="#090d26", height=24)
        _cbar.pack(fill=tk.X, side="bottom")
        _cbar.pack_propagate(False)
        tk.Label(_cbar, text=f"Developed by Abad Umair Channa | Copyright © {date.today().year} | All rights reserved.",
                 font=("Segoe UI", 8), fg="#9d9db8", bg="#090d26").pack(expand=True, fill="both")

    def _build_config_tab(self):
        config_container = tk.Frame(self.tab_config)
        config_container.pack(fill=tk.X, padx=10, pady=10)

        cred_frame = ttk.LabelFrame(config_container, text="CRM Credentials")
        cred_frame.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5)
        )

        tk.Label(cred_frame, text="Account ID:").grid(
            row=0, column=0, padx=10, pady=5, sticky=tk.W
        )
        self.entry_acc = ttk.Entry(cred_frame, width=25)
        self.entry_acc.insert(0, self.config_data.get("crm_account", ""))
        self.entry_acc.grid(row=0, column=1, padx=10, pady=5)

        tk.Label(cred_frame, text="Username:").grid(
            row=1, column=0, padx=10, pady=5, sticky=tk.W
        )
        self.entry_usr = ttk.Entry(cred_frame, width=25)
        self.entry_usr.insert(
            0, self.config_data.get("crm_username", "")
        )
        self.entry_usr.grid(row=1, column=1, padx=10, pady=5)

        tk.Label(cred_frame, text="Password:").grid(
            row=2, column=0, padx=10, pady=5, sticky=tk.W
        )
        self.entry_pwd = ttk.Entry(cred_frame, width=25, show="*")
        self.entry_pwd.insert(
            0, decode_pw(self.config_data.get("crm_password", ""))
        )
        self.entry_pwd.grid(row=2, column=1, padx=10, pady=5)

        bot_frame = ttk.LabelFrame(
            config_container, text="Bot & Schedule Settings"
        )
        bot_frame.pack(
            side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0)
        )

        tk.Label(
            bot_frame, text="WhatsApp Groups (comma-separated):"
        ).grid(
            row=0, column=0, padx=10, pady=2, sticky=tk.W
        )
        self.txt_wa_groups = scrolledtext.ScrolledText(
            bot_frame, width=35, height=4, font=("Segoe UI", 9)
        )
        self.txt_wa_groups.insert(tk.END, self.wa_group)
        self.txt_wa_groups.grid(
            row=1, column=0, columnspan=2, padx=10, pady=2, sticky=tk.W
        )

        # WhatsApp mode selector: Desktop App (pyautogui) or Web (browser tab)
        wa_mode_frame = tk.Frame(bot_frame)
        wa_mode_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=(4, 2), sticky=tk.W)
        tk.Label(wa_mode_frame, text="Send via:").pack(side=tk.LEFT, padx=(0, 8))
        self.wa_mode_var = tk.StringVar(value=self.config_data.get("whatsapp_mode", "web"))
        tk.Radiobutton(wa_mode_frame, text="Desktop App", variable=self.wa_mode_var,
                       value="desktop").pack(side=tk.LEFT, padx=(0, 6))
        tk.Radiobutton(wa_mode_frame, text="WhatsApp Web", variable=self.wa_mode_var,
                       value="web").pack(side=tk.LEFT)

        # Trigger words/sentences — bot only activates when these appear
        # in a WhatsApp message (case-insensitive). Messages with images
        # containing IMEIs are OCR'd when any trigger word matches.
        tk.Label(
            bot_frame,
            text="Trigger words/sentences (comma-separated):"
        ).grid(
            row=3, column=0, padx=10, pady=(6, 2), sticky=tk.W
        )
        self.txt_trigger_words = scrolledtext.ScrolledText(
            bot_frame, width=35, height=3, font=("Segoe UI", 9)
        )
        saved_triggers = self.config_data.get("trigger_words", "transfer")
        self.txt_trigger_words.insert(tk.END, saved_triggers)
        self.txt_trigger_words.grid(
            row=4, column=0, columnspan=2, padx=10, pady=2, sticky=tk.W
        )

        tk.Label(bot_frame, text="Schedule (HH:MM):").grid(
            row=5, column=0, padx=10, pady=5, sticky=tk.W
        )
        self.entry_schedule = ttk.Entry(bot_frame, width=25)
        self.entry_schedule.insert(0, self.schedule_times)
        self.entry_schedule.grid(
            row=5, column=1, padx=10, pady=5, sticky=tk.W
        )

        self.btn_save = self._make_btn(
            bot_frame,
            text="Save All Settings",
            icon_key="save",
            bg=self.colors["navy"],
            fg="#ffffff",
            command=self.save_settings,
            width=None,
            tag="save",
        )
        self.btn_save.grid(row=6, column=0, columnspan=2, pady=6)

        map_frame = ttk.LabelFrame(
            self.tab_config, text="Store Name to Account ID Mappings"
        )
        map_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        entry_frame = tk.Frame(map_frame)
        entry_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(entry_frame, text="Store Name:").pack(
            side=tk.LEFT, padx=5
        )
        self.entry_map_store = ttk.Entry(entry_frame, width=20)
        self.entry_map_store.pack(side=tk.LEFT, padx=5)

        tk.Label(entry_frame, text="Account ID:").pack(
            side=tk.LEFT, padx=5
        )
        self.entry_map_acc = ttk.Entry(entry_frame, width=15)
        self.entry_map_acc.pack(side=tk.LEFT, padx=5)

        tk.Label(entry_frame, text="Aliases:").pack(
            side=tk.LEFT, padx=5
        )
        self.entry_map_aliases = ttk.Entry(entry_frame, width=30)
        self.entry_map_aliases.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            entry_frame, text="Add Mapping", command=self.add_mapping
        ).pack(side=tk.LEFT, padx=10)
        ttk.Button(
            entry_frame,
            text="Remove Selected",
            command=self.remove_mapping,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            entry_frame, text="Import Excel", command=self.import_excel
        ).pack(side=tk.LEFT, padx=10)

        cols = ("Store Name", "Account ID", "Aliases")
        self.tree_map = ttk.Treeview(
            map_frame, columns=cols, show="headings", height=8
        )
        for c in cols:
            self.tree_map.heading(c, text=c)
        self.tree_map.column("Store Name", width=160)
        self.tree_map.column("Account ID", width=100)
        self.tree_map.column("Aliases", width=250)
        self.tree_map.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.refresh_mappings_tree()

    def _build_history_tab(self):
        cols = ("Date", "Store", "Target Account", "IMEI Count", "Status")
        self.tree_history = ttk.Treeview(
            self.tab_history, columns=cols, show="headings"
        )
        for c in cols:
            self.tree_history.heading(c, text=c)
        self.tree_history.column("Date", width=150)
        self.tree_history.column("Store", width=150)
        self.tree_history.column("Target Account", width=120)
        self.tree_history.column("IMEI Count", width=80, anchor=tk.CENTER)
        self.tree_history.column("Status", width=250)
        self.tree_history.pack(
            fill=tk.BOTH, expand=True, padx=10, pady=10
        )

        self.refresh_history_tree()

    # --- Data Management ---

    def import_excel(self):
        file_path = filedialog.askopenfilename(
            title="Select Mappings File",
            filetypes=[
                ("Excel Files", "*.xlsx *.xls"),
                ("CSV Files", "*.csv"),
                ("All Files", "*.*"),
            ],
        )

        if not file_path:
            return

        try:
            import pandas as pd

            if file_path.endswith(".csv"):
                df = pd.read_csv(file_path, dtype=str)
            else:
                df = pd.read_excel(file_path, dtype=str)

            if len(df.columns) < 2:
                messagebox.showerror(
                    "Import Error",
                    "File must have at least 2 columns: Store Name and Account ID.",
                )
                return

            imported_count = 0
            for _index, row in df.iterrows():
                store = str(row.iloc[0]).strip()
                acc = str(row.iloc[1]).strip()

                # Check for aliases column (3rd column, if present)
                aliases_str = ""
                if len(df.columns) >= 3:
                    aliases_str = str(row.iloc[2]).strip()

                if (
                    store
                    and acc
                    and store.lower() != "nan"
                    and acc.lower() != "nan"
                ):
                    # Parse aliases (comma-separated)
                    if aliases_str and aliases_str.lower() != "nan":
                        aliases = [a.strip().lower() for a in aliases_str.split(",") if a.strip()]
                    else:
                        aliases = []
                    self.mappings[store] = {"account_id": acc, "aliases": aliases}
                    imported_count += 1

            self.config_data["transfer_mappings"] = self.mappings
            save_config(self.config_data)
            self.refresh_mappings_tree()

            self.log_msg(
                f"Successfully imported {imported_count} mappings from file."
            )
            messagebox.showinfo(
                "Success", f"Imported {imported_count} mappings."
            )

        except ImportError:
            messagebox.showerror(
                "Dependency Error",
                "Please run 'pip install pandas openpyxl' to enable Excel imports.",
            )
        except Exception as e:
            messagebox.showerror(
                "Import Error", f"An error occurred while importing:\n{e}"
            )
            self.log_msg(f"Import Error: {e}")

    def save_settings(self):
        self.config_data["crm_account"] = self.entry_acc.get().strip()
        self.config_data["crm_username"] = self.entry_usr.get().strip()
        self.config_data["crm_password"] = encode_pw(
            self.entry_pwd.get().strip()
        )
        self.config_data["transfer_wa_group"] = (
            self.txt_wa_groups.get("1.0", tk.END).strip()
        )
        self.config_data["whatsapp_mode"] = self.wa_mode_var.get()
        self.config_data["trigger_words"] = (
            self.txt_trigger_words.get("1.0", tk.END).strip()
        )
        self.config_data["transfer_schedule"] = (
            self.entry_schedule.get().strip()
        )

        self.wa_group = self.config_data["transfer_wa_group"]
        self.schedule_times = self.config_data["transfer_schedule"]
        self.config_data["theme"] = self.theme_setting

        save_config(self.config_data)
        self.log_msg("Settings and Credentials saved securely to config.")

    def add_mapping(self):
        store = self.entry_map_store.get().strip()
        acc = self.entry_map_acc.get().strip()
        aliases_str = self.entry_map_aliases.get().strip()
        if store and acc:
            # Migrate old format: if mappings[store] is a string, convert to dict
            if store in self.mappings and isinstance(self.mappings[store], str):
                old_acc = self.mappings[store]
                self.mappings[store] = {"account_id": old_acc, "aliases": []}
            
            # Ensure dict format
            if store not in self.mappings or isinstance(self.mappings[store], str):
                self.mappings[store] = {"account_id": acc, "aliases": []}
            else:
                self.mappings[store]["account_id"] = acc
            
            # Parse aliases (comma-separated)
            if aliases_str:
                aliases = [a.strip().lower() for a in aliases_str.split(",") if a.strip()]
                self.mappings[store]["aliases"] = aliases
            else:
                self.mappings[store]["aliases"] = []
            
            self.config_data["transfer_mappings"] = self.mappings
            save_config(self.config_data)
            self.refresh_mappings_tree()
            self.entry_map_store.delete(0, tk.END)
            self.entry_map_acc.delete(0, tk.END)
            self.entry_map_aliases.delete(0, tk.END)

    def remove_mapping(self):
        selected = self.tree_map.selection()
        if not selected:
            return
        for item in selected:
            store = self.tree_map.item(item, "values")[0]
            if store in self.mappings:
                del self.mappings[store]
        self.config_data["transfer_mappings"] = self.mappings
        save_config(self.config_data)
        self.refresh_mappings_tree()

    def refresh_mappings_tree(self):
        for item in self.tree_map.get_children():
            self.tree_map.delete(item)
        for store, data in self.mappings.items():
            # Handle both old (string) and new (dict) format
            if isinstance(data, str):
                acc = data
                aliases = ""
            else:
                acc = data.get("account_id", "")
                aliases = ", ".join(data.get("aliases", []))
            self.tree_map.insert("", tk.END, values=(store, acc, aliases))

    def append_history(self, store, acc, count, status):
        record = (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            store,
            acc,
            count,
            status,
        )
        self.history.append(record)
        self.config_data["transfer_history"] = self.history[-100:]
        save_config(self.config_data)
        self.refresh_history_tree()

    def refresh_history_tree(self):
        for item in self.tree_history.get_children():
            self.tree_history.delete(item)
        for rec in reversed(self.history[-100:]):
            self.tree_history.insert("", tk.END, values=rec)

    # --- Logging & UI Updates ---

    def log_msg(self, msg):
        self.log_queue.put(
            f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
        )

    def _process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                # Auto-detect log level for colored output.
                # Looks at the message content for keywords like
                # "ERROR", "FAILED", "✅", "⚠️", "✋", etc.
                tag = "INFO"
                msg_upper = msg.upper()
                if any(k in msg_upper for k in ("ERROR", "FAILED", "CRITICAL")):
                    tag = "ERROR"
                elif any(k in msg for k in ("✅", "SUCCESS", "✓")):
                    tag = "SUCCESS"
                elif any(k in msg for k in ("⚠️", "WARNING", "✋", "SKIP")):
                    tag = "WARNING"

                self.log_area.insert(tk.END, msg, tag)
                self.log_area.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._process_log_queue)

    def _prompt_vpn_connect(self):
        """Called from the workflow worker thread after the browser opens.

        Marshals a modal prompt onto the UI thread asking the user to
        connect their VPN, and blocks until they click OK/Cancel. Returns
        True to proceed with the login, False to abort the run.
        """
        ready = threading.Event()
        proceed = {"ok": False}

        def ask():
            proceed["ok"] = messagebox.askokcancel(
                "Connect VPN",
                "The browser is now open.\n\n"
                "Connect your VPN now (if not already connected), then "
                "click OK to continue to the VidaPay login page.",
            )
            ready.set()

        self.after(0, ask)
        ready.wait()
        return proceed["ok"]

    # ------------------------------------------------------------------
    # VidaPay Branding: logo, icons, themes
    # ------------------------------------------------------------------

    def _resolve_theme(self):
        if self.theme_setting == "system":
            return self._detect_system_theme()
        return self.theme_setting

    def _on_header_theme_toggle(self):
        """Callback for the header's sun/moon button.

        The header toggle flips self.theme_manager.current_theme (an
        imported ThemeManager instance used only to drive the icon), but
        _apply_theme()/_style_ttk()/_theme_walk() actually read a
        different variable, self.theme_setting. Previously this callback
        pointed straight at self._apply_theme(), so clicking the header
        button changed the icon but nothing else on screen. Sync the two
        here so the header button drives the app's real theme state.
        """
        self.theme_setting = self.theme_manager.current_theme
        self.config_data["theme"] = self.theme_setting
        save_config(self.config_data)
        self._apply_theme()
        self._update_theme_btn()

    def _detect_system_theme(self):
        """Follow the Windows light/dark mode when 'System' is selected."""
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value else "dark"
        except Exception:
            return "light"

    def _toggle(self):
        """One-click toggle between light and dark (no dropdown)."""
        current = self._resolve_theme()
        self.theme_setting = "dark" if current == "light" else "light"
        self.config_data["theme"] = self.theme_setting
        save_config(self.config_data)
        self._apply_theme()
        self._update_theme_btn()

    def _update_theme_btn(self):
        """Show which theme the button will switch to and style it."""
        current = self._resolve_theme()
        next_theme = "dark" if current == "light" else "light"
        try:
            self.theme_btn.config(
                text="Switch to Dark" if next_theme == "dark" else "Switch to Light",
            )
            # Ink the button to match the current theme navbar accent.
            btn_style = ttk.Style()
            btn_style.configure(
                "ThemeToggle.TButton",
                background=self.colors["navy"],
                foreground="#ffffff",
                bordercolor=self.colors["navy"],
                focusthickness=0,
            )
            btn_style.map(
                "ThemeToggle.TButton",
                background=[("active", self.colors["red"])],
            )
            self.theme_btn.configure(style="ThemeToggle.TButton")
        except Exception:
            pass

    def _apply_theme(self):
        self.colors = THEMES[self._resolve_theme()]
        self.configure(bg=self.colors["bg"])
        self._style_ttk()
        self._theme_walk(self)
        self._update_theme_btn()

    def _style_ttk(self):
        c = self.colors
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=c["bg"])
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=c["panel_alt"],
            foreground=c["text_dim"],
            font=("Segoe UI", 10, "bold"),
            padding=[12, 6],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["red"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "TLabelframe",
            background=c["bg"],
            bordercolor=c["border"],
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=c["bg"],
            foreground=c["text"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TEntry",
            fieldbackground=c["input"],
            foreground=c["text"],
            insertcolor=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
        )
        style.map(
            "TEntry",
            fieldbackground=[("focus", c["input"])],
            bordercolor=[("focus", c["red"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=c["input"],
            background=c["panel_alt"],
            foreground=c["text"],
            arrowcolor=c["text_dim"],
            bordercolor=c["border"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["input"])],
            foreground=[("readonly", c["text"])],
            selectbackground=[("readonly", c["input"])],
            selectforeground=[("readonly", c["text"])],
        )
        style.configure(
            "Treeview",
            background=c["panel"],
            fieldbackground=c["panel"],
            foreground=c["text"],
            bordercolor=c["border"],
            rowheight=26,
        )
        style.configure(
            "Treeview.Heading",
            background=c["navy"],
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", c["red"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "TButton",
            background=c["panel_alt"],
            foreground=c["text"],
            bordercolor=c["border"],
            padding=[10, 5],
            font=("Segoe UI", 9),
        )
        style.map(
            "TButton",
            background=[("active", c["border"])],
            foreground=[("active", c["text"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=c["red"],
            troughcolor=c["panel_alt"],
            bordercolor=c["border"],
            lightcolor=c["red"],
            darkcolor=c["red"],
        )

    def _theme_walk(self, widget):
        """Recursively re-color every non-ttk widget from the active theme."""
        try:
            if not isinstance(widget, ttk.Widget):
                tag = getattr(widget, "_tag", None)
                if tag == "header":
                    widget.configure(bg=self.colors["navy"])
                elif tag == "header_label":
                    widget.configure(bg=self.colors["navy"])
                elif tag == "run":
                    widget.configure(
                        bg=self.colors["red"],
                        fg="#ffffff",
                        activebackground="#d84410",
                        activeforeground="#ffffff",
                    )
                elif tag == "sched":
                    # Scheduler button: navy when idle, red when running (Stop Scheduler)
                    try:
                        txt = str(widget.cget("text")).lower()
                    except Exception:
                        txt = ""
                    if "stop" in txt:
                        widget.configure(
                            bg=self.colors["red"],
                            fg="#ffffff",
                            activebackground="#d84410",
                            activeforeground="#ffffff",
                        )
                    else:
                        widget.configure(
                            bg=self.colors["navy"],
                            fg="#ffffff",
                            activebackground="#1b2047",
                            activeforeground="#ffffff",
                        )
                elif tag == "save":
                    widget.configure(
                        bg=self.colors["navy"],
                        fg="#ffffff",
                        activebackground="#1b2047",
                        activeforeground="#ffffff",
                    )
                elif tag == "stop":
                    widget.configure(
                        bg="#6b7280",
                        fg="#ffffff",
                        activebackground="#565e6c",
                        activeforeground="#ffffff",
                    )
                elif isinstance(widget, tk.Button):
                    widget.configure(
                        bg=self.colors["panel_alt"],
                        fg=self.colors["text"],
                        activebackground=self.colors["border"],
                        activeforeground=self.colors["text"],
                    )
                elif isinstance(widget, tk.Label):
                    widget.configure(
                        bg=self.colors["bg"], fg=self.colors["text"]
                    )
                elif isinstance(widget, tk.Frame):
                    widget.configure(bg=self.colors["bg"])
                elif isinstance(widget, scrolledtext.ScrolledText):
                    widget.configure(
                        bg=self.colors["log_bg"],
                        fg=self.colors["log_fg"],
                        insertbackground=self.colors["log_fg"],
                    )
                elif isinstance(widget, tk.Text):
                    widget.configure(
                        bg=self.colors["log_bg"],
                        fg=self.colors["log_fg"],
                        insertbackground=self.colors["log_fg"],
                    )
                elif isinstance(widget, tk.Entry):
                    widget.configure(
                        bg=self.colors["input"],
                        fg=self.colors["text"],
                        insertbackground=self.colors["text"],
                    )
        except Exception:
            pass
        for child in widget.winfo_children():
            self._theme_walk(child)

    def _load_brand_assets(self):
        """Load the real VidaPay logo + window icon (embedded), else render fallbacks."""
        # Try _MEIPASS first (PyInstaller onefile extraction dir)
        import sys as _sys, os as _os
        _meipass = getattr(_sys, "_MEIPASS", None)
        if _meipass:
            for _ico_name in ("vidapay_icon.ico", "vidapay_icon.ico", "vidapay_icon.ico"):
                _ico_path = _os.path.join(_meipass, _ico_name)
                if _os.path.exists(_ico_path):
                    try:
                        self.iconbitmap(default=_ico_path)
                        self.after(200, lambda p=_ico_path: self.iconbitmap(default=p))
                    except Exception:
                        pass
                    break
        # Fallback: decode EMBEDDED_ICON_B64 to %TEMP%
        try:
            import base64 as _b64, tempfile as _tf
            data = _b64.b64decode(EMBEDDED_ICON_B64.strip())
            _tmp_dir = _os.environ.get("TEMP", _tf.gettempdir())
            _ico_path = _os.path.join(_tmp_dir, "vidapay_transfer_icon.ico")
            with open(_ico_path, "wb") as _f:
                _f.write(data)
            self.iconbitmap(default=_ico_path)
            self.after(200, lambda p=_ico_path: self.iconbitmap(default=p))
        except Exception:
            pass
        # Header logo: embedded real VidaPay logo first, rendered badge as fallback
        logo_path = self._extract_embedded(EMBEDDED_LOGO_B64, "vidapay_logo_real.png")
        if not logo_path:
            logo_path = self._build_logo_image()
        if logo_path:
            try:
                self.images["logo"] = tk.PhotoImage(file=logo_path)
            except Exception:
                self.images["logo"] = None
        for key in ("play", "clock", "stop", "save"):
            self.images[key] = self._build_icon(key, "#ffffff")

    def _extract_embedded(self, b64, filename):
        """Decode an embedded base64 asset into APP_DATA_DIR; return path or None."""
        try:
            if not b64:
                return None
            import base64 as _b64
            target = os.path.join(APP_DATA_DIR, filename)
            with open(target, "wb") as fh:
                fh.write(_b64.b64decode(b64))
            return target if os.path.isfile(target) else None
        except Exception:
            return None

    def _build_logo_image(self, size=46):
        """Render a red 'VidaPay' badge logo and save it next to the config."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                [2, 2, size - 2, size - 2],
                radius=int(size * 0.28),
                fill=BRAND_RED,
            )
            font = None
            for font_path in (
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ):
                try:
                    font = ImageFont.truetype(font_path, int(size * 0.40))
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()
            text = "VidaPay"
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
                text,
                font=font,
                fill="#ffffff",
            )
            path = os.path.join(APP_DATA_DIR, "vidapay_logo.png")
            img.save(path)
            return path
        except Exception:
            return None

    def _build_icon(self, kind, color="#ffffff", size=18):
        """Render a simple white icon for the action buttons."""
        try:
            from PIL import Image, ImageDraw

            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            s = float(size)
            w1 = max(1, int(s * 0.08))
            if kind == "play":
                d.polygon(
                    [
                        (int(s * 0.32), int(s * 0.16)),
                        (int(s * 0.32), int(s * 0.84)),
                        (int(s * 0.86), int(s * 0.50)),
                    ],
                    fill=color,
                )
            elif kind == "clock":
                r = s * 0.40
                cx, cy = s / 2, s / 2
                d.ellipse(
                    [cx - r, cy - r, cx + r, cy + r],
                    outline=color,
                    width=w1,
                )
                d.line([cx, cy, cx, cy - r * 0.62], fill=color, width=w1)
                d.line([cx, cy, cx + r * 0.52, cy], fill=color, width=w1)
            elif kind == "stop":
                pad = s * 0.24
                d.rectangle([pad, pad, s - pad, s - pad], fill=color)
            elif kind == "save":
                d.rounded_rectangle(
                    [s * 0.18, s * 0.22, s * 0.82, s * 0.88],
                    radius=s * 0.08,
                    outline=color,
                    width=w1,
                )
                d.polygon(
                    [
                        (s * 0.18, s * 0.40), (s * 0.40, s * 0.40),
                        (s * 0.40, s * 0.22), (s * 0.60, s * 0.22),
                        (s * 0.60, s * 0.40), (s * 0.82, s * 0.40),
                        (s * 0.82, s * 0.62), (s * 0.60, s * 0.62),
                        (s * 0.60, s * 0.88), (s * 0.40, s * 0.88),
                        (s * 0.40, s * 0.62), (s * 0.18, s * 0.62),
                    ],
                    fill=color,
                )
            path = os.path.join(APP_DATA_DIR, "icon_{0}.png".format(kind))
            img.save(path)
            return tk.PhotoImage(file=path)
        except Exception:
            return None

    def _make_btn(self, parent, text, icon_key, bg, fg, command,
                  width=None, tag=None, state=None):
        """Create a flat branded button with an optional icon.

        width=None lets the button auto-size to fit its text + icon.
        Pass an explicit width only when you need a fixed-width button.
        """
        kw = dict(
            text=text,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=command,
            width=width,
        )
        if state is not None:
            kw["state"] = state
        btn = tk.Button(parent, **kw)
        icon = self.images.get(icon_key)
        if icon is not None:
            btn.configure(image=icon, compound=tk.LEFT)
            btn._imgref = icon
        if tag:
            btn._tag = tag
        return btn

    # ------------------------------------------------------------------
    # OCR dependency auto-setup (Tesseract / Ghostscript / pip packages)
    # ------------------------------------------------------------------

    def _auto_setup_deps(self):
        """Check for missing OCR components; ask to install what's absent."""
        missing_tools = []
        if not _is_tesseract_installed():
            missing_tools.append("Tesseract OCR")
        if not _ghostscript_installed():
            missing_tools.append("Ghostscript")
        missing_pkgs = _missing_python_packages()

        if not missing_tools and not missing_pkgs:
            self.log_msg(
                "OCR dependencies OK (Tesseract + Ghostscript found)."
            )
            return

        self.after(
            0,
            lambda: self._confirm_setup_deps(missing_tools, missing_pkgs),
        )

    def _confirm_setup_deps(self, missing_tools, missing_pkgs):
        title = "Install OCR Dependencies"
        msg = (
            "The bot needs a few extra components to read IMEIs "
            "from WhatsApp images:\n\n"
        )
        if missing_tools:
            msg += (
                "System tools missing: "
                + ", ".join(
                    PIP_DEPENDENCIES.get(t, t) for t in missing_tools
                )
                + "\n"
            )
        if missing_pkgs:
            msg += (
                "Python packages missing: "
                + ", ".join(
                    PIP_DEPENDENCIES.get(p, p) for p in missing_pkgs
                )
                + "\n"
            )
        msg += (
            "\nInstall automatically now? This downloads the installers "
            "and may take a few minutes."
        )
        if messagebox.askyesno(title, msg):
            threading.Thread(
                target=self._install_deps_worker,
                args=(missing_tools, missing_pkgs),
                daemon=True,
            ).start()
        else:
            self.log_msg(
                "Skipped automatic install. OCR needs Tesseract to read IMEIs."
            )

    def _install_deps_worker(self, missing_tools, missing_pkgs):
        try:
            if missing_pkgs:
                self.log_msg("Installing missing Python packages...")
                pip_names = [
                    PIP_DEPENDENCIES.get(pkg, pkg) for pkg in missing_pkgs
                ]
                pip_cmd = _pip_cmd()
                if pip_cmd is None:
                    self.log_msg(
                        "Python not found for pip - install it from "
                        "https://www.python.org/downloads/ first."
                    )
                    ok, output = False, "no python interpreter found"
                else:
                    ok, output = _run_cmd_quiet(
                        pip_cmd + ["install", "--upgrade"] + pip_names,
                        timeout=600,
                    )
                if ok:
                    self.log_msg("Python packages installed.")
                else:
                    self.log_msg("pip install failed: " + output[-400:])

            if "Tesseract OCR" in missing_tools:
                self._install_tesseract()

            if "Ghostscript" in missing_tools:
                self._install_ghostscript()

            self.log_msg("Dependency setup finished.")
        except Exception as e:
            self.log_msg("Dependency setup error: " + str(e))

    def _install_tesseract(self):
        self.log_msg("Installing Tesseract OCR...")
        # Preferred: the Windows package manager
        if _tool_on_path("winget"):
            ok, _out = _run_cmd_quiet(
                [
                    "winget", "install", "--id",
                    "UB-Mannheim.TesseractOCR", "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements", "--silent",
                ],
                timeout=600,
            )
            self._refresh_tesseract_path()
            if _is_tesseract_installed():
                self.log_msg("Tesseract installed via winget.")
                return
        # Fallback: official silent installer
        installer = os.path.join(APP_DATA_DIR, "tesseract-setup.exe")
        if _download_file(TESSERACT_URL, installer, self.log_msg):
            _run_cmd_quiet([installer, "/S"], timeout=900)
            self._refresh_tesseract_path()
            if _is_tesseract_installed():
                self.log_msg("Tesseract installed from official installer.")
                return
        self.log_msg(
            "Tesseract install failed - install it manually from "
            "https://digi.bib.uni-mannheim.de/tesseract/ and restart the bot."
        )

    def _install_ghostscript(self):
        self.log_msg("Installing Ghostscript (optional image support)...")
        if _tool_on_path("winget"):
            ok, _out = _run_cmd_quiet(
                [
                    "winget", "install", "--id",
                    "ArtifexSoftware.Ghostscript", "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements", "--silent",
                ],
                timeout=600,
            )
            if ok and _ghostscript_installed():
                self.log_msg("Ghostscript installed via winget.")
                return
        self.log_msg(
            "Ghostscript not installed (optional - only needed for some "
            "image formats; PNG screenshots work without it)."
        )

    def _refresh_tesseract_path(self):
        path = _locate_tesseract()
        pytesseract.pytesseract.tesseract_cmd = path
        if os.path.isfile(path):
            self.log_msg("Tesseract located at: " + path)

    def ui_state_running(self):
        self.btn_run.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress.start(10)

    def ui_state_stopped(self):
        self.btn_run.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.progress.stop()

    # --- Scheduler ---

    def toggle_scheduler(self):
        if self.scheduler_running:
            self.scheduler_running = False
            # FIX #4: Clear stale jobs when stopping
            schedule.clear()
            self.btn_sched.config(
                text="\u23f0 Start Scheduler", bg=self.colors["navy"],
                activebackground="#1b2047"
            )
            self.log_msg("Scheduler stopped and jobs cleared.")
        else:
            schedule.clear()
            times = [
                t.strip()
                for t in self.schedule_times.split(",")
                if t.strip()
            ]
            for t in times:
                try:
                    schedule.every().day.at(t).do(self.run_manual)
                    self.log_msg(f"Scheduled transfer job at {t}.")
                except Exception as e:
                    self.log_msg(f"Invalid time format '{t}': {e}")

            if schedule.get_jobs():
                self.scheduler_running = True
                self.btn_sched.config(
                    text="\u23f0 Stop Scheduler", bg=self.colors["red"],
                    activebackground="#d84410"
                )
                self.scheduler_thread = threading.Thread(
                    target=self._scheduler_loop, daemon=True
                )
                self.scheduler_thread.start()
            else:
                self.log_msg("No valid schedule times provided.")

    def _scheduler_loop(self):
        while self.scheduler_running:
            schedule.run_pending()
            time.sleep(1)

    # --- Bot Execution ---

    def run_manual(self):
        if not self.mappings:
            messagebox.showwarning(
                "Missing Config",
                "Please add Store Name to Account ID mappings first.",
            )
            return

        wa_groups_str = (
            self.txt_wa_groups.get("1.0", tk.END).strip()
        )
        if not wa_groups_str:
            messagebox.showwarning(
                "Missing Config",
                "Please set the WhatsApp Group(s) to monitor.",
            )
            return

        acc = self.entry_acc.get().strip()
        usr = self.entry_usr.get().strip()
        pwd = self.entry_pwd.get().strip()

        if not all([acc, usr, pwd]):
            messagebox.showwarning(
                "Missing Config",
                "Please enter CRM Account, Username, and Password in settings.",
            )
            return

        self.stop_event.clear()
        self.ui_state_running()
        wa_mode = self.wa_mode_var.get()
        trigger_words_str = self.txt_trigger_words.get("1.0", tk.END).strip()
        threading.Thread(
            target=self._bot_workflow,
            args=(acc, usr, pwd, wa_groups_str, wa_mode, trigger_words_str),
            daemon=True,
        ).start()

    def stop_bot(self):
        self.log_msg("Sending stop signal... Please wait.")
        self.stop_event.set()

    def _process_one_task(self, task, crm_system, wa_scraper, wa_mode):
        """Process a single transfer task end-to-end:
        navigate to CRM → execute transfer → send WhatsApp reply.

        Refactored out of _bot_workflow so both the initial-scan path and
        the monitoring loop can call the same code.
        """
        # Navigate to the Transfer Tool
        if not crm_system.navigate_to_transfer_tool():
            self.log_msg(
                f"Could not open Reassignment Tool for {task['store']}. Skipping."
            )
            self.append_history(
                task["store"], task["account_id"],
                len(task["imeis"]), "FAILED — Navigation error",
            )
            return

        # Clear any previous error screenshots
        if hasattr(crm_system, '_error_screenshots'):
            crm_system._error_screenshots = []

        success = crm_system.execute_transfer(
            task["account_id"], task["imeis"]
        )
        status = "SUCCESS" if success else "FAILED"
        self.append_history(
            task["store"], task["account_id"],
            len(task["imeis"]), status,
        )

        # Send WhatsApp reply to the same group
        if wa_scraper and wa_mode != "desktop":
            if success:
                reply_msg = (
                    f"✅ Transfer completed: {len(task['imeis'])} device(s) "
                    f"transferred to {task['store']} "
                    f"(Account: {task['account_id']})"
                )
            else:
                error_screenshots = getattr(crm_system, '_error_screenshots', [])
                if error_screenshots:
                    error_details = "; ".join(
                        f"{e['imei']}: {e['error']}" for e in error_screenshots
                    )
                    reply_msg = (
                        f"⚠️ Transfer to {task['store']} had errors: "
                        f"{error_details}"
                    )
                else:
                    reply_msg = (
                        f"❌ Transfer to {task['store']} FAILED — "
                        f"could not interact with the VidaPay transfer page. "
                        f"Please check manually."
                    )

            self.log_msg(f"Sending WhatsApp reply to '{task['group']}'...")
            try:
                wa_scraper.send_reply(task["group"], reply_msg)
            except Exception as reply_err:
                self.log_msg(f"WhatsApp reply failed: {reply_err}")
            time.sleep(2)

            # Switch back to main CRM tab (the temp CRM tab was already
            # closed by _navigate_back_to_main_panel inside execute_transfer).
            try:
                if (
                    getattr(crm_system, "main_window", None)
                    and crm_system.main_window in crm_system.driver.window_handles
                ):
                    crm_system.driver.switch_to.window(crm_system.main_window)
            except Exception:
                pass
        else:
            self.log_msg(
                f"Transfer {status} for {task['store']} — "
                f"WhatsApp reply skipped (Desktop mode or no scraper)."
            )

        self.log_msg(f"Task complete: {task['store']} → {status}.")

    def _bot_workflow(self, crm_acc, crm_usr, crm_pwd, wa_groups_str, wa_mode="web", trigger_words_str=""):
        self.log_msg("=== Starting Transfer Bot Workflow ===")
        wa_scraper = None
        crm_system = None

        # With ATTACH_TO_OPEN_EDGE the dedicated Edge window stays open
        # between runs (extensions/VPN/profile persist) and this run simply
        # re-attaches to it through the remote debugging port, so there is
        # no prior driver session to force-quit.
        if self.retained_driver is not None:
            if ATTACH_TO_OPEN_EDGE:
                # The dedicated Edge window (profile, extensions, VPN)
                # stays open; this run re-attaches via remote debugging.
                self.log_msg("Reusing the open automation Edge window...")
                self.retained_driver = None
            else:
                try:
                    self.log_msg("Closing previous browser session...")
                    self.retained_driver.quit()
                except Exception:
                    pass
                finally:
                    self.retained_driver = None

        try:
            # 1. VidaPay CRM opens FIRST (browser ordering)
            crm_system = VidapayTransferSystem(
                crm_acc, crm_usr, crm_pwd,
                self.log_msg, self.stop_event,
                vpn_pause_cb=self._prompt_vpn_connect,
            )

            if not crm_system.start_browser_and_login():
                self.log_msg("Failed to log in to CRM. Aborting run.")
                return

            # 1b. Open WhatsApp Web in a second tab alongside the CRM tab.
            #     The sign-in flow just completed, so the CRM tab is ready.
            wa_tab_handle = None
            self.log_msg("Opening WhatsApp Web in a second tab...")
            try:
                # Make sure we're on the CRM tab first
                try:
                    crm_system.driver.switch_to.window(crm_system.main_window)
                except Exception:
                    pass

                if not open_blank_normal_tab(crm_system.driver, log=self.log_msg):
                    self.log_msg("Could not create a WhatsApp Web tab — continuing with CRM only.")
                else:
                    # Navigate the new tab to WhatsApp Web
                    crm_system.driver.get("https://web.whatsapp.com")
                    time.sleep(3)
                    wa_tab_handle = crm_system.driver.current_window_handle
                    self.log_msg("WhatsApp Web tab opened alongside VidaPay CRM.")
                    # Switch back to the CRM tab so automation continues there
                    crm_system.driver.switch_to.window(crm_system.main_window)
                    time.sleep(1)
            except Exception as wa_err:
                self.log_msg(f"WhatsApp Web tab failed (continuing with CRM): {wa_err}")

            # 2. WhatsApp: Web tab (default) or Desktop pyautogui
            if wa_mode == "desktop":
                self.log_msg("WhatsApp mode: Desktop App (pyautogui)")
                # Use pyautogui to focus WhatsApp Desktop and search group
                try:
                    import pyautogui as _pag
                    import ctypes, time as _t
                    _pag.FAILSAFE = False
                    groups = [g.strip() for g in wa_groups_str.split(",") if g.strip()]
                    for grp in groups:
                        # Focus WhatsApp Desktop
                        import win32gui
                        def _find_wa(hwnd, _):
                            if win32gui.IsWindowVisible(hwnd) and "WhatsApp" in (win32gui.GetWindowText(hwnd) or ""):
                                ctypes.windll.user32.SetForegroundWindow(hwnd)
                        win32gui.EnumWindows(_find_wa, None)
                        _t.sleep(1.0)
                        _pag.hotkey("ctrl", "f"); _t.sleep(0.7)
                        _pag.hotkey("ctrl", "a"); _t.sleep(0.2)
                        _pag.write(grp, interval=0.05); _t.sleep(1.2)
                        _pag.press("enter"); _t.sleep(1.5)
                        _pag.write("Transfer complete", interval=0.05)
                        _t.sleep(0.5); _pag.press("enter"); _t.sleep(0.5)
                        self.log_msg(f"WhatsApp Desktop message sent to '{grp}'.")
                except Exception as e:
                    self.log_msg(f"WhatsApp Desktop send error: {e}")
                wa_scraper = None
            else:
                self.log_msg("WhatsApp mode: WhatsApp Web (browser tab)")
                wa_scraper = WhatsAppScraper(
                    wa_groups_str, self.log_msg, self.stop_event
                )
                # Reuse the WhatsApp Web tab we already opened (don't open another)
                if wa_tab_handle:
                    wa_scraper.driver = crm_system.driver
                    wa_scraper.owns_driver = False
                    wa_scraper.wait = WebDriverWait(crm_system.driver, 30)
                    wa_scraper.wa_window = wa_tab_handle
                    # Switch to the WhatsApp Web tab
                    crm_system.driver.switch_to.window(wa_tab_handle)
                    if not wa_scraper._wait_for_whatsapp_session():
                        self.log_msg(
                            "Failed to load WhatsApp Web in the shared browser. "
                            "Aborting run."
                        )
                        return
                else:
                    if not wa_scraper.start_whatsapp(
                        shared_driver=crm_system.driver
                    ):
                        self.log_msg(
                            "Failed to open WhatsApp Web in the shared browser. "
                            "Aborting run."
                        )
                        return

            tasks = wa_scraper.find_and_read_groups(self.mappings, trigger_words_str)
            self.log_msg(
                f"Extraction complete: {len(tasks)} transfer request(s) found."
            )

            # ── "Wait for reply" feature ──────────────────────────────────
            # Instead of processing transfers immediately, we add them to a
            # pending queue.  Each task has a `process_after` timestamp set
            # to `detected_at + WAIT_FOR_REPLY_SECONDS`.  The monitoring loop
            # re-checks each pending task's group for handling replies
            # ("on it", "doing", etc.) — if found, the task is skipped.
            # When `process_after` is reached without a reply, the task is
            # processed.
            pending_tasks = []
            # Track recently processed/skipped tasks to avoid re-queuing the
            # same transfer request from subsequent monitoring scans.  Each
            # entry is a (store, imeis_tuple, timestamp) tuple.  Entries
            # expire after 5 minutes so a genuinely new request for the same
            # store + IMEIs can be queued later.
            recently_processed = []
            RECENT_TTL = 300  # 5 minutes
            for t in tasks:
                t["process_after"] = t.get("detected_at", time.time()) + WAIT_FOR_REPLY_SECONDS
                pending_tasks.append(t)
                # Also add to recently_processed so the first monitoring
                # scan doesn't re-queue the same task.
                recently_processed.append(
                    (t["store"], tuple(sorted(t["imeis"])), time.time())
                )

            if not pending_tasks:
                self.log_msg(
                    "No pending transfer requests found in initial scan. "
                    "Entering monitoring mode..."
                )
            else:
                self.log_msg(
                    f"{len(pending_tasks)} transfer request(s) queued. "
                    f"Waiting {WAIT_FOR_REPLY_SECONDS}s before processing each "
                    f"(checking for 'on it' / 'doing' replies)."
                )
                for i, t in enumerate(pending_tasks, 1):
                    self.log_msg(
                        f"  [{i}/{len(pending_tasks)}] {t['store']} "
                        f"({len(t['imeis'])} IMEIs) from '{t['group']}' — "
                        f"will process in {WAIT_FOR_REPLY_SECONDS}s unless someone replies."
                    )

            self.log_msg("=== Initial Scan Complete ===")

            # ── Continuous monitoring loop ───────────────────────────────────
            # Polls pending_tasks every MONITOR_INTERVAL seconds.  For each
            # task whose process_after has not yet been reached, re-checks
            # the group for handling replies.  When process_after is reached
            # and no reply was found, processes the transfer.
            #
            # Also scans for NEW transfer requests every MONITOR_INTERVAL.
            MONITOR_INTERVAL = 10  # seconds between checks

            self.log_msg(
                f"Entering continuous monitoring mode — checking every {MONITOR_INTERVAL}s. "
                f"Click Stop to exit."
            )

            while not self.stop_event.is_set():
                time.sleep(MONITOR_INTERVAL)
                if self.stop_event.is_set():
                    break

                now = time.time()

                # ── Step 1: Re-check pending tasks for replies / timeout ──
                still_pending = []
                for task in pending_tasks:
                    if self.stop_event.is_set():
                        break

                    # Has the wait period elapsed?
                    wait_remaining = task["process_after"] - now

                    if wait_remaining > 0:
                        # Still waiting.  Re-check the group for handling
                        # replies every REPLY_CHECK_INTERVAL_SECONDS.
                        last_check = task.get("last_reply_check", 0)
                        if now - last_check >= REPLY_CHECK_INTERVAL_SECONDS:
                            self.log_msg(
                                f"\n--- Reply check: '{task['group']}' for "
                                f"{task['store']} "
                                f"({int(wait_remaining)}s remaining) ---"
                            )
                            try:
                                replied, reply_text = wa_scraper.check_for_reply_in_group(
                                    task["group"],
                                    task.get("trigger_text", ""),
                                )
                            except Exception as rc_err:
                                self.log_msg(f"Reply check error: {rc_err}")
                                replied, reply_text = False, ""
                            task["last_reply_check"] = now

                            if replied:
                                self.log_msg(
                                    f"✋ SKIP: Someone is handling the transfer "
                                    f"to {task['store']} in '{task['group']}': "
                                    f"\"{reply_text[:80]}\""
                                )
                                self.append_history(
                                    task["store"], task["account_id"],
                                    len(task["imeis"]),
                                    "SKIPPED — Someone replied (handling manually)",
                                )
                                # Mark as recently processed so subsequent
                                # monitoring scans don't re-queue it.
                                recently_processed.append(
                                    (task["store"], tuple(sorted(task["imeis"])), time.time())
                                )
                                # Do NOT add to still_pending — drop this task.
                                continue
                        # Keep waiting.
                        still_pending.append(task)
                        continue

                    # ── wait period elapsed → claim & process the transfer ──
                    # Race-condition guard: do ONE final reply-check right
                    # before claiming, in case someone replied between the
                    # last periodic check and now (e.g. they typed "on it"
                    # at second 29 but our last check was at second 15).
                    try:
                        final_replied, final_reply_text = (
                            wa_scraper.check_for_reply_in_group(
                                task["group"],
                                task.get("trigger_text", ""),
                            )
                        )
                    except Exception as final_err:
                        self.log_msg(f"Final reply check error: {final_err}")
                        final_replied, final_reply_text = False, ""

                    if final_replied:
                        self.log_msg(
                            f"✋ SKIP (final check): Someone is handling the "
                            f"transfer to {task['store']} in '{task['group']}': "
                            f"\"{final_reply_text[:80]}\""
                        )
                        self.append_history(
                            task["store"], task["account_id"],
                            len(task["imeis"]),
                            "SKIPPED — Someone replied (handling manually)",
                        )
                        # Mark as recently processed so subsequent
                        # monitoring scans don't re-queue it.
                        recently_processed.append(
                            (task["store"], tuple(sorted(task["imeis"])), time.time())
                        )
                        # Do NOT re-add to still_pending — drop this task.
                        continue

                    # No human has replied → claim the transfer by posting
                    # "on it" in the group, then process it.
                    self.log_msg(
                        f"\n{'='*60}\n"
                        f"No reply after {WAIT_FOR_REPLY_SECONDS}s — claiming "
                        f"transfer: {task['store']} ({len(task['imeis'])} IMEIs) "
                        f"from '{task['group']}'\n"
                        f"{'='*60}"
                    )

                    if wa_scraper and wa_mode != "desktop":
                        try:
                            wa_scraper.send_reply(
                                task["group"], BOT_CLAIM_REPLY
                            )
                            self.log_msg(
                                f"Sent claim reply \"{BOT_CLAIM_REPLY}\" to "
                                f"'{task['group']}'."
                            )
                            time.sleep(2)
                            # Switch back to CRM tab for the upcoming
                            # navigate_to_transfer_tool() call.
                            try:
                                if (
                                    getattr(crm_system, "main_window", None)
                                    and crm_system.main_window
                                    in crm_system.driver.window_handles
                                ):
                                    crm_system.driver.switch_to.window(
                                        crm_system.main_window
                                    )
                            except Exception:
                                pass
                        except Exception as claim_err:
                            self.log_msg(
                                f"Could not send claim reply: {claim_err}. "
                                f"Proceeding with transfer anyway."
                            )

                    self._process_one_task(
                        task, crm_system, wa_scraper, wa_mode
                    )
                    # Mark as recently processed so subsequent monitoring
                    # scans don't re-queue the same transfer (the trigger
                    # message may still be visible in WhatsApp for several
                    # minutes after it's been processed).
                    recently_processed.append(
                        (task["store"], tuple(sorted(task["imeis"])), time.time())
                    )
                    # Do NOT re-add to still_pending — it's processed.

                pending_tasks = still_pending

                # ── Step 2: Scan for NEW transfer requests ──
                self.log_msg("\n--- Monitoring: checking for unread messages ---")
                if wa_scraper and wa_mode != "desktop":
                    try:
                        new_tasks = wa_scraper.find_and_read_groups(
                            self.mappings, trigger_words_str, only_unread=True
                        )
                    except Exception as scan_err:
                        self.log_msg(f"Monitoring scan error: {scan_err}")
                        new_tasks = []
                    self.log_msg(
                        f"Monitoring: {len(new_tasks)} new transfer request(s) found."
                    )

                    for t in new_tasks:
                        # Expire old entries from recently_processed
                        now_ts = time.time()
                        recently_processed = [
                            (s, im, ts) for s, im, ts in recently_processed
                            if now_ts - ts < RECENT_TTL
                        ]

                        # De-dup: skip if a pending OR recently-processed task
                        # for the same store + IMEIs already exists.
                        is_dup = any(
                            p["store"] == t["store"]
                            and p["imeis"] == t["imeis"]
                            for p in pending_tasks
                        )
                        if not is_dup:
                            # Also check recently_processed (covers tasks that
                            # were already processed or skipped in the last
                            # 5 minutes — prevents re-queuing the same transfer
                            # from subsequent monitoring scans while the
                            # trigger message is still visible in WhatsApp).
                            t_imeis = tuple(sorted(t["imeis"]))
                            is_dup = any(
                                s == t["store"] and im == t_imeis
                                for s, im, _ in recently_processed
                            )
                        if is_dup:
                            self.log_msg(
                                f"  Skipping duplicate request for "
                                f"{t['store']} (already pending or recently "
                                f"processed)."
                            )
                            continue
                        t["process_after"] = time.time() + WAIT_FOR_REPLY_SECONDS
                        t["detected_at"] = time.time()
                        pending_tasks.append(t)
                        recently_processed.append(
                            (t["store"], tuple(sorted(t["imeis"])), time.time())
                        )
                        self.log_msg(
                            f"  Queued: {t['store']} ({len(t['imeis'])} IMEIs) "
                            f"from '{t['group']}' — will process in "
                            f"{WAIT_FOR_REPLY_SECONDS}s unless someone replies."
                        )
                else:
                    self.log_msg("Monitoring skipped (Desktop mode or no scraper).")
                    break

            self.log_msg("Monitoring stopped.")

        except Exception as e:
            self.log_msg(f"Critical workflow error: {e}")
        finally:
            # Keep the browser open for manual inspection; it is closed
            # automatically at the start of the next run.
            if crm_system is not None and crm_system.driver is not None:
                self.retained_driver = crm_system.driver
            # FIX #3: Always clean up a WhatsApp-only browser if one existed
            if wa_scraper is not None:
                wa_scraper.close()
            self.log_msg(
                "Browser kept open (VidaPay + WhatsApp tabs) for manual "
                "inspection."
            )
            self.ui_state_stopped()


def _enable_dpi_awareness() -> None:
    """Make Windows report physical pixels so winfo_screen* is accurate on
    high-DPI displays (1080p, 1440p, 2K, 4K, DPI-scaled laptops).

    Also sets the AppUserModelID BEFORE any Tk window is created — this is
    critical for the taskbar to show our custom icon instead of the generic
    Python/PyInstaller icon.  Setting the AppUserModelID after the window is
    created has no effect on taskbar grouping."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Set AppUserModelID BEFORE any window is created
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


if __name__ == "__main__":
    # AppUserModelID MUST be set before VidaPayTransferApp() — that class
    # inherits tk.Tk so the window is created during instantiation.
    # Setting it inside __init__ is already too late; Windows ignores it.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VidaPay.TransferBot")
    except Exception:
        pass
    _enable_dpi_awareness()
    app = VidaPayTransferApp()
    app.after(10, lambda: app.state("zoomed"))
    app.mainloop()
