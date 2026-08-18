# VidaPay Automation Suite - Standalone Inventory Transfer Bot
# (No dependency on VidaPay_Device_Ordering_FULL.pyw)

import os
import re
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

# GFH brand assets embedded as base64 (injected at build time) so the bot
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
# running multiple GFH/VidaPay tools at once each gets its own Edge
# process/window instead of colliding on a shared profile+port and
# opening as tabs inside whichever tool launched first.
AUTOMATION_PROFILE_DIR = r"C:\VidaPay_Edge_Automation_Profile_TransferBot"
REMOTE_DEBUGGING_PORT = 9224
ATTACH_TO_OPEN_EDGE = True
PAGE_LOAD_TIMEOUT = 90

CRM_MAIN_PANEL_URL = "https://www.vidapaycrm.com/Main%20Panel.aspx"
CRM_LOGIN_URL = "https://www.vidapaycrm.com/Login.aspx"

# ----------------------------------------------------------------------------
# GFH THEME PALETTES (light / dark) - brand: deep navy + signal red
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


def _is_human_verification_page(driver):
    """Detect Cloudflare / reCAPTCHA / Turnstile pages."""
    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        markers = [
            "verify you are human", "verify human", "confirm you are human",
            "checking your browser", "security check", "cloudflare",
            "cf-turnstile", "turnstile", "i'm not a robot", "not a robot",
            "recaptcha",
        ]
        if any(m in body_text for m in markers):
            return True
        return bool(
            driver.execute_script(
                """
                function vis(el) {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display!=='none' && s.visibility!=='hidden'
                           && s.opacity!=='0' && r.width>0 && r.height>0
                           && el.getClientRects().length>0;
                }
                const bt = (document.body && document.body.innerText || '').toLowerCase();
                const hv = bt.includes('verify') || bt.includes('human')
                          || bt.includes('cloudflare') || bt.includes('robot');
                const cb = Array.from(document.querySelectorAll(
                    'input[type="checkbox"]')).some(vis);
                const ts = !!document.querySelector(
                    '[name="cf-turnstile-response"], .cf-turnstile, '
                    + 'iframe[src*="turnstile"], iframe[src*="cloudflare"]');
                const rc = !!document.querySelector(
                    'iframe[src*="recaptcha"], .g-recaptcha, '
                    + '#recaptcha-anchor, #rc-anchor-container');
                return (cb && hv) || ts || rc;
                """
            )
        )
    except Exception:
        return False


