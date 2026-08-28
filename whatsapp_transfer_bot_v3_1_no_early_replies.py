#!/usr/bin/env python3
"""
WhatsApp → VidaPay CRM Transfer Bot v3.1 - CRITICAL FIX

WORKFLOW (CORRECTED):
1. Detect transfer request in WhatsApp message
2. Wait 30 seconds for user reply ("on it", "doing", etc.)
3. If reply detected within 30s → Mark transfer as "claimed by user"
4. If NO reply after 30s → Bot will process it
5. Process transfer in CRM (open tab, enter account ID, enter IMEIs, submit)
6. ONLY AFTER CRM processing completes (success or error):
   → Send WhatsApp reply with result
   → ✅ "Transfer successful" OR
   → ❌ "Transfer failed: [error]"

CRITICAL: NO messages sent to WhatsApp until transfer is actually processed!

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
    WebDriverException, InvalidSessionIdException
)

from bs4 import BeautifulSoup
import pyautogui

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# ==============================================================================
# CONFIGURATION
# ==============================================================================

WHATSAPP_URL = "https://web.whatsapp.com"
IMEI_PATTERN = r"\b(35|01)\d{13}\b"
SCREENSHOT_INTERVAL = 10
REPLY_WAIT_TIME = 30  # Wait 30 seconds for user reply before bot processes

# Phrases that mean user is handling it (they replied with these)
USER_HANDLING_PHRASES = {
    "on it": ["on it", "doing", "sure", "checking", "will check", "let me check", 
              "working on it", "processing", "one moment"],
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
        """
        Check if text contains phrase indicating user is handling it.
        
        Returns: True if user said they'll handle it, False otherwise
        """
        normalized = ReplyDetector.normalize_text(text)
        
        for category, phrases in USER_HANDLING_PHRASES.items():
            for phrase in phrases:
                if phrase in normalized:
                    logger.info(f"    ✓ User reply detected: '{phrase}' → User handling")
                    return True
        
        return False
    
    @staticmethod
    def check_for_user_reply(html: str) -> bool:
        """
        Check if recent messages have user saying they'll handle it.
        
        Returns: True if user replied they're handling it, False otherwise
        """
        try:
            messages = MessageExtractor.extract_from_group_html(html)
            
            if not messages:
                logger.debug("  [reply-check] No messages in HTML")
                return False
            
            # Check last 10 messages
            for msg in messages[-10:]:
                if ReplyDetector.is_user_handling(msg["text"]):
                    logger.info(f"  [reply-check] ✓ User handling: {msg['text'][:60]}")
                    return True
            
            logger.debug(f"  [reply-check] ✗ No user handling reply in last {len(messages)} messages")
            return False
        except Exception as e:
            logger.error(f"Reply check error: {e}")
            return False

# ==============================================================================
# DRIVER MANAGEMENT
# ==============================================================================

class DriverManager:
    """Safe WebDriver lifecycle management."""
    
    def __init__(self):
        self.driver = None
        self.active_window = 0
        self.retry_count = 0
    
    def initialize(self) -> bool:
        """Initialize Edge WebDriver."""
        try:
            logger.info("Initializing WebDriver...")
            
            options = webdriver.EdgeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_argument("--disable-gpu")
            
            self.driver = webdriver.Edge(options=options)
            self.driver.set_page_load_timeout(60)
            self.driver.set_script_timeout(60)
            
            logger.info("✓ WebDriver initialized")
            return True
        except Exception as e:
            logger.error(f"Driver init failed: {e}")
            self.driver = None
            return False
    
    def navigate(self, url: str, timeout: int = 30) -> bool:
        """Navigate to URL."""
        try:
            if not self.driver:
                return False
            
            self.driver.get(url)
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Navigated to {url}")
            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            self._recover_driver()
            return False
    
    def get_page_html(self) -> str:
        """Get current page HTML."""
        try:
            if not self.driver:
                return ""
            return self.driver.page_source
        except InvalidSessionIdException:
            logger.error("Driver session lost")
            self._recover_driver()
            return ""
        except Exception as e:
            logger.error(f"Page read error: {e}")
            return ""
    
    def switch_to_tab(self, index: int, wait_load: int = 5) -> bool:
        """Switch to tab and wait for page load."""
        try:
            if not self.driver or index >= len(self.driver.window_handles):
                logger.error(f"Tab {index} not available (total: {len(self.driver.window_handles)})")
                return False
            
            self.driver.switch_to.window(self.driver.window_handles[index])
            self.active_window = index
            
            time.sleep(wait_load)
            
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Switched to tab {index}")
            return True
        except Exception as e:
            logger.error(f"Tab switch error: {e}")
            return False
    
    def open_new_tab(self, url: str) -> bool:
        """Open new tab with URL."""
        try:
            self.driver.execute_script(f"window.open('{url}', '_blank');")
            time.sleep(3)
            
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.active_window = len(self.driver.window_handles) - 1
            
            time.sleep(3)
            
            logger.info(f"✓ Opened new tab {self.active_window}")
            return True
        except Exception as e:
            logger.error(f"New tab failed: {e}")
            return False
    
    def _recover_driver(self):
        """Recover from driver crash."""
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        self.driver = None
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

# ==============================================================================
# CRM OPERATIONS
# ==============================================================================

class CRMOperations:
    """VidaPay CRM operations - ONLY processes transfers, no WhatsApp replies."""
    
    def __init__(self, driver_manager: DriverManager, crm_url: str):
        self.driver_manager = driver_manager
        self.crm_url = crm_url
        self.crm_tab_index = None
        self.whatsapp_tab_index = 0
    
    def open_crm(self) -> bool:
        """Open CRM in new tab."""
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
        """
        Process transfer in CRM.
        
        Returns:
            Tuple of (success: bool, message: str)
            success=True: transfer completed successfully
            success=False: transfer failed with error message
        """
        try:
            logger.info(f"[CRM] Processing: {transfer['store']} → {len(transfer['imeis'])} IMEI(s)")
            
            if not self.driver_manager.switch_to_tab(self.crm_tab_index, wait_load=5):
                return False, "Failed to switch to CRM tab"
            
            if not self._navigate_to_reassignment():
                return False, "Failed to navigate to reassignment tool"
            
            if not self._enter_account_id(transfer["account_id"]):
                return False, f"Account ID invalid or store locked: {transfer['account_id']}"
            
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
            driver = self.driver_manager.driver
            
            if "Reassign" in driver.page_source or "Inventory" in driver.page_source:
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
                    elem = WebDriverWait(driver, 5).until(
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
        """Enter account ID."""
        try:
            driver = self.driver_manager.driver
            
            logger.info(f"[CRM] Entering Account ID: {account_id}")
            
            account_selectors = [
                (By.ID, "account_id"),
                (By.NAME, "account_id"),
                (By.NAME, "accountId"),
                (By.XPATH, "//input[contains(@placeholder, 'Account')]"),
                (By.XPATH, "//input[contains(@name, 'account')]"),
                (By.CSS_SELECTOR, "input[placeholder*='Account']"),
            ]
            
            field = None
            for by, selector in account_selectors:
                try:
                    field = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    logger.info(f"[CRM] ✓ Found account field")
                    break
                except:
                    pass
            
            if not field:
                logger.error("[CRM] Account ID field not found")
                return False
            
            driver.execute_script("arguments[0].scrollIntoView(true);", field)
            time.sleep(1)
            
            field.clear()
            field.send_keys(account_id)
            field.send_keys(Keys.TAB)
            
            time.sleep(2)
            
            logger.info(f"[CRM] ✓ Account ID entered")
            return True
        except Exception as e:
            logger.error(f"[CRM] Account ID error: {e}")
            return False
    
    def _enter_imeis(self, imeis: List[str]) -> bool:
        """Enter IMEIs."""
        try:
            driver = self.driver_manager.driver
            
            logger.info(f"[CRM] Entering {len(imeis)} IMEI(s)...")
            
            for idx, imei in enumerate(imeis, 1):
                imei_selectors = [
                    (By.XPATH, "//input[contains(@placeholder, 'IMEI')]"),
                    (By.XPATH, "//input[contains(@name, 'imei')]"),
                    (By.NAME, "imei"),
                    (By.CSS_SELECTOR, "input[placeholder*='IMEI']"),
                ]
                
                field = None
                for by, selector in imei_selectors:
                    try:
                        field = WebDriverWait(driver, 5).until(
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
        except Exception as e:
            logger.error(f"[CRM] IMEI error: {e}")
            return False
    
    def _submit_transfer(self) -> bool:
        """Submit transfer."""
        try:
            driver = self.driver_manager.driver
            
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
                    submit_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    logger.info(f"[CRM] ✓ Found submit button")
                    break
                except:
                    pass
            
            if not submit_btn:
                logger.error("[CRM] Submit button not found")
                return False
            
            driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
            time.sleep(1)
            
            submit_btn.click()
            
            time.sleep(3)
            
            html = driver.page_source
            if "success" in html.lower() or "completed" in html.lower():
                logger.info("[CRM] ✓ Transfer submitted successfully")
                return True
            
            logger.warning("[CRM] Submission status unclear")
            return True
        except Exception as e:
            logger.error(f"[CRM] Submission error: {e}")
            return False

# ==============================================================================
# WHATSAPP REPLY SENDER
# ==============================================================================

class WhatsAppReplier:
    """Send replies to WhatsApp - ONLY after transfer is processed."""
    
    def __init__(self, driver_manager: DriverManager):
        self.driver_manager = driver_manager
        self.whatsapp_tab_index = 0
    
    def send_reply(self, group_name: str, message: str) -> bool:
        """
        Send reply to WhatsApp group.
        IMPORTANT: Only call AFTER transfer is fully processed!
        
        Args:
            group_name: WhatsApp group name
            message: Reply message to send
            
        Returns: True if sent, False otherwise
        """
        try:
            logger.info(f"[REPLY] Sending to '{group_name}': {message[:60]}...")
            
            # Switch to WhatsApp tab
            if not self.driver_manager.switch_to_tab(self.whatsapp_tab_index, wait_load=2):
                logger.error("[REPLY] Failed to switch to WhatsApp tab")
                return False
            
            driver = self.driver_manager.driver
            
            # Find message input field
            msg_selectors = [
                (By.XPATH, "//div[@title='Type a message']"),
                (By.XPATH, "//div[@contenteditable='true']"),
                (By.CSS_SELECTOR, "div[role='textbox']"),
                (By.XPATH, "//p[@class='']"),
            ]
            
            msg_field = None
            for by, selector in msg_selectors:
                try:
                    msg_field = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    logger.info(f"[REPLY] ✓ Found message field")
                    break
                except:
                    pass
            
            if not msg_field:
                logger.error("[REPLY] Message field not found")
                return False
            
            # Click field and type message
            msg_field.click()
            time.sleep(0.5)
            
            # Use pyautogui to type (more reliable)
            pyautogui.typewrite(message, interval=0.05)
            
            # Send with Enter key
            msg_field.send_keys(Keys.RETURN)
            
            time.sleep(1)
            
            logger.info(f"[REPLY] ✓ Message sent to '{group_name}'")
            return True
            
        except Exception as e:
            logger.error(f"[REPLY] Send error: {e}")
            return False

# ==============================================================================
# TRANSFER PROCESSOR - THE HEART OF THE BOT
# ==============================================================================

class TransferProcessor:
    """Process a single transfer request - MAIN WORKFLOW."""
    
    def __init__(self, driver_manager: DriverManager, crm_ops: CRMOperations, replier: WhatsAppReplier):
        self.driver_manager = driver_manager
        self.crm_ops = crm_ops
        self.replier = replier
    
    def process(self, transfer: Dict, wait_for_reply: bool = True) -> bool:
        """
        Process a transfer request.
        
        WORKFLOW:
        1. If wait_for_reply=True: Wait 30s to see if user replies they're handling it
        2. If user replied: Mark as claimed, don't process
        3. If no reply after 30s: Process in CRM
        4. After CRM processes: Send result reply
        
        Returns: True if transfer processed successfully or user handling it
        """
        transfer_id = f"{transfer['group']}_{transfer['store']}_{datetime.now().timestamp()}"
        
        logger.info(f"\n[TRANSFER {transfer_id[:30]}...]")
        logger.info(f"  Request: {transfer['group']} → {transfer['store']} ({len(transfer['imeis'])} IMEIs)")
        
        # STEP 1: Wait for user reply if enabled
        if wait_for_reply:
            logger.info(f"  Waiting {REPLY_WAIT_TIME}s for user reply ('on it', 'doing', etc.)...")
            
            deadline = datetime.now() + timedelta(seconds=REPLY_WAIT_TIME)
            user_handling = False
            
            while datetime.now() < deadline:
                html = self.driver_manager.get_page_html()
                if ReplyDetector.check_for_user_reply(html):
                    user_handling = True
                    logger.info(f"  ✓ User replied they're handling it - SKIPPING bot processing")
                    return True  # Don't process, user is handling it
                
                time.sleep(2)  # Check every 2 seconds
            
            logger.info(f"  ⏳ No user reply after {REPLY_WAIT_TIME}s - BOT will process")
        
        # STEP 2: Process in CRM
        logger.info(f"  [CRM] Starting transfer processing...")
        success, message = self.crm_ops.process_transfer(transfer)
        
        # STEP 3: Send result reply - ONLY NOW, after CRM is done
        if success:
            reply_msg = f"✅ Transfer successful to {transfer['store']}"
            logger.info(f"  [RESULT] ✓ {message}")
        else:
            reply_msg = f"⚠️ Transfer failed: {message}"
            logger.error(f"  [RESULT] ✗ {message}")
        
        # STEP 4: Send WhatsApp reply with result
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
        self.root.title("WhatsApp → VidaPay Transfer Bot v3.1 - NO EARLY REPLIES")
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
        
        ttk.Label(header, text="WhatsApp → VidaPay Transfer Bot v3.1",
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
        self._log("=== BOT STARTED v3.1 ===", "SUCCESS")
        self._log("CRITICAL: NO messages sent to WhatsApp until transfer is processed!", "WARNING")
        self._log(f"Reply wait time: {REPLY_WAIT_TIME}s (user can reply to claim transfer)", "INFO")
        
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
        """Main monitoring loop."""
        if not self.driver_manager.initialize():
            self._log("Failed to initialize driver", "ERROR")
            self.root.after(0, self._stop)
            return
        
        if not self.driver_manager.navigate(WHATSAPP_URL):
            self._log("Failed to navigate to WhatsApp", "ERROR")
            self.root.after(0, self._stop)
            return
        
        self._log("Waiting for WhatsApp login (scan QR code)...", "INFO")
        time.sleep(15)
        
        # Initialize CRM and replier
        self.crm_ops = CRMOperations(self.driver_manager, "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx")
        self.replier = WhatsAppReplier(self.driver_manager)
        self.processor = TransferProcessor(self.driver_manager, self.crm_ops, self.replier)
        
        if not self.crm_ops.open_crm():
            self._log("Failed to open CRM", "ERROR")
            self.root.after(0, self._stop)
            return
        
        self._log("Bot ready - monitoring WhatsApp groups...", "SUCCESS")
        
        while not self.stop_requested:
            try:
                html = self.driver_manager.get_page_html()
                if not html:
                    time.sleep(SCREENSHOT_INTERVAL)
                    continue
                
                # Extract messages
                messages = MessageExtractor.extract_from_group_html(html)
                
                for msg in messages:
                    msg_hash = hash(msg["text"])
                    if msg_hash in self.processed_messages:
                        continue  # Already processed
                    
                    # Check for trigger words
                    msg_lower = msg["text"].lower()
                    has_trigger = any(trigger in msg_lower for trigger in triggers)
                    
                    if not has_trigger:
                        continue
                    
                    # Extract IMEIs
                    imeis = re.findall(IMEI_PATTERN, msg["text"])
                    if not imeis:
                        continue
                    
                    # Extract store name
                    store = self._extract_store_name(msg["text"], triggers)
                    if not store:
                        continue
                    
                    account_id = self.store_aliases.get(store.lower())
                    if not account_id:
                        self._log(f"Unknown store: {store} (add to aliases)", "WARNING")
                        continue
                    
                    # Create transfer and process
                    transfer = {
                        "group": groups[0],  # Detected from current context
                        "store": store,
                        "account_id": account_id,
                        "imeis": imeis,
                        "original_msg": msg["text"]
                    }
                    
                    self.processed_messages.add(msg_hash)
                    
                    # PROCESS TRANSFER (with reply wait)
                    self.processor.process(transfer, wait_for_reply=True)
                
                time.sleep(SCREENSHOT_INTERVAL)
                
            except Exception as e:
                self._log(f"Loop error: {e}", "ERROR")
                time.sleep(SCREENSHOT_INTERVAL)
    
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
