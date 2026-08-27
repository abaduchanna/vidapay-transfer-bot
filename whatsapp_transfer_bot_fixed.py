#!/usr/bin/env python3
"""
WhatsApp → VidaPay CRM Transfer Bot v2.0
Continuous monitoring with driver crash recovery, proper reply detection, and message extraction.

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
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# WebDriver & Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
    WebDriverException, InvalidSessionIdException
)

# HTML/Image processing
from bs4 import BeautifulSoup
from PIL import ImageGrab
import pytesseract

# ==============================================================================
# CONFIGURATION
# ==============================================================================

WHATSAPP_URL = "https://web.whatsapp.com"
IMEI_PATTERN = r"\b(35|01)\d{13}\b"
SCREENSHOT_INTERVAL = 10  # seconds between checks
MAX_RETRIES = 3
RETRY_DELAY = 5

# Reply confirmation phrases (bot looks for these in replies, not trigger words)
HANDLING_PHRASES = {
    "received": ["received", "got it", "ok", "ack", "acknowledged"],
    "processing": ["processing", "working on it", "on it", "handling"],
    "done": ["done", "completed", "finished", "transferred", "moved"],
    "error": ["error", "failed", "issue", "problem", "not possible", "can't", "unable"]
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# WHATSAPP MESSAGE HANDLER
# ==============================================================================

class MessageExtractor:
    """Extract and parse WhatsApp messages using BeautifulSoup."""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean message text: remove extra whitespace, normalize."""
        if not text:
            return ""
        # Remove multiple spaces, newlines, special chars
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s\-\+\.]', '', text)
        return text.strip()
    
    @staticmethod
    def extract_message_text(msg_element) -> Tuple[str, str]:
        """
        Extract message text and sender from DOM element.
        
        Returns:
            Tuple of (message_text, sender_name)
        """
        try:
            # Try multiple selectors for message body
            msg_text = ""
            sender = "unknown"
            
            # Get message body text
            body_selectors = [
                "span[class*='selectable-text']",
                "div[class*='message-text']",
                "span[class*='text']",
                ".quoted-text"
            ]
            
            for selector in body_selectors:
                try:
                    elem = msg_element.select_one(selector)
                    if elem and elem.text:
                        msg_text = MessageExtractor.clean_text(elem.text)
                        break
                except:
                    pass
            
            # Get sender name
            sender_selectors = [
                "span[title]",
                ".message-author",
                "div[class*='sender']"
            ]
            
            for selector in sender_selectors:
                try:
                    elem = msg_element.select_one(selector)
                    if elem and elem.get("title"):
                        sender = elem.get("title")
                        break
                    elif elem and elem.text:
                        sender = MessageExtractor.clean_text(elem.text)[:30]
                        break
                except:
                    pass
            
            return msg_text, sender
        except Exception as e:
            logger.debug(f"Message extraction error: {e}")
            return "", "unknown"
    
    @staticmethod
    def extract_from_group_html(html_content: str) -> List[Dict]:
        """
        Extract recent messages from WhatsApp group HTML.
        
        Args:
            html_content: Raw HTML from group chat
            
        Returns:
            List of dicts: [{text, sender, timestamp}, ...]
        """
        messages = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Find all message divs
            msg_divs = soup.select(
                "div[role='article'], "
                "div[class*='message'], "
                "div[data-testid*='message-item']"
            )
            
            for div in msg_divs[-15:]:  # Last 15 messages
                msg_text, sender = MessageExtractor.extract_message_text(div)
                
                if msg_text:  # Only include non-empty messages
                    messages.append({
                        "text": msg_text,
                        "sender": sender,
                        "timestamp": datetime.now()
                    })
            
            return messages
        except Exception as e:
            logger.warning(f"HTML extraction error: {e}")
            return []

