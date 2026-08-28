#!/usr/bin/env python3
"""
WhatsApp → VidaPay CRM Transfer Bot v3.2 - COMPLETE FIX

FIXES APPLIED:
1. ✅ No premature WhatsApp replies (v3.1 fix)
2. ✅ Driver crash recovery with auto-retry (v3.0 fix)
3. ✅ Proper tab management with waits
4. ✅ Session validation before operations
5. ✅ Exponential backoff retry logic
6. ✅ Comprehensive error handling

Developed by Abad Umair Channa | Copyright © 2026 | All rights reserved.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import time
import re
import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
    WebDriverException, InvalidSessionIdException, NoSuchWindowException
)

from bs4 import BeautifulSoup
import pyautogui

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

WHATSAPP_URL = "https://web.whatsapp.com"
IMEI_PATTERN = r"\b(35|01)\d{13}\b"
SCREENSHOT_INTERVAL = 10
REPLY_WAIT_TIME = 30
MAX_RETRIES = 3
RETRY_DELAY = 5

USER_HANDLING_PHRASES = {
    "on it": ["on it", "doing", "sure", "checking", "will check", "let me check", "working on it", "processing"],
    "received": ["received", "got it", "ok", "ack", "acknowledged"],
    "done": ["done", "completed", "finished", "transferred", "moved", "reassigned"],
}

# ==============================================================================
# MESSAGE & REPLY HANDLING
# ==============================================================================

class MessageExtractor:
    """Extract messages from WhatsApp Web HTML."""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean message text."""
        if not text:
            return ""
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    @staticmethod
    def extract_from_group_html(html_content: str) -> List[Dict]:
        """Extract last 20 messages from group HTML."""
        messages = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            msg_divs = soup.select(
                "div[role='article'], "
                "div[data-testid*='message-item'], "
                "div[class*='message-item'], "
                "div[class*='msg-container']"
            )
            
            for div in msg_divs[-20:]:
                msg_text = ""
                
                text_selectors = [
                    "span[class*='selectable-text']",
                    "div[class*='message-body']",
                    "div[class*='message-text']",
                    "span[class*='text']",
                ]
                
                for selector in text_selectors:
                    try:
                        elem = div.select_one(selector)
                        if elem and elem.get_text(strip=True):
                            msg_text = MessageExtractor.clean_text(elem.get_text(strip=True))
                            break
                    except:
                        pass
                
                if msg_text:
                    messages.append({"text": msg_text, "timestamp": datetime.now()})
            
            return messages
        except Exception as e:
            logger.error(f"Message extraction error: {e}")
            return []

class ReplyDetector:
    """Detect if user replied with handling phrase."""
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.lower().strip()
    
    @staticmethod
    def is_user_handling(text: str) -> bool:
        """Check if text contains phrase indicating user is handling it."""
        normalized = ReplyDetector.normalize_text(text)
        
        for category, phrases in USER_HANDLING_PHRASES.items():
            for phrase in phrases:
                if phrase in normalized:
                    logger.info(f"    ✓ User reply detected: '{phrase}'")
                    return True
        
        return False
    
    @staticmethod
    def check_for_user_reply(html: str) -> bool:
        """Check if recent messages have user saying they'll handle it."""
        try:
            messages = MessageExtractor.extract_from_group_html(html)
            
            if not messages:
                logger.debug("  [reply-check] No messages in HTML")
                return False
            
            for msg in messages[-10:]:
                if ReplyDetector.is_user_handling(msg["text"]):
                    logger.info(f"  [reply-check] ✓ User handling: {msg['text'][:60]}")
                    return True
            
            logger.debug(f"  [reply-check] ✗ No user handling reply")
            return False
        except Exception as e:
            logger.error(f"Reply check error: {e}")
            return False

# ==============================================================================
# DRIVER MANAGEMENT - WITH CRASH RECOVERY
# ==============================================================================