def _wait_for_human_verification_clear(driver, log, timeout=300):
    """Block until human verification page is gone or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_human_verification_page(driver):
            return True
        log("Human verification detected. Please complete it in the browser...")
        time.sleep(3)
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

    driver = EdgeDriver(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    _inject_anti_detection(driver)
    return driver



def create_logo_label(frame, width=100, height=50):
    """Create logo label with fallback to text"""
    try:
        from PIL import Image, ImageTk
        logo_files = ["GFH_Telecom_Logo.png", "logo.png", "gfh_logo.png"]
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
    return tk.Label(frame, text="GFH", font=("Segoe UI", 16, "bold"), fg=BRAND_RED, bg=BRAND_NAVY)


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
        try:
            btn = self.driver.find_element(By.ID, "btnNext")
            if btn.is_displayed():
                btn.click()
                return True
        except Exception:
            pass
        try:
            btns = self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            for b in btns:
                if b.is_displayed() and b.is_enabled():
                    b.click()
                    return True
        except Exception:
            pass
        return False

    def _click_trust_radio(self):
        try:
            for el in self.driver.find_elements(By.ID, "trustRadio"):
                if el.is_displayed():
                    el.click()
                    return True
        except Exception:
            pass
        return False

    def _click_setup_next(self, label_hint=""):
        try:
            btns = self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            for b in btns:
                txt = (b.get_attribute("value") or "").strip()
                if b.is_displayed() and b.is_enabled() and txt.lower() in ("next", "continue"):
                    b.click()
                    return True
        except Exception:
            pass
        return False

    def _click_ready_to_go_continue(self):
        try:
            btns = self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            for b in btns:
                val = (b.get_attribute("value") or "").strip().lower()
                if b.is_displayed() and b.is_enabled() and ("continue" in val or "ready" in val):
                    b.click()
                    return True
        except Exception:
            pass
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
                self.log("Human verification detected during sign-in. Waiting...")
                _wait_for_human_verification_clear(self.driver, self.log)
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

    def navigate_to_transfer_tool(self):
        self.log("Navigating to Inventory Reassignment Tool...")
        try:
            # WhatsApp tab may be the active one right now
            self._focus_main_window()
            self.driver.get(
                "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx"
            )
            self.wait.until(
                EC.presence_of_element_located(
                    (By.ID, "ctl00_MainContent_rcbAccount_Input")
                )
            )
            time.sleep(2)
            return True
        except Exception as e:
            self.log(f"Failed to navigate to Transfer Tool: {e}")
            return False

    def execute_transfer(self, target_account_id, imeis):
        if not imeis:
            self.log("No IMEIs to transfer. Skipping.")
            return False

        self.log(
            f"Initiating transfer to Account ID: {target_account_id} "
            f"for {len(imeis)} devices."
        )

        try:
            # 1. Enter Target Account ID
            account_input = self.wait.until(
                EC.element_to_be_clickable(
                    (By.ID, "ctl00_MainContent_rcbAccount_Input")
                )
            )
            account_input.clear()
            account_input.send_keys(target_account_id)
            time.sleep(1)
            account_input.send_keys(Keys.ENTER)
            time.sleep(3)

            # 2. Enter IMEIs
            sim_input = self.driver.find_element(By.ID, "MainContent_txtSimEntry")
            add_btn = self.driver.find_element(By.ID, "MainContent_btnAddSimEntry")

            for imei in imeis:
                if self.should_stop():
                    self.log("Process stopped by user.")
                    return False

                # FIX #5: Retry loop for StaleElementReferenceException
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
                        break
                    except StaleElementReferenceException:
                        self.log(
                            f"Stale element for IMEI {imei}, re-fetching..."
                        )
                        sim_input = self.driver.find_element(
                            By.ID, "MainContent_txtSimEntry"
                        )
                        add_btn = self.driver.find_element(
                            By.ID, "MainContent_btnAddSimEntry"
                        )
                        time.sleep(1)
                else:
                    self.log(f"Failed to add IMEI {imei} after retries.")
                    return False

            # 3. Proceed to Next
            next_btn = self.driver.find_element(By.ID, "MainContent_btnNext")
            self.driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(2)

            # 4. Submit Transfer
            submit_btn = self.wait.until(
                EC.element_to_be_clickable((By.ID, "MainContent_submitButton"))
            )
            self.driver.execute_script("arguments[0].click();", submit_btn)
            self.log(
                f"Transfer submitted successfully to {target_account_id}."
            )
            time.sleep(3)
            return True

        except Exception as e:
            self.log(f"Error during CRM transfer: {e}")
            return False


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

                // 2. Check for loading spinner (WhatsApp uses a specific SVG spinner)
                const spinners = document.querySelectorAll(
                    'svg[role="img"], div[role="progressbar"], '
                  + 'div[class*="spinner"], div[class*="loading"]'
                );
                for (const s of spinners) {
                    if (s.offsetWidth > 0 && s.offsetHeight > 0) return 'loading';
                }

                // 3. Check for the chat list / sidebar (evidence of being logged in)
                // WhatsApp renders chat items as divs with role="listitem" or
                // inside a scrollable panel.  Also look for the app header bar.
                const chatItems = document.querySelectorAll(
                    '[data-id], [role="listitem"]'
                );
                const header = document.querySelector(
                    'header, [class*="header"][class*="app"]'
                );
                // Must have at least 1 chat item AND a visible header
                if (chatItems.length > 0 && header
                    && header.offsetWidth > 0) {
                    return 'logged_in';
                }

                // 4. Fallback: any contenteditable div means the UI rendered
                const editables = document.querySelectorAll('[contenteditable="true"]');
                if (editables.length > 0) {
                    // No QR, no spinner, has editables => probably logged in
                    return 'logged_in';
                }

                return 'unknown';
            """)
        except Exception:
            return 'unknown'

    def _wa_find_search_box_js(self):
        """Use JavaScript to locate the search input.  Much more resilient
        than hardcoded XPath because it inspects the live DOM structure."""
        return self.driver.execute_script("""
            // Strategy 1: contenteditable inside a header or search panel
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

            // Strategy 2: ANY visible contenteditable div (broadest fallback)
            const all = document.querySelectorAll('[contenteditable="true"]');
            for (const el of all) {
                if (el.offsetWidth > 0 && el.offsetHeight > 0
                    && getComputedStyle(el).display !== 'none'
                    && getComputedStyle(el).visibility !== 'hidden'
                    && el.tagName === 'DIV') {
                    // Prefer the one that is closest to the top of the page
                    // (the search box is in the header, not the message composer)
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
        """Locate the search box (used by find_and_read_groups).
        Reuses the JS-based finder from start_whatsapp."""
        return self._wa_find_search_box_js()

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

    def find_and_read_groups(self, mappings):
        transfer_tasks = []

        # Make sure the WhatsApp tab is the active one before scraping
        self._focus_wa_window()

        for group_name in self.groups:
            if self.stop_event.is_set():
                break

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

                messages = self.driver.find_elements(
                    By.CSS_SELECTOR, "div.message-in"
                )
                self.log(
                    f"[{group_name}] Found {len(messages)} recent incoming messages."
                )

                for msg in messages:
                    if self.stop_event.is_set():
                        break

                    text_content = msg.text.lower()
                    if "transfer" in text_content:
                        target_account = None
                        target_store = None
                        for store_name, acc_id in mappings.items():
                            if store_name.lower() in text_content:
                                target_account = acc_id
                                target_store = store_name
                                break

                        if target_account:
                            self.log(
                                f"Transfer request detected in '{group_name}' "
                                f"for '{target_store}' -> Account: {target_account}"
                            )
                            img_path = os.path.join(
                                os.environ.get("TEMP", ""), "wa_msg_temp.png"
                            )
                            msg.screenshot(img_path)

                            imeis = self._extract_imeis_from_image(img_path)
                            if imeis:
                                self.log(
                                    f"Extracted {len(imeis)} IMEIs via OCR."
                                )
                                transfer_tasks.append({
                                    "group": group_name,
                                    "store": target_store,
                                    "account_id": target_account,
                                    "imeis": imeis,
                                })
                            else:
                                self.log(
                                    "No valid IMEIs found in the message image."
                                )

                search_box.send_keys(Keys.ESCAPE)
                time.sleep(1)

            except Exception as e:
                self.log(f"Error scanning group '{group_name}': {e}")

        return transfer_tasks

    def _extract_imeis_from_image(self, img_path):
        try:
            # FIX #6: Preprocess image for better OCR accuracy
            img = Image.open(img_path)
            img = img.convert("L")  # grayscale
            img = img.resize(
                (img.width * 2, img.height * 2), Image.Resampling.LANCZOS
            )

            # FIX #6: Use Tesseract config for better results
            custom_config = r"--oem 3 --psm 6"
            text = pytesseract.image_to_string(img, config=custom_config)

            # FIX #7: Also search stripped text for OCR output with spaces/dashes
            cleaned = re.sub(r"[\s\-\.]+", "", text)
            imeis_from_cleaned = re.findall(r"(?:35|01)\d{13}", cleaned)
            imeis_from_raw = re.findall(r"\b(?:35|01)\d{13}\b", text)
            all_imeis = list(set(imeis_from_cleaned + imeis_from_raw))
            return all_imeis
        except Exception as e:
            self.log(f"OCR Error: {e}")
            return []

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

        # GFH theme: 'system' follows Windows, otherwise explicit light/dark
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
        # ---- GFH header (FixedHeaderManager) ----
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
            _lp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GFH_Telecom_Logo.png")
            if not os.path.exists(_lp) and hasattr(sys, '_MEIPASS'):
                _lp = os.path.join(sys._MEIPASS, "GFH_Telecom_Logo.png")
            if os.path.exists(_lp):
                self.header_mgr.set_logo(logo_path=_lp, text="GFH")
        except Exception:
            pass
        # Add theme toggle
        self.header_mgr.add_theme_toggle(self.theme_manager, callback=self._on_header_theme_toggle)

        # Theme toggle — attach to the header frame created by FixedHeaderManager
        _header_frame = self.header_mgr.header_frame if hasattr(self.header_mgr, 'header_frame') else self
        theme_box = tk.Frame(_header_frame, bg=self.colors["navy"])
        theme_box.pack(side=tk.RIGHT)
        theme_box._tag = "header"
        theme_lbl = tk.Label(
            theme_box,
            text="Theme",
            font=("Segoe UI", 9, "bold"),
            fg="#c8cdf0",
            bg=self.colors["navy"],
        )
        theme_lbl.pack(side=tk.LEFT, padx=(0, 8))
        theme_lbl._tag = "header_label"
        # One-click toggle between light and dark, instead of a dropdown.
        self.theme_btn = ttk.Button(
            theme_box,
            text="",
            width=12,
            command=self._toggle,
        )
        self.theme_btn.pack(side=tk.LEFT)
        self._update_theme_btn()

        # ---- Body ----
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

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

        bottom_frame = tk.Frame(self, bg=self.colors["bg"])
        bottom_frame.pack(fill=tk.X, padx=20, pady=10)

        ctrl_frame = tk.Frame(bottom_frame, bg=self.colors["bg"])
        ctrl_frame.pack(fill=tk.X, pady=5)

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

        self.log_area = scrolledtext.ScrolledText(
            bottom_frame,
            height=10,
            font=("Consolas", 9),
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground=self.colors["log_fg"],
        )
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=5)

        self.progress = ttk.Progressbar(
            bottom_frame, mode="indeterminate"
        )
        self.progress.pack(fill=tk.X)

        # Copyright footer
        _cbar = tk.Frame(self, bg="#090d26", height=24)
        _cbar.pack(fill=tk.X, side="bottom")
        _cbar.pack_propagate(False)
        tk.Label(_cbar, text=f"Developed by Abad Umair Channa | Copyright © {date.today().year} | All rights reserved.",
                 font=("Segoe UI", 8), fg="#9d9db8", bg="#090d26").pack(side="left", padx=14, pady=3)

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

        tk.Label(bot_frame, text="Schedule (HH:MM):").grid(
            row=3, column=0, padx=10, pady=5, sticky=tk.W
        )
        self.entry_schedule = ttk.Entry(bot_frame, width=25)
        self.entry_schedule.insert(0, self.schedule_times)
        self.entry_schedule.grid(
            row=3, column=1, padx=10, pady=5, sticky=tk.W
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
        self.btn_save.grid(row=4, column=0, columnspan=2, pady=6)

        map_frame = ttk.LabelFrame(
            self.tab_config, text="Store Name to Account ID Mappings"
        )
        map_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        entry_frame = tk.Frame(map_frame)
        entry_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(entry_frame, text="Store Name:").pack(
            side=tk.LEFT, padx=5
        )
        self.entry_map_store = ttk.Entry(entry_frame, width=25)
        self.entry_map_store.pack(side=tk.LEFT, padx=5)

        tk.Label(entry_frame, text="Account ID:").pack(
            side=tk.LEFT, padx=5
        )
        self.entry_map_acc = ttk.Entry(entry_frame, width=20)
        self.entry_map_acc.pack(side=tk.LEFT, padx=5)

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

        cols = ("Store Name", "Account ID")
        self.tree_map = ttk.Treeview(
            map_frame, columns=cols, show="headings", height=8
        )
        for c in cols:
            self.tree_map.heading(c, text=c)
            self.tree_map.column(c, width=200)
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

                if (
                    store
                    and acc
                    and store.lower() != "nan"
                    and acc.lower() != "nan"
                ):
                    self.mappings[store] = acc
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
        if store and acc:
            self.mappings[store] = acc
            self.config_data["transfer_mappings"] = self.mappings
            save_config(self.config_data)
            self.refresh_mappings_tree()
            self.entry_map_store.delete(0, tk.END)
            self.entry_map_acc.delete(0, tk.END)

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
        for store, acc in self.mappings.items():
            self.tree_map.insert("", tk.END, values=(store, acc))

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
                self.log_area.insert(tk.END, msg)
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
    # GFH Branding: logo, icons, themes
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
        """Load the real GFH logo + window icon (embedded), else render fallbacks."""
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
        # Header logo: embedded real GFH logo first, rendered badge as fallback
        logo_path = self._extract_embedded(EMBEDDED_LOGO_B64, "gfh_logo_real.png")
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
        """Render a red 'GFH' badge logo and save it next to the config."""
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
            text = "GFH"
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
                text,
                font=font,
                fill="#ffffff",
            )
            path = os.path.join(APP_DATA_DIR, "gfh_logo.png")
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
        threading.Thread(
            target=self._bot_workflow,
            args=(acc, usr, pwd, wa_groups_str, wa_mode),
            daemon=True,
        ).start()

    def stop_bot(self):
        self.log_msg("Sending stop signal... Please wait.")
        self.stop_event.set()

    def _bot_workflow(self, crm_acc, crm_usr, crm_pwd, wa_groups_str, wa_mode="web"):
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
                if not wa_scraper.start_whatsapp(
                    shared_driver=crm_system.driver
                ):
                    self.log_msg(
                        "Failed to open WhatsApp Web in the shared browser. "
                        "Aborting run."
                    )
                    return

            tasks = wa_scraper.find_and_read_groups(self.mappings)
            self.log_msg(
                f"Extraction complete: {len(tasks)} transfer request(s) found."
            )

            if not tasks:
                self.log_msg(
                    "No pending transfer requests found across any monitored group."
                )
                return

            # 3. Switch back to the VidaPay tab and run the transfers
            self.log_msg(
                "Switching back to the VidaPay tab to run the transfers..."
            )
            if not crm_system.navigate_to_transfer_tool():
                self.log_msg("Could not open Reassignment Tool. Aborting.")
                return

            for task in tasks:
                if self.stop_event.is_set():
                    break
                success = crm_system.execute_transfer(
                    task["account_id"], task["imeis"]
                )
                status = "SUCCESS" if success else "FAILED"
                self.append_history(
                    task["store"],
                    task["account_id"],
                    len(task["imeis"]),
                    status,
                )

            self.log_msg("=== Workflow Complete ===")

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
    app.mainloop()