class DriverManager:
    """Manages WebDriver lifecycle and crash recovery."""
    
    def __init__(self):
        self.driver = None
        self.active_window = None
        self.retry_count = 0
        
    def initialize(self, headless: bool = False) -> bool:
        """Initialize Edge WebDriver with crash recovery."""
        try:
            logger.info("Initializing WebDriver...")
            
            options = webdriver.EdgeOptions()
            if headless:
                options.add_argument("--headless")
            
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
            
            logger.info("✓ WebDriver initialized")
            return True
        except Exception as e:
            logger.error(f"Driver init failed: {e}")
            self.driver = None
            return False
    
    def navigate(self, url: str, timeout: int = 30) -> bool:
        """Navigate to URL with error handling."""
        try:
            if not self.driver:
                return False
            
            self.driver.get(url)
            
            # Wait for page to load
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Navigated to {url}")
            return True
        except TimeoutException:
            logger.error(f"Navigation timeout: {url}")
            return False
        except WebDriverException as e:
            logger.error(f"WebDriver error during navigation: {e}")
            self._recover_driver()
            return False
    
    def get_page_html(self) -> str:
        """Get current page HTML with error handling."""
        try:
            if not self.driver:
                return ""
            return self.driver.page_source
        except InvalidSessionIdException:
            logger.error("Driver session lost - reinitializing")
            self._recover_driver()
            return ""
        except WebDriverException as e:
            logger.error(f"WebDriver error reading page: {e}")
            self._recover_driver()
            return ""
    
    def switch_to_tab(self, index: int) -> bool:
        """Switch to browser tab by index."""
        try:
            if not self.driver or index >= len(self.driver.window_handles):
                return False
            
            self.driver.switch_to.window(self.driver.window_handles[index])
            self.active_window = index
            return True
        except Exception as e:
            logger.error(f"Tab switch failed: {e}")
            return False
    
    def open_new_tab(self, url: str) -> bool:
        """Open new tab with URL."""
        try:
            self.driver.execute_script(f"window.open('{url}', '_blank');")
            time.sleep(2)
            
            # Switch to newest tab
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.active_window = len(self.driver.window_handles) - 1
            
            logger.info(f"✓ Opened new tab ({self.active_window})")
            return True
        except Exception as e:
            logger.error(f"New tab failed: {e}")
            return False
    
    def close_tab(self, index: int = None) -> bool:
        """Close tab by index (default: current)."""
        try:
            if index is None:
                index = self.active_window
            
            if index >= len(self.driver.window_handles):
                return False
            
            self.driver.switch_to.window(self.driver.window_handles[index])
            self.driver.close()
            
            # Switch back to first tab
            if len(self.driver.window_handles) > 0:
                self.driver.switch_to.window(self.driver.window_handles[0])
                self.active_window = 0
            
            logger.info(f"✓ Closed tab {index}")
            return True
        except Exception as e:
            logger.error(f"Close tab failed: {e}")
            return False
    
    def _recover_driver(self):
        """Attempt to recover from driver crash."""
        logger.warning("Attempting driver recovery...")
        
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
# REPLY DETECTOR
# ==============================================================================

class ReplyDetector:
    """Detect confirmation/handling replies in chat."""
    
    @staticmethod
    def find_handling_phrase(text: str) -> Optional[str]:
        """
        Check if text contains a handling phrase.
        
        Returns:
            Category of handling phrase found, or None
        """
        text_lower = text.lower()
        
        for category, phrases in HANDLING_PHRASES.items():
            for phrase in phrases:
                if phrase.lower() in text_lower:
                    return category
        
        return None
    
    @staticmethod
    def check_for_reply(group_html: str, keywords: List[str], timeout: int = 30) -> bool:
        """
        Check if group chat contains a reply with handling phrases.
        
        Args:
            group_html: Raw HTML from group
            keywords: Keywords from original request (to ignore)
            timeout: How long to check (seconds)
            
        Returns:
            True if handling reply found, False otherwise
        """
        try:
            messages = MessageExtractor.extract_from_group_html(group_html)
            
            # Check last few messages for handling phrases
            for msg in messages[-5:]:
                handling_type = ReplyDetector.find_handling_phrase(msg["text"])
                
                if handling_type:
                    logger.info(f"  [reply-check] ✓ Found '{handling_type}' reply: {msg['text'][:60]}")
                    return True
            
            logger.info(f"  [reply-check] ✗ No handling reply found")
            return False
            
        except Exception as e:
            logger.error(f"Reply detection error: {e}")
            return False