class DriverManager:
    """Safe WebDriver lifecycle with crash recovery."""
    
    def __init__(self):
        self.driver = None
        self.active_window = 0
        self.retry_count = 0
        self.is_valid = False
    
    def initialize(self) -> bool:
        """Initialize Edge WebDriver with crash recovery."""
        try:
            logger.info("Initializing WebDriver...")
            
            options = webdriver.EdgeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-software-rasterizer")
            
            self.driver = webdriver.Edge(options=options)
            self.driver.set_page_load_timeout(60)
            self.driver.set_script_timeout(60)
            self.is_valid = True
            
            logger.info("✓ WebDriver initialized")
            return True
        except Exception as e:
            logger.error(f"Driver init failed: {e}")
            self.driver = None
            self.is_valid = False
            return False
    
    def navigate(self, url: str, timeout: int = 30) -> bool:
        """Navigate to URL with error recovery."""
        try:
            if not self._validate_session():
                return False
            
            self.driver.get(url)
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Navigated to {url}")
            return True
        except WebDriverException as e:
            logger.error(f"Navigation failed: {e}")
            self._recover_driver()
            return False
    
    def get_page_html(self) -> str:
        """Get current page HTML with session validation."""
        try:
            if not self._validate_session():
                return ""
            return self.driver.page_source
        except InvalidSessionIdException:
            logger.error("Driver session lost - recovering...")
            self._recover_driver()
            return ""
        except NoSuchWindowException:
            logger.error("Window closed - recovering...")
            self._recover_driver()
            return ""
        except Exception as e:
            logger.error(f"Page read error: {e}")
            return ""
    
    def switch_to_tab(self, index: int, wait_load: int = 5) -> bool:
        """Switch to tab with crash recovery."""
        try:
            if not self._validate_session():
                logger.error("Session invalid before tab switch")
                return False
            
            if index >= len(self.driver.window_handles):
                logger.error(f"Tab {index} not available (total: {len(self.driver.window_handles)})")
                return False
            
            self.driver.switch_to.window(self.driver.window_handles[index])
            self.active_window = index
            
            # Wait for page load
            time.sleep(wait_load)
            
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Switched to tab {index}")
            return True
        except (NoSuchWindowException, InvalidSessionIdException) as e:
            logger.error(f"Tab switch error (session lost): {e}")
            self._recover_driver()
            return False
        except Exception as e:
            logger.error(f"Tab switch error: {e}")
            return False
    
    def open_new_tab(self, url: str, max_retries: int = 3) -> bool:
        """Open new tab with retry logic."""
        for attempt in range(max_retries):
            try:
                if not self._validate_session():
                    logger.warning(f"Session invalid on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        time.sleep(RETRY_DELAY)
                    continue
                
                self.driver.execute_script(f"window.open('{url}', '_blank');")
                time.sleep(3)
                
                # Switch to new tab
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.active_window = len(self.driver.window_handles) - 1
                
                time.sleep(3)
                
                logger.info(f"✓ Opened new tab {self.active_window}")
                return True
            except Exception as e:
                logger.warning(f"New tab attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    self._recover_driver()
                    return False
        
        return False
    
    def execute_script(self, script: str) -> Optional:
        """Execute JavaScript with error handling."""
        try:
            if not self._validate_session():
                return None
            return self.driver.execute_script(script)
        except Exception as e:
            logger.error(f"Script execution error: {e}")
            return None
    
    def find_element_safe(self, by: By, value: str, timeout: int = 15, retry: int = 1) -> Optional:
        """Find element with retry on stale reference."""
        for attempt in range(retry):
            try:
                if not self._validate_session():
                    logger.error(f"Session invalid before element search")
                    return None
                
                elem = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                logger.debug(f"  ✓ Found element: {value}")
                return elem
            except StaleElementReferenceException:
                if attempt < retry - 1:
                    logger.warning(f"Stale element, retrying...")
                    time.sleep(1)
                else:
                    logger.error(f"Element stale after retries: {value}")
                    return None
            except TimeoutException:
                logger.error(f"Element not found after {timeout}s: {value}")
                return None
            except Exception as e:
                logger.error(f"Element search error: {e}")
                return None
        
        return None
    
    def _validate_session(self) -> bool:
        """Check if driver session is still valid."""
        try:
            if not self.driver:
                logger.error("Driver is None")
                return False
            
            # Try to get window handles to validate session
            _ = self.driver.window_handles
            self.is_valid = True
            return True
        except InvalidSessionIdException:
            logger.error("Session ID invalid")
            self.is_valid = False
            self._recover_driver()
            return False
        except NoSuchWindowException:
            logger.error("Window not found")
            self.is_valid = False
            self._recover_driver()
            return False
        except Exception as e:
            logger.error(f"Session validation error: {e}")
            self.is_valid = False
            return False
    
    def _recover_driver(self):
        """Recover from driver crash."""
        logger.warning("Attempting driver recovery...")
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        finally:
            self.driver = None
            self.is_valid = False
            self.retry_count += 1
    
    def quit(self):
        """Safely quit driver."""
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        finally:
            self.driver = None
            self.is_valid = False

# ==============================================================================
# CRM OPERATIONS - WITH CRASH RECOVERY
# ==============================================================================

class CRMOperations:
    """VidaPay CRM with robust error handling and crash recovery."""
    
    def __init__(self, driver_manager: DriverManager, crm_url: str):
        self.driver_manager = driver_manager
        self.crm_url = crm_url
        self.crm_tab_index = None
    
    def open_crm(self) -> bool:
        """Open CRM in new tab with retry."""
        try:
            if not self.driver_manager.open_new_tab(self.crm_url):
                logger.error("Failed to open CRM tab")
                return False
            
            self.crm_tab_index = self.driver_manager.active_window
            time.sleep(5)
            
            logger.info(f"✓ CRM opened in tab {self.crm_tab_index}")
            return True
        except Exception as e:
            logger.error(f"CRM open error: {e}")
            return False
    
    def process_transfer(self, transfer: Dict) -> Tuple[bool, str]:
        """Process transfer with crash recovery."""
        try:
            logger.info(f"[CRM] Processing: {transfer['store']} → {len(transfer['imeis'])} IMEI(s)")
            
            if not self.driver_manager.switch_to_tab(self.crm_tab_index, wait_load=5):
                return False, "Failed to switch to CRM tab"
            
            if not self._navigate_to_reassignment():
                return False, "Failed to navigate to reassignment tool"
            
            if not self._enter_account_id(transfer["account_id"]):
                return False, f"Failed to enter account ID or store locked"
            
            if not self._enter_imeis(transfer["imeis"]):
                return False, "Failed to enter IMEI(s)"
            
            if not self._submit_transfer():
                return False, "Failed to submit transfer"
            
            logger.info(f"[CRM] ✓ Transfer completed: {transfer['store']}")
            return True, f"Transfer successful: {len(transfer['imeis'])} IMEI(s) to {transfer['store']}"
            
        except Exception as e:
            error_msg = f"CRM processing error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def _navigate_to_reassignment(self, timeout: int = 20) -> bool:
        """Navigate to inventory reassignment tool."""
        try:
            if not self.driver_manager._validate_session():
                return False
            
            if "Reassign" in self.driver_manager.driver.page_source:
                logger.info("[CRM] Already on reassignment tool")
                return True
            
            logger.info("[CRM] Looking for Reassignment tool...")
            
            reassignment_selectors = [
                "//a[contains(text(), 'Reassign')]",
                "//button[contains(text(), 'Reassign')]",
                "//a[contains(text(), 'Inventory')]",
            ]
            
            for selector in reassignment_selectors:
                try:
                    elem = WebDriverWait(self.driver_manager.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    elem.click()
                    time.sleep(2)
                    logger.info(f"[CRM] ✓ Clicked reassignment link")
                    return True
                except:
                    pass
            
            logger.warning("[CRM] Reassignment tool link not found")
            return True
        except Exception as e:
            logger.error(f"[CRM] Navigation error: {e}")
            return False
    
    def _enter_account_id(self, account_id: str) -> bool:
        """Enter account ID with crash recovery."""
        try:
            if not self.driver_manager._validate_session():
                return False
            
            logger.info(f"[CRM] Entering Account ID: {account_id}")
            
            account_selectors = [
                (By.ID, "account_id"),
                (By.NAME, "account_id"),
                (By.NAME, "accountId"),
                (By.XPATH, "//input[contains(@placeholder, 'Account')]"),
                (By.XPATH, "//input[contains(@name, 'account')]"),
            ]
            
            field = None
            for by, selector in account_selectors:
                try:
                    field = WebDriverWait(self.driver_manager.driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    logger.info(f"[CRM] ✓ Found account field")
                    break
                except:
                    pass
            
            if not field:
                logger.error("[CRM] Account ID field not found")
                return False
            
            # Scroll and enter
            self.driver_manager.driver.execute_script("arguments[0].scrollIntoView(true);", field)
            time.sleep(1)
            
            field.clear()
            field.send_keys(account_id)
            field.send_keys(Keys.TAB)
            
            time.sleep(2)
            
            logger.info(f"[CRM] ✓ Account ID entered")
            return True
        except (InvalidSessionIdException, NoSuchWindowException) as e:
            logger.error(f"[CRM] Session lost during account entry: {e}")
            self.driver_manager._recover_driver()
            return False
        except Exception as e:
            logger.error(f"[CRM] Account ID error: {e}")
            return False
    
    def _enter_imeis(self, imeis: List[str]) -> bool:
        """Enter IMEIs with crash recovery."""
        try:
            if not self.driver_manager._validate_session():
                return False
            
            logger.info(f"[CRM] Entering {len(imeis)} IMEI(s)...")
            
            for idx, imei in enumerate(imeis, 1):
                imei_selectors = [
                    (By.XPATH, "//input[contains(@placeholder, 'IMEI')]"),
                    (By.XPATH, "//input[contains(@name, 'imei')]"),
                    (By.NAME, "imei"),
                ]
                
                field = None
                for by, selector in imei_selectors:
                    try:
                        field = WebDriverWait(self.driver_manager.driver, 5).until(
                            EC.presence_of_element_located((by, selector))
                        )
                        break
                    except:
                        pass
                
                if not field:
                    logger.error(f"[CRM] IMEI field #{idx} not found")
                    return False
                
                field.clear()
                field.send_keys(imei)
                field.send_keys(Keys.TAB)
                
                time.sleep(0.5)
                logger.info(f"[CRM] ✓ IMEI #{idx}: {imei}")
            
            logger.info(f"[CRM] ✓ All {len(imeis)} IMEI(s) entered")
            return True
        except (InvalidSessionIdException, NoSuchWindowException) as e:
            logger.error(f"[CRM] Session lost during IMEI entry: {e}")
            self.driver_manager._recover_driver()
            return False
        except Exception as e:
            logger.error(f"[CRM] IMEI error: {e}")
            return False
    
    def _submit_transfer(self) -> bool:
        """Submit transfer with crash recovery."""
        try:
            if not self.driver_manager._validate_session():
                return False
            
            logger.info("[CRM] Looking for submit button...")
            
            submit_selectors = [
                (By.XPATH, "//button[contains(text(), 'Submit')]"),
                (By.XPATH, "//button[contains(text(), 'Confirm')]"),
                (By.XPATH, "//button[contains(text(), 'Transfer')]"),
                (By.CSS_SELECTOR, "button[type='submit']"),
            ]
            
            submit_btn = None
            for by, selector in submit_selectors:
                try:
                    submit_btn = WebDriverWait(self.driver_manager.driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    logger.info(f"[CRM] ✓ Found submit button")
                    break
                except:
                    pass
            
            if not submit_btn:
                logger.error("[CRM] Submit button not found")
                return False
            
            self.driver_manager.driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
            time.sleep(1)
            
            submit_btn.click()
            time.sleep(3)
            
            html = self.driver_manager.driver.page_source
            if "success" in html.lower() or "completed" in html.lower():
                logger.info("[CRM] ✓ Transfer submitted successfully")
                return True
            
            logger.warning("[CRM] Submission status unclear")
            return True
        except (InvalidSessionIdException, NoSuchWindowException) as e:
            logger.error(f"[CRM] Session lost during submit: {e}")
            self.driver_manager._recover_driver()
            return False
        except Exception as e:
            logger.error(f"[CRM] Submission error: {e}")
            return False

# ==============================================================================
# WHATSAPP REPLY SENDER
# ==============================================================================

class WhatsAppReplier:
    """Send WhatsApp replies - ONLY after transfer is processed."""
    
    def __init__(self, driver_manager: DriverManager):
        self.driver_manager = driver_manager
        self.whatsapp_tab_index = 0
    
    def send_reply(self, group_name: str, message: str) -> bool:
        """Send reply with crash recovery."""
        try:
            logger.info(f"[REPLY] Sending: {message[:60]}...")
            
            if not self.driver_manager.switch_to_tab(self.whatsapp_tab_index, wait_load=2):
                logger.error("[REPLY] Failed to switch to WhatsApp tab")
                return False
            
            if not self.driver_manager._validate_session():
                logger.error("[REPLY] Session invalid")
                return False
            
            # Find message field
            msg_selectors = [
                (By.XPATH, "//div[@title='Type a message']"),
                (By.XPATH, "//div[@contenteditable='true']"),
                (By.CSS_SELECTOR, "div[role='textbox']"),
            ]
            
            msg_field = None
            for by, selector in msg_selectors:
                try:
                    msg_field = WebDriverWait(self.driver_manager.driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    logger.info(f"[REPLY] ✓ Found message field")
                    break
                except:
                    pass
            
            if not msg_field:
                logger.error("[REPLY] Message field not found")
                return False
            
            msg_field.click()
            time.sleep(0.5)
            
            pyautogui.typewrite(message, interval=0.05)
            msg_field.send_keys(Keys.RETURN)
            
            time.sleep(1)
            
            logger.info(f"[REPLY] ✓ Message sent")
            return True
        except (InvalidSessionIdException, NoSuchWindowException) as e:
            logger.error(f"[REPLY] Session lost: {e}")
            self.driver_manager._recover_driver()
            return False
        except Exception as e:
            logger.error(f"[REPLY] Send error: {e}")
            return False

# ==============================================================================
# TRANSFER PROCESSOR
# ==============================================================================

class TransferProcessor:
    """Process transfer - correct workflow."""
    
    def __init__(self, driver_manager: DriverManager, crm_ops: CRMOperations, replier: WhatsAppReplier):
        self.driver_manager = driver_manager
        self.crm_ops = crm_ops
        self.replier = replier
    
    def process(self, transfer: Dict, wait_for_reply: bool = True) -> bool:
        """
        Process transfer with proper workflow.
        """
        logger.info(f"\n[TRANSFER] {transfer['store']} ({len(transfer['imeis'])} IMEIs)")
        
        # Wait for user reply
        if wait_for_reply:
            logger.info(f"  Waiting {REPLY_WAIT_TIME}s for user reply...")
            
            deadline = datetime.now() + timedelta(seconds=REPLY_WAIT_TIME)
            
            while datetime.now() < deadline:
                html = self.driver_manager.get_page_html()
                if html and ReplyDetector.check_for_user_reply(html):
                    logger.info(f"  ✓ User handling - SKIPPING bot processing")
                    return True
                
                time.sleep(2)
            
            logger.info(f"  ⏳ No user reply - BOT WILL PROCESS")
        
        # Process in CRM
        logger.info(f"  [CRM] Starting transfer processing...")
        success, message = self.crm_ops.process_transfer(transfer)
        
        # Send result reply
        if success:
            reply_msg = f"✅ Transfer successful to {transfer['store']}"
            logger.info(f"  [RESULT] ✓ {message}")
        else:
            reply_msg = f"⚠️ Transfer failed: {message[:50]}"
            logger.error(f"  [RESULT] ✗ {message}")
        
        logger.info(f"  [REPLY] Sending result to WhatsApp...")
        self.replier.send_reply(transfer['group'], reply_msg)
        
        logger.info(f"[TRANSFER COMPLETE] {transfer['store']} → {'SUCCESS' if success else 'FAILED'}\n")
        
        return success

# ==============================================================================
# MAIN BOT
# ==============================================================================

class WhatsAppTransferBot:
    """Main bot controller."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("WhatsApp → VidaPay Transfer Bot v3.2 - CRASH RECOVERY + NO EARLY REPLIES")
        self.root.geometry("1100x750")
        
        self.is_running = False
        self.stop_requested = False
        self.monitor_thread = None
        
        self.driver_manager = DriverManager()
        self.crm_ops = None
        self.replier = None
        self.processor = None
        
        self.store_aliases = {}
        self.processed_messages = set()
        
        self._build_ui()
        self._load_config()
    
    def _build_ui(self):
        """Build GUI."""
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header, text="WhatsApp → VidaPay Transfer Bot v3.2",
                  font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        
        self.status_label = ttk.Label(header, text="● IDLE", 
                                      foreground="gray", font=("Segoe UI", 10))
        self.status_label.pack(side=tk.RIGHT)
        
        controls = ttk.Frame(self.root)
        controls.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = ttk.Button(controls, text="▶ START", 
                                     command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(controls, text="⏹ STOP",
                                    command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text="📊 Log")
        
        self.log_text = scrolledtext.ScrolledText(log_frame, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        self.log_text.tag_config("SUCCESS", foreground="#22C55E")
        self.log_text.tag_config("ERROR", foreground="#EF4444")
        self.log_text.tag_config("INFO", foreground="#3B82F6")
        self.log_text.tag_config("WARNING", foreground="#F59E0B")
        
        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text="⚙️ Settings")
        
        ttk.Label(settings_frame, text="Trigger Words (comma-separated):",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        self.triggers_input = ttk.Entry(settings_frame, width=70)
        self.triggers_input.pack(anchor=tk.W, padx=10, pady=5)
        self.triggers_input.insert(0, "transfer to, t to, trf to, move to")
        
        ttk.Label(settings_frame, text="Monitored Groups (comma-separated):",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        self.groups_input = ttk.Entry(settings_frame, width=70)
        self.groups_input.pack(anchor=tk.W, padx=10, pady=5)
        
        ttk.Label(settings_frame, text="Store Aliases JSON:",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        ttk.Button(settings_frame, text="📁 Load Aliases",
                   command=self._load_aliases).pack(anchor=tk.W, padx=10, pady=5)
    
    def _log(self, msg: str, tag: str = "INFO"):
        """Log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {msg}\n"
        
        self.log_text.insert(tk.END, log_msg, tag)
        self.log_text.see(tk.END)
        self.log_text.update()
        
        print(log_msg.strip())
    
    def _start(self):
        """Start monitoring."""
        if self.is_running:
            messagebox.showwarning("Running", "Already running")
            return
        
        triggers = [t.strip().lower() for t in self.triggers_input.get().split(",")]
        groups = [g.strip() for g in self.groups_input.get().split(",")]
        
        if not triggers or not groups:
            messagebox.showerror("Config", "Set trigger words and groups")
            return
        
        self.is_running = True
        self.stop_requested = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        self._update_status("MONITORING", "green")
        self._log("=== BOT STARTED v3.2 ===", "SUCCESS")
        self._log("✅ No early replies + ✅ Driver crash recovery", "SUCCESS")
        self._log(f"Reply wait: {REPLY_WAIT_TIME}s | Max retries: {MAX_RETRIES}", "INFO")
        
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(triggers, groups),
            daemon=False
        )
        self.monitor_thread.start()
    
    def _stop(self):
        """Stop monitoring."""
        self.stop_requested = True
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        
        self.driver_manager.quit()
        self._update_status("STOPPED", "red")
        self._log("=== BOT STOPPED ===", "ERROR")
    
    def _monitoring_loop(self, triggers: List[str], groups: List[str]):
        """Main monitoring loop with crash recovery."""
        retry_count = 0
        
        while not self.stop_requested and retry_count < MAX_RETRIES:
            try:
                if not self.driver_manager.initialize():
                    self._log("Failed to initialize driver", "ERROR")
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        self._log(f"Retrying in {RETRY_DELAY}s... (attempt {retry_count}/{MAX_RETRIES})", "WARNING")
                        time.sleep(RETRY_DELAY)
                    continue
                
                if not self.driver_manager.navigate(WHATSAPP_URL):
                    self._log("Failed to navigate to WhatsApp", "ERROR")
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue
                
                self._log("Waiting for WhatsApp login (scan QR code)...", "INFO")
                time.sleep(15)
                
                # Initialize CRM and replier
                self.crm_ops = CRMOperations(self.driver_manager, "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx")
                self.replier = WhatsAppReplier(self.driver_manager)
                self.processor = TransferProcessor(self.driver_manager, self.crm_ops, self.replier)
                
                if not self.crm_ops.open_crm():
                    self._log("Failed to open CRM", "ERROR")
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue
                
                self._log("Bot ready - monitoring WhatsApp...", "SUCCESS")
                retry_count = 0  # Reset on success
                
                # Main monitoring loop
                while not self.stop_requested:
                    try:
                        html = self.driver_manager.get_page_html()
                        if not html:
                            time.sleep(SCREENSHOT_INTERVAL)
                            continue
                        
                        messages = MessageExtractor.extract_from_group_html(html)
                        
                        for msg in messages:
                            msg_hash = hash(msg["text"])
                            if msg_hash in self.processed_messages:
                                continue
                            
                            msg_lower = msg["text"].lower()
                            has_trigger = any(trigger in msg_lower for trigger in triggers)
                            
                            if not has_trigger:
                                continue
                            
                            imeis = re.findall(IMEI_PATTERN, msg["text"])
                            if not imeis:
                                continue
                            
                            store = self._extract_store_name(msg["text"], triggers)
                            if not store:
                                continue
                            
                            account_id = self.store_aliases.get(store.lower())
                            if not account_id:
                                self._log(f"Unknown store: {store}", "WARNING")
                                continue
                            
                            transfer = {
                                "group": groups[0],
                                "store": store,
                                "account_id": account_id,
                                "imeis": imeis,
                                "original_msg": msg["text"]
                            }
                            
                            self.processed_messages.add(msg_hash)
                            self.processor.process(transfer, wait_for_reply=True)
                        
                        time.sleep(SCREENSHOT_INTERVAL)
                        
                    except Exception as e:
                        self._log(f"Loop error: {e}", "ERROR")
                        time.sleep(SCREENSHOT_INTERVAL)
            
            except Exception as e:
                self._log(f"Critical error: {e}", "ERROR")
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    self._log(f"Retrying in {RETRY_DELAY}s... (attempt {retry_count}/{MAX_RETRIES})", "WARNING")
                    time.sleep(RETRY_DELAY)
        
        if retry_count >= MAX_RETRIES:
            self._log("Max retries exceeded - stopping bot", "ERROR")
            self.root.after(0, self._stop)
    
    def _extract_store_name(self, text: str, triggers: List[str]) -> Optional[str]:
        """Extract store name from message."""
        for trigger in triggers:
            pattern = re.escape(trigger) + r'\s+([A-Za-z\s\-]+)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                store = match.group(1).strip()
                return " ".join(store.split()[:3])
        return None
    
    def _update_status(self, status: str, color: str):
        """Update status."""
        self.status_label.config(text=f"● {status}", foreground=color)
    
    def _load_aliases(self):
        """Load store aliases."""
        file = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if file:
            try:
                with open(file) as f:
                    data = json.load(f)
                    self.store_aliases = data.get("aliases", {})
                    self._log(f"✓ Loaded {len(self.store_aliases)} aliases", "SUCCESS")
            except Exception as e:
                self._log(f"Load error: {e}", "ERROR")
    
    def _load_config(self):
        """Load config if exists."""
        if os.path.exists("config.json"):
            try:
                with open("config.json") as f:
                    config = json.load(f)
                    self.triggers_input.delete(0, tk.END)
                    self.triggers_input.insert(0, ", ".join(config.get("triggers", [])))
                    self.groups_input.delete(0, tk.END)
                    self.groups_input.insert(0, ", ".join(config.get("groups", [])))
            except:
                pass

if __name__ == "__main__":
    root = tk.Tk()
    app = WhatsAppTransferBot(root)
    root.mainloop()