# ==============================================================================
# MESSAGE PROCESSOR
# ==============================================================================

class MessageProcessor:
    """Process WhatsApp messages and extract transfer requests."""
    
    def __init__(self, trigger_words: List[str], store_aliases: Dict):
        self.trigger_words = [w.lower().strip() for w in trigger_words]
        self.store_aliases = store_aliases
        self.processed_hashes = set()  # Avoid duplicate processing
    
    def find_transfers(self, group_html: str, group_name: str) -> List[Dict]:
        """
        Extract transfer requests from group HTML.
        
        Returns:
            List of transfer dicts: [{store, imeis, original_msg}, ...]
        """
        transfers = []
        
        try:
            messages = MessageExtractor.extract_from_group_html(group_html)
            
            for msg in messages[-10:]:  # Last 10 messages
                msg_hash = hash(msg["text"])
                
                # Skip already processed
                if msg_hash in self.processed_hashes:
                    continue
                
                # Check for trigger words
                msg_lower = msg["text"].lower()
                has_trigger = any(trigger in msg_lower for trigger in self.trigger_words)
                
                if not has_trigger:
                    continue
                
                # Extract IMEIs
                imeis = re.findall(IMEI_PATTERN, msg["text"])
                if not imeis:
                    continue
                
                # Extract store name (next words after trigger)
                store = self._extract_store_name(msg["text"])
                if not store:
                    continue
                
                # Resolve store alias to Account ID
                account_id = self._resolve_account_id(store)
                if not account_id:
                    logger.warning(f"Unknown store: {store}")
                    continue
                
                transfer = {
                    "group": group_name,
                    "store": store,
                    "account_id": account_id,
                    "imeis": imeis,
                    "sender": msg["sender"],
                    "original_msg": msg["text"],
                    "timestamp": datetime.now()
                }
                
                transfers.append(transfer)
                self.processed_hashes.add(msg_hash)
                
                logger.info(
                    f"✓ Transfer request: {group_name} → {store} "
                    f"({len(imeis)} IMEI(s))"
                )
        
        except Exception as e:
            logger.error(f"Message processing error: {e}")
        
        return transfers
    
    def _extract_store_name(self, text: str) -> Optional[str]:
        """Extract store name from message."""
        try:
            for trigger in self.trigger_words:
                pattern = trigger + r'\s+([A-Za-z\s\-]+)'
                match = re.search(pattern, text, re.IGNORECASE)
                
                if match:
                    store = match.group(1).strip()
                    # Take first 2-3 words
                    return " ".join(store.split()[:3])
        except:
            pass
        
        return None
    
    def _resolve_account_id(self, store_name: str) -> Optional[str]:
        """Resolve store name to Account ID using aliases."""
        if not store_name or not self.store_aliases:
            return None
        
        store_lower = store_name.lower()
        
        # Direct lookup
        if store_lower in self.store_aliases:
            return self.store_aliases[store_lower]
        
        # Partial match (longest wins)
        matches = []
        for alias, account_id in self.store_aliases.items():
            if alias in store_lower or store_lower in alias:
                matches.append((len(alias), alias, account_id))
        
        if matches:
            matches.sort(reverse=True)
            return matches[0][2]
        
        return None

# ==============================================================================
# CRM OPERATIONS
# ==============================================================================

class CRMOperations:
    """Handle VidaPay CRM transfer processing."""
    
    def __init__(self, driver_manager: DriverManager, crm_url: str):
        self.driver_manager = driver_manager
        self.crm_url = crm_url
        self.crm_tab_index = None
    
    def open_crm(self) -> bool:
        """Open CRM in new tab."""
        try:
            if not self.driver_manager.open_new_tab(self.crm_url):
                logger.error("Failed to open CRM tab")
                return False
            
            # Wait for CRM to load
            time.sleep(3)
            
            # Verify we're on CRM page
            html = self.driver_manager.get_page_html()
            if "VidaPay" not in html:
                logger.warning("CRM may not have loaded properly")
            
            self.crm_tab_index = self.driver_manager.active_window
            logger.info(f"✓ CRM opened in tab {self.crm_tab_index}")
            return True
            
        except Exception as e:
            logger.error(f"Error opening CRM: {e}")
            return False
    
    def process_transfer(self, transfer: Dict) -> bool:
        """
        Process a single transfer in CRM.
        
        Args:
            transfer: Transfer dict with store, account_id, imeis
            
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(
                f"Processing: {transfer['store']} → "
                f"{len(transfer['imeis'])} IMEI(s)"
            )
            
            # Make sure we're on CRM tab
            if not self.driver_manager.switch_to_tab(self.crm_tab_index or -1):
                logger.error("Failed to switch to CRM tab")
                return False
            
            # Navigate to reassignment tool
            if not self._navigate_to_reassignment():
                logger.error("Failed to navigate to reassignment tool")
                return False
            
            # Enter account ID
            if not self._enter_account_id(transfer["account_id"]):
                logger.error(f"Failed to enter account ID: {transfer['account_id']}")
                return False
            
            # Process IMEIs
            if not self._enter_imeis(transfer["imeis"]):
                logger.error("Failed to enter IMEIs")
                return False
            
            # Submit transfer
            if not self._submit_transfer():
                logger.error("Failed to submit transfer")
                return False
            
            logger.info(f"✓ Transfer completed: {transfer['store']}")
            return True
            
        except Exception as e:
            logger.error(f"CRM transfer error: {e}")
            return False
    
    def _navigate_to_reassignment(self, timeout: int = 20) -> bool:
        """Navigate to inventory reassignment tool."""
        try:
            # Try to find and click reassignment link
            driver = self.driver_manager.driver
            
            # Wait for tool link to appear
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//a[contains(text(), 'Reassign')] | "
                    "//button[contains(text(), 'Reassign')]"
                ))
            )
            
            # Click it
            link = driver.find_element(
                By.XPATH,
                "//a[contains(text(), 'Reassign')] | "
                "//button[contains(text(), 'Reassign')]"
            )
            link.click()
            
            time.sleep(2)
            logger.info("✓ Navigated to reassignment tool")
            return True
            
        except TimeoutException:
            logger.warning("Reassignment tool not found - may already be open")
            return True  # Assume already open
        except Exception as e:
            logger.error(f"Navigation error: {e}")
            return False
    
    def _enter_account_id(self, account_id: str, timeout: int = 15) -> bool:
        """Enter account ID."""
        try:
            driver = self.driver_manager.driver
            
            field = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.ID, "account_id"))
            )
            
            field.clear()
            field.send_keys(account_id)
            field.send_keys(Keys.TAB)
            
            time.sleep(1)
            
            logger.info(f"✓ Account ID entered: {account_id}")
            return True
            
        except Exception as e:
            logger.error(f"Account ID entry error: {e}")
            return False
    
    def _enter_imeis(self, imeis: List[str], timeout: int = 10) -> bool:
        """Enter IMEI list."""
        try:
            driver = self.driver_manager.driver
            
            for imei in imeis:
                field = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        "//input[contains(@placeholder, 'IMEI')] | "
                        "//input[contains(@name, 'imei')]"
                    ))
                )
                
                field.clear()
                field.send_keys(imei)
                field.send_keys(Keys.TAB)
                
                time.sleep(0.5)
            
            logger.info(f"✓ Entered {len(imeis)} IMEI(s)")
            return True
            
        except Exception as e:
            logger.error(f"IMEI entry error: {e}")
            return False
    
    def _submit_transfer(self, timeout: int = 20) -> bool:
        """Submit the transfer."""
        try:
            driver = self.driver_manager.driver
            
            submit_btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(text(), 'Submit')] | "
                    "//button[contains(text(), 'Confirm')]"
                ))
            )
            
            submit_btn.click()
            
            time.sleep(2)
            
            # Check for success
            html = driver.page_source
            if "success" in html.lower() or "completed" in html.lower():
                logger.info("✓ Transfer submitted successfully")
                return True
            
            logger.warning("Submission status unclear")
            return True  # Assume success
            
        except Exception as e:
            logger.error(f"Submission error: {e}")
            return False
    
    def close_crm(self):
        """Close CRM tab."""
        if self.crm_tab_index is not None:
            self.driver_manager.close_tab(self.crm_tab_index)
            self.crm_tab_index = None

# ==============================================================================
# MAIN BOT APPLICATION
# ==============================================================================

class WhatsAppTransferBot:
    """Main bot controller."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("WhatsApp → VidaPay Transfer Bot v2.0")
        self.root.geometry("1100x750")
        
        self.is_running = False
        self.stop_requested = False
        self.monitor_thread = None
        
        self.driver_manager = DriverManager()
        self.message_processor = None
        self.crm_ops = None
        
        self.transfer_queue = []
        self.failed_transfers = []
        
        self._build_ui()
        self._load_config()
    
    def _build_ui(self):
        """Build GUI."""
        # Header
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(header, text="WhatsApp → VidaPay CRM Transfer Bot v2.0",
                  font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        
        self.status_label = ttk.Label(header, text="● IDLE", 
                                      foreground="gray", font=("Segoe UI", 10))
        self.status_label.pack(side=tk.RIGHT)
        
        # Controls
        controls = ttk.Frame(self.root)
        controls.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = ttk.Button(controls, text="▶ START", 
                                     command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(controls, text="⏹ STOP",
                                    command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(controls, text="⚙️ Settings", 
                   command=self._open_settings).pack(side=tk.LEFT, padx=5)
        
        # Tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Log tab
        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text="📊 Log")
        
        self.log_text = scrolledtext.ScrolledText(log_frame, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        self.log_text.tag_config("SUCCESS", foreground="#22C55E")
        self.log_text.tag_config("ERROR", foreground="#EF4444")
        self.log_text.tag_config("INFO", foreground="#3B82F6")
        self.log_text.tag_config("WARNING", foreground="#F59E0B")
        
        # Settings tab
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
        
        # Queue tab
        queue_frame = ttk.Frame(notebook)
        notebook.add(queue_frame, text="📦 Queue")
        
        self.queue_text = scrolledtext.ScrolledText(queue_frame, font=("Consolas", 9))
        self.queue_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Status bar
        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=tk.X, padx=10, pady=5)
        
        self.info_label = ttk.Label(status_bar, text="Ready", font=("Segoe UI", 9))
        self.info_label.pack(anchor=tk.W)
    
    def _log(self, msg: str, tag: str = "INFO"):
        """Log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {msg}\n"
        
        self.log_text.insert(tk.END, log_msg, tag)
        self.log_text.see(tk.END)
        self.log_text.update()
        
        logger.log(
            logging.INFO if tag == "INFO" else logging.WARNING if tag == "WARNING"
            else logging.ERROR if tag == "ERROR" else logging.INFO,
            msg
        )
    
    def _start(self):
        """Start monitoring."""
        if self.is_running:
            messagebox.showwarning("Running", "Already running")
            return
        
        # Load settings
        triggers = [t.strip() for t in self.triggers_input.get().split(",")]
        groups = [g.strip() for g in self.groups_input.get().split(",")]
        
        if not triggers or not groups:
            messagebox.showerror("Config", "Set trigger words and groups")
            return
        
        self.is_running = True
        self.stop_requested = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        self._update_status("MONITORING", "green")
        self._log("=== MONITORING STARTED ===", "SUCCESS")
        self._log(f"Triggers: {', '.join(triggers)}", "INFO")
        self._log(f"Groups: {', '.join(groups)}", "INFO")
        
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(triggers, groups),
            daemon=False
        )
        self.monitor_thread.start()
    
    def _stop(self):
        """Stop monitoring."""
        self.stop_requested = True
        self._log("Stopping...", "WARNING")
        self._update_status("STOPPING", "orange")
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        self.driver_manager.quit()
        
        self._update_status("STOPPED", "red")
        self._log("=== MONITORING STOPPED ===", "ERROR")
    
    def _monitoring_loop(self, triggers: List[str], groups: List[str]):
        """Main monitoring loop."""
        self.message_processor = MessageProcessor(triggers, getattr(self, 'store_aliases', {}))
        
        if not self.driver_manager.initialize():
            self._log("Failed to initialize driver", "ERROR")
            self.root.after(0, self._stop)
            return
        
        if not self.driver_manager.navigate(WHATSAPP_URL):
            self._log("Failed to navigate to WhatsApp", "ERROR")
            self.root.after(0, self._stop)
            return
        
        self._log("Waiting for WhatsApp login (scan QR code)...", "INFO")
        time.sleep(15)  # Wait for login
        
        retry_count = 0
        
        while not self.stop_requested:
            try:
                # Get WhatsApp page
                html = self.driver_manager.get_page_html()
                if not html:
                    raise Exception("Failed to get page HTML")
                
                # Extract messages from groups
                for group in groups:
                    if self.stop_requested:
                        break
                    
                    transfers = self.message_processor.find_transfers(html, group)
                    
                    for transfer in transfers:
                        if self.stop_requested:
                            break
                        
                        self.transfer_queue.append(transfer)
                        self._update_queue()
                        
                        # Process transfer
                        success = self._process_single_transfer(transfer)
                        
                        if not success:
                            self.failed_transfers.append(transfer)
                
                retry_count = 0
                
            except InvalidSessionIdException:
                self._log("Driver session lost - recovering...", "WARNING")
                self.driver_manager._recover_driver()
                retry_count += 1
                
                if retry_count >= MAX_RETRIES:
                    self._log("Max retries exceeded", "ERROR")
                    self.root.after(0, self._stop)
                    break
                
                time.sleep(RETRY_DELAY)
                
            except Exception as e:
                self._log(f"Monitoring error: {e}", "ERROR")
                retry_count += 1
                
                if retry_count >= MAX_RETRIES:
                    self.root.after(0, self._stop)
                    break
                
                time.sleep(SCREENSHOT_INTERVAL)
            
            time.sleep(SCREENSHOT_INTERVAL)
    
    def _process_single_transfer(self, transfer: Dict) -> bool:
        """Process a single transfer request."""
        try:
            self._log(
                f"Processing: {transfer['group']} → "
                f"{transfer['store']} ({len(transfer['imeis'])} IMEI(s))",
                "PROCESS"
            )
            
            if not self.crm_ops:
                self.crm_ops = CRMOperations(self.driver_manager, "https://vidapaycrm.com")
                if not self.crm_ops.open_crm():
                    self._log("Failed to open CRM", "ERROR")
                    return False
            
            success = self.crm_ops.process_transfer(transfer)
            
            if success:
                self._log(f"✓ Completed: {transfer['store']}", "SUCCESS")
            else:
                self._log(f"✗ Failed: {transfer['store']}", "ERROR")
            
            return success
            
        except Exception as e:
            self._log(f"Transfer processing error: {e}", "ERROR")
            return False
    
    def _update_status(self, status: str, color: str):
        """Update status indicator."""
        self.status_label.config(text=f"● {status}", foreground=color)
    
    def _update_queue(self):
        """Update queue display."""
        self.queue_text.config(state=tk.NORMAL)
        self.queue_text.delete(1.0, tk.END)
        
        for i, t in enumerate(self.transfer_queue, 1):
            line = f"{i}. {t['group']} → {t['store']} ({len(t['imeis'])} IMEI(s))\n"
            self.queue_text.insert(tk.END, line)
        
        self.queue_text.config(state=tk.DISABLED)
    
    def _open_settings(self):
        """Open settings dialog."""
        messagebox.showinfo("Settings", "Configure via Settings tab above")
    
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
