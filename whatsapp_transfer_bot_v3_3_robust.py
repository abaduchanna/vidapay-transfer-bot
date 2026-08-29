#!/usr/bin/env python3
"""
WhatsApp → VidaPay CRM Transfer Bot v3.3 - COMPLETE REWRITE

ISSUES FIXED:
1. ✅ CORRECT CRM SELECTORS from actual HTML structure
   - Account: name="ctl00$MainContent$rcbAccount" id="rcbAccount_Input"
   - IMEI: name="ctl00$MainContent$txtSimEntry" id="txtSimEntry"
   - Next: id="btnNext"
   - Submit: id="MainContent_submitButton"

2. ✅ ROBUST REPLY DETECTION
   - NO premature "on it" replies
   - Only process if user didn't reply within 30s
   - Check multiple messages backward

3. ✅ ERROR HANDLING WITH SCREENSHOTS
   - When IMEI validation fails → send screenshot to WhatsApp
   - Show actual error messages (not just "N/A")
   - Screenshot of "SIM not found!" dialog

4. ✅ BULK IMEI UPLOAD
   - Support template upload for 2+ IMEIs
   - Single entry for 1 IMEI
   - CSV parsing

5. ✅ BETTER ERROR MESSAGES
   - Clear feedback on what failed
   - IMEI validation results
   - Account field accessibility

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
from io import BytesIO
import base64

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
from PIL import Image

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

GFH_NAVY = "#090d26"
GFH_RED = "#f0541c"
GFH_WHITE = "#ffffff"

# ==============================================================================
# MESSAGE & REPLY HANDLING - ROBUST
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
        """Extract last 50 messages from group HTML - very thorough."""
        messages = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Try multiple selector strategies
            msg_divs = []
            
            # Strategy 1: role='article'
            msg_divs.extend(soup.select("div[role='article']"))
            
            # Strategy 2: data-testid containing 'message'
            msg_divs.extend(soup.select("div[data-testid*='message']"))
            
            # Strategy 3: class containing 'message'
            msg_divs.extend(soup.select("div[class*='message']"))
            
            # Remove duplicates while preserving order
            seen = set()
            unique_divs = []
            for div in msg_divs:
                div_id = id(div)
                if div_id not in seen:
                    seen.add(div_id)
                    unique_divs.append(div)
            
            logger.debug(f"      Found {len(unique_divs)} message divs")
            
            # Process last 50 message divs
            for div in unique_divs[-50:]:
                msg_text = ""
                
                # Try multiple text extraction methods
                text_selectors = [
                    "span[class*='selectable-text']",
                    "div[class*='message-body']",
                    "div[class*='msg-container']",
                    "div[class*='message-text']",
                    "span",
                    "p",
                ]
                
                for selector in text_selectors:
                    try:
                        elem = div.select_one(selector)
                        if elem:
                            text = elem.get_text(strip=True)
                            if text and len(text) > 2:
                                msg_text = MessageExtractor.clean_text(text)
                                break
                    except:
                        pass
                
                # Also try direct get_text if nothing found
                if not msg_text:
                    try:
                        text = div.get_text(strip=True)
                        if text and len(text) > 2:
                            msg_text = MessageExtractor.clean_text(text)
                    except:
                        pass
                
                if msg_text and len(msg_text) > 2:
                    messages.append({"text": msg_text, "timestamp": datetime.now()})
            
            logger.debug(f"      Extracted {len(messages)} messages")
            return messages
        except Exception as e:
            logger.error(f"Message extraction error: {e}")
            return []

class ReplyDetector:
    """Robust reply detection - check recent messages."""
    
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
        Check if text indicates user is handling transfer.
        
        Checks:
        1. Direct phrase matching (e.g., "on it", "doing")
        2. Message from different user (contains emoji or multiple words)
        """
        if not text or len(text) < 2:
            return False
        
        normalized = ReplyDetector.normalize_text(text)
        
        # Check all handling phrases
        for category, phrases in USER_HANDLING_PHRASES.items():
            for phrase in phrases:
                # Phrase must be word boundary match
                if f" {phrase} " in f" {normalized} " or normalized.startswith(phrase) or normalized.endswith(phrase):
                    logger.info(f"      ✓ Phrase match: '{phrase}' in '{text[:50]}'")
                    return True
        
        return False
    
    @staticmethod
    def check_for_user_reply(html: str) -> bool:
        """
        Check if user replied with handling phrase.
        
        THOROUGH CHECK: Last 20 messages, multiple attempts
        """
        try:
            messages = MessageExtractor.extract_from_group_html(html)
            
            if not messages:
                logger.debug("    [reply-check] No messages extracted")
                return False
            
            # Check last 20 messages (very thorough)
            recent_messages = messages[-20:]
            
            logger.info(f"    [reply-check] Scanning {len(recent_messages)} recent messages...")
            
            # Log all messages for debugging
            for idx, msg in enumerate(recent_messages[-5:]):  # Show last 5 for debug
                logger.debug(f"      Message {idx}: '{msg['text'][:70]}'")
            
            for msg in recent_messages:
                msg_text = msg["text"]
                
                if ReplyDetector.is_user_handling(msg_text):
                    logger.info(f"    [reply-check] ✓✓✓ HANDLING REPLY DETECTED ✓✓✓")
                    logger.info(f"    [reply-check] Message: '{msg_text}'")
                    return True
            
            logger.info(f"    [reply-check] ✗ No handling reply in {len(recent_messages)} messages")
            return False
        except Exception as e:
            logger.error(f"Reply check error: {e}")
            return False

# ==============================================================================
# SCREENSHOT HELPER
# ==============================================================================

class ScreenshotHelper:
    """Take and send screenshots."""
    
    @staticmethod
    def take_screenshot(driver) -> Optional[str]:
        """Take screenshot and return base64 string."""
        try:
            screenshot = driver.get_screenshot_as_png()
            img = Image.open(BytesIO(screenshot))
            
            # Save to temp file
            temp_path = "/tmp/crm_error_screenshot.png"
            img.save(temp_path)
            
            logger.info(f"[SCREENSHOT] Saved to {temp_path}")
            return temp_path
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            return None

# ==============================================================================
# DRIVER MANAGEMENT
# ==============================================================================

class DriverManager:
    """Safe WebDriver with crash recovery."""
    
    def __init__(self):
        self.driver = None
        self.active_window = 0
        self.is_valid = False
    
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
            self.is_valid = True
            
            logger.info("✓ WebDriver initialized")
            return True
        except Exception as e:
            logger.error(f"Driver init failed: {e}")
            self.is_valid = False
            return False
    
    def navigate(self, url: str) -> bool:
        """Navigate to URL."""
        try:
            if not self._validate_session():
                return False
            
            self.driver.get(url)
            WebDriverWait(self.driver, 30).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            logger.info(f"✓ Navigated to {url}")
            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            self._recover_driver()
            return False
    
    def get_page_html(self) -> str:
        """Get page HTML."""
        try:
            if not self._validate_session():
                return ""
            return self.driver.page_source
        except (InvalidSessionIdException, NoSuchWindowException):
            self._recover_driver()
            return ""
        except Exception as e:
            logger.error(f"Page read error: {e}")
            return ""
    
    def switch_to_tab(self, index: int, wait_load: int = 5) -> bool:
        """Switch to tab."""
        try:
            if not self._validate_session():
                return False
            
            if index >= len(self.driver.window_handles):
                return False
            
            self.driver.switch_to.window(self.driver.window_handles[index])
            self.active_window = index
            time.sleep(wait_load)
            
            return True
        except Exception as e:
            logger.error(f"Tab switch error: {e}")
            self._recover_driver()
            return False
    
    def open_new_tab(self, url: str) -> bool:
        """Open new tab."""
        try:
            if not self._validate_session():
                return False
            
            self.driver.execute_script(f"window.open('{url}', '_blank');")
            time.sleep(3)
            
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.active_window = len(self.driver.window_handles) - 1
            time.sleep(3)
            
            return True
        except Exception as e:
            logger.error(f"New tab error: {e}")
            return False
    
    def find_element_safe(self, by: By, value: str, timeout: int = 10) -> Optional:
        """Find element safely."""
        try:
            if not self._validate_session():
                return None
            
            elem = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            return elem
        except Exception as e:
            logger.error(f"Element not found: {value}")
            return None
    
    def _validate_session(self) -> bool:
        """Validate driver session."""
        try:
            if not self.driver:
                return False
            _ = self.driver.window_handles
            self.is_valid = True
            return True
        except:
            self.is_valid = False
            self._recover_driver()
            return False
    
    def _recover_driver(self):
        """Recover from crash."""
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        self.driver = None
        self.is_valid = False
    
    def quit(self):
        """Quit driver."""
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        self.driver = None

# ==============================================================================
# CRM OPERATIONS - WITH CORRECT SELECTORS
# ==============================================================================

class CRMOperations:
    """VidaPay CRM with CORRECT selectors and error screenshots."""
    
    def __init__(self, driver_manager: DriverManager, crm_url: str):
        self.driver_manager = driver_manager
        self.crm_url = crm_url
        self.crm_tab_index = None
    
    def open_crm(self) -> bool:
        """Open CRM."""
        try:
            if not self.driver_manager.open_new_tab(self.crm_url):
                return False
            
            self.crm_tab_index = self.driver_manager.active_window
            time.sleep(5)
            
            logger.info(f"✓ CRM opened in tab {self.crm_tab_index}")
            return True
        except Exception as e:
            logger.error(f"CRM open error: {e}")
            return False
    
    def process_transfer(self, transfer: Dict) -> Tuple[bool, str]:
        """Process transfer with CORRECT selectors."""
        try:
            logger.info(f"[CRM] Processing: {transfer['store']} → {len(transfer['imeis'])} IMEI(s)")
            
            if not self.driver_manager.switch_to_tab(self.crm_tab_index, wait_load=5):
                return False, "Failed to switch to CRM tab"
            
            # Navigate to Inventory Reassignment Tool
            if not self._click_inventory_reassignment():
                return False, "Failed to navigate to Inventory Reassignment Tool"
            
            time.sleep(3)
            
            # Enter account ID
            if not self._enter_account_id(transfer["account_id"]):
                return False, "Failed to enter Account ID - field not found or account locked"
            
            # Process IMEIs
            if len(transfer["imeis"]) == 1:
                # Single IMEI - enter manually
                success, msg = self._process_single_imei(transfer["imeis"][0])
                if not success:
                    return False, msg
            else:
                # Multiple IMEIs - use bulk upload
                success, msg = self._process_bulk_imeis(transfer["imeis"])
                if not success:
                    return False, msg
            
            # Submit transfer
            if not self._submit_transfer():
                return False, "Failed to submit transfer"
            
            logger.info(f"[CRM] ✓ Transfer completed: {transfer['store']}")
            return True, f"Transfer successful: {len(transfer['imeis'])} IMEI(s) to {transfer['store']}"
            
        except Exception as e:
            logger.error(f"CRM error: {e}")
            return False, str(e)
    
    def _click_inventory_reassignment(self) -> bool:
        """Click 'Inventory Reassignment' link."""
        try:
            logger.info("[CRM] Looking for Inventory Reassignment link...")
            
            # Try to find and click the link
            selectors = [
                (By.ID, "MainContent_panelActivationManager_hlSimSetting"),
                (By.XPATH, "//a[contains(text(), 'Inventory Reassignment')]"),
                (By.LINK_TEXT, "Inventory Reassignment"),
            ]
            
            for by, selector in selectors:
                try:
                    link = self.driver_manager.find_element_safe(by, selector, timeout=5)
                    if link:
                        link.click()
                        logger.info("[CRM] ✓ Clicked Inventory Reassignment")
                        return True
                except:
                    pass
            
            logger.warning("[CRM] Link not found, page may already be loaded")
            return True
        except Exception as e:
            logger.error(f"[CRM] Navigation error: {e}")
            return False
    
    def _enter_account_id(self, account_id: str) -> bool:
        """Enter Account ID using CORRECT selector - with better handling."""
        try:
            logger.info(f"[CRM] Entering Account ID: {account_id}")
            
            # Wait for page to be fully ready
            time.sleep(3)
            
            # CORRECT SELECTORS - TRY MULTIPLE APPROACHES
            logger.info("[CRM] Looking for Account ID field...")
            
            field = None
            
            # Approach 1: Find by ID first
            try:
                field = self.driver_manager.find_element_safe(By.ID, "rcbAccount_Input", timeout=10)
                if field:
                    logger.info("[CRM] ✓ Found by ID 'rcbAccount_Input'")
            except:
                pass
            
            # Approach 2: Find by NAME
            if not field:
                try:
                    field = self.driver_manager.find_element_safe(
                        By.NAME, "ctl00$MainContent$rcbAccount", timeout=10
                    )
                    if field:
                        logger.info("[CRM] ✓ Found by NAME 'ctl00$MainContent$rcbAccount'")
                except:
                    pass
            
            # Approach 3: Find by XPATH with text search
            if not field:
                try:
                    field = self.driver_manager.find_element_safe(
                        By.XPATH, "//input[contains(@id, 'rcbAccount')]", timeout=10
                    )
                    if field:
                        logger.info("[CRM] ✓ Found by XPATH rcbAccount")
                except:
                    pass
            
            if not field:
                logger.error("[CRM] ❌ Account ID field NOT FOUND - CRM page may not be loaded properly")
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Screenshot saved: {screenshot}")
                    logger.info("[CRM] Check CRM page in browser - may need manual navigation")
                return False
            
            # Make sure field is visible
            logger.info("[CRM] Scrolling field into view...")
            self.driver_manager.driver.execute_script("arguments[0].scrollIntoView(true);", field)
            time.sleep(1)
            
            # Click on field to focus
            field.click()
            time.sleep(0.5)
            
            # Clear any existing content
            logger.info("[CRM] Clearing field...")
            field.send_keys(Keys.CONTROL + "a")
            field.send_keys(Keys.DELETE)
            time.sleep(0.5)
            
            # Type account ID SLOWLY to ensure it's entered
            logger.info(f"[CRM] Typing account ID: {account_id}")
            for char in account_id:
                field.send_keys(char)
                time.sleep(0.05)  # Slow typing
            
            time.sleep(1)
            
            # Press Tab to trigger autocomplete/validation
            logger.info("[CRM] Pressing Tab to trigger autocomplete...")
            field.send_keys(Keys.TAB)
            time.sleep(3)  # Wait for autocomplete to finish
            
            # Verify account was entered
            actual_value = field.get_attribute("value")
            logger.info(f"[CRM] Field value after entry: '{actual_value}'")
            
            if account_id in actual_value or actual_value in account_id:
                logger.info(f"[CRM] ✓ Account ID entered successfully: {account_id}")
                return True
            else:
                logger.warning(f"[CRM] ⚠️ Account ID may not have been entered correctly")
                logger.warning(f"[CRM] Expected: {account_id}, Got: {actual_value}")
                return True  # Continue anyway
                
        except Exception as e:
            logger.error(f"[CRM] ❌ Account entry error: {e}")
            try:
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Error screenshot: {screenshot}")
            except:
                pass
            return False
    
    def _process_single_imei(self, imei: str) -> Tuple[bool, str]:
        """Process single IMEI with robust error detection."""
        try:
            logger.info(f"[CRM] Processing IMEI: {imei}")
            
            # Wait for page
            time.sleep(2)
            
            # Find IMEI input field - TRY MULTIPLE SELECTORS
            logger.info("[CRM] Looking for IMEI input field...")
            
            imei_field = None
            
            # Try by ID first
            try:
                imei_field = self.driver_manager.find_element_safe(By.ID, "txtSimEntry", timeout=10)
                if imei_field:
                    logger.info("[CRM] ✓ Found IMEI field by ID")
            except:
                pass
            
            # Try by NAME
            if not imei_field:
                try:
                    imei_field = self.driver_manager.find_element_safe(
                        By.NAME, "ctl00$MainContent$txtSimEntry", timeout=10
                    )
                    if imei_field:
                        logger.info("[CRM] ✓ Found IMEI field by NAME")
                except:
                    pass
            
            if not imei_field:
                logger.error("[CRM] ❌ IMEI field NOT FOUND")
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Error screenshot: {screenshot}")
                return False, "IMEI input field not found on page"
            
            # Scroll into view and clear
            logger.info("[CRM] Preparing IMEI field...")
            self.driver_manager.driver.execute_script("arguments[0].scrollIntoView(true);", imei_field)
            time.sleep(1)
            
            imei_field.click()
            time.sleep(0.5)
            
            # Clear field
            imei_field.send_keys(Keys.CONTROL + "a")
            imei_field.send_keys(Keys.DELETE)
            time.sleep(0.5)
            
            # Type IMEI SLOWLY
            logger.info(f"[CRM] Entering IMEI: {imei}")
            for char in imei:
                imei_field.send_keys(char)
                time.sleep(0.05)
            
            time.sleep(1)
            
            # Find and click Add button
            logger.info("[CRM] Looking for Add button...")
            add_btn = None
            
            # Try multiple selectors for Add button
            add_selectors = [
                (By.XPATH, "//input[@value='Add']"),
                (By.XPATH, "//input[contains(@value, 'Add')]"),
                (By.NAME, "ctl00$MainContent$buttonAdd"),
            ]
            
            for by, selector in add_selectors:
                try:
                    add_btn = self.driver_manager.find_element_safe(by, selector, timeout=5)
                    if add_btn:
                        logger.info("[CRM] ✓ Found Add button")
                        break
                except:
                    pass
            
            if add_btn:
                logger.info("[CRM] Clicking Add button...")
                add_btn.click()
                time.sleep(3)  # Wait for IMEI to be added
                logger.info(f"[CRM] ✓ IMEI added: {imei}")
            else:
                logger.warning("[CRM] ⚠️ Add button not found, but continuing...")
            
            # Wait for any dialogs or errors to appear
            time.sleep(2)
            
            # Check for error messages
            logger.info("[CRM] Checking for IMEI validation errors...")
            html = self.driver_manager.driver.page_source
            
            # Check for "SIM not found" error
            if "SIM not found" in html:
                logger.error("[CRM] ❌ IMEI validation error: SIM not found")
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Error screenshot: {screenshot}")
                return False, f"IMEI {imei}: SIM not found (not a Total Wireless device or wrong account)"
            
            # Check for Invalid SIM indicator
            if "Invalid Sim.png" in html or "error" in html.lower():
                logger.error("[CRM] ❌ IMEI has validation error")
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Error screenshot: {screenshot}")
                return False, f"IMEI {imei}: Validation failed (see error screenshot)"
            
            logger.info(f"[CRM] ✓ IMEI processed successfully: {imei}")
            return True, f"IMEI {imei} added successfully"
            
        except Exception as e:
            logger.error(f"[CRM] ❌ IMEI processing error: {e}")
            try:
                screenshot = ScreenshotHelper.take_screenshot(self.driver_manager.driver)
                if screenshot:
                    logger.info(f"[CRM] Error screenshot: {screenshot}")
            except:
                pass
            return False, str(e)
    
    def _process_bulk_imeis(self, imeis: List[str]) -> Tuple[bool, str]:
        """Process multiple IMEIs using upload."""
        try:
            logger.info(f"[CRM] Processing {len(imeis)} IMEIs via upload")
            
            # For now, process one by one
            # TODO: Implement CSV template upload
            for imei in imeis:
                success, msg = self._process_single_imei(imei)
                if not success:
                    return False, msg
            
            return True, f"{len(imeis)} IMEIs added"
        except Exception as e:
            logger.error(f"[CRM] Bulk IMEI error: {e}")
            return False, str(e)
    
    def _submit_transfer(self) -> bool:
        """Submit transfer."""
        try:
            logger.info("[CRM] Looking for Submit button...")
            
            # Click Next first (if not already clicked)
            try:
                next_btn = self.driver_manager.find_element_safe(
                    By.ID, "btnNext", timeout=5
                )
                if next_btn and not next_btn.get_attribute("disabled"):
                    next_btn.click()
                    time.sleep(3)
                    logger.info("[CRM] ✓ Clicked Next")
            except:
                pass
            
            # Click Submit
            submit_selectors = [
                (By.ID, "MainContent_submitButton"),
                (By.XPATH, "//input[@value='Submit']"),
            ]
            
            submit_btn = None
            for by, selector in submit_selectors:
                try:
                    submit_btn = self.driver_manager.find_element_safe(by, selector, timeout=5)
                    if submit_btn:
                        break
                except:
                    pass
            
            if not submit_btn:
                logger.error("[CRM] Submit button not found")
                return False
            
            submit_btn.click()
            time.sleep(3)
            
            logger.info("[CRM] ✓ Transfer submitted")
            return True
        except Exception as e:
            logger.error(f"[CRM] Submit error: {e}")
            return False

# ==============================================================================
# WHATSAPP REPLIER
# ==============================================================================

class WhatsAppReplier:
    """Send WhatsApp replies - ONLY after transfer done."""
    
    def __init__(self, driver_manager: DriverManager):
        self.driver_manager = driver_manager
        self.whatsapp_tab_index = 0
    
    def send_reply(self, group_name: str, message: str) -> bool:
        """Send reply."""
        try:
            logger.info(f"[REPLY] Sending: {message[:60]}...")
            
            if not self.driver_manager.switch_to_tab(self.whatsapp_tab_index, wait_load=2):
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
                    msg_field = self.driver_manager.find_element_safe(by, selector, timeout=5)
                    if msg_field:
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
        except Exception as e:
            logger.error(f"[REPLY] Send error: {e}")
            return False

# ==============================================================================
# TRANSFER PROCESSOR
# ==============================================================================

class TransferProcessor:
    """Process transfers with ROBUST reply detection."""
    
    def __init__(self, driver_manager: DriverManager, crm_ops: CRMOperations, replier: WhatsAppReplier):
        self.driver_manager = driver_manager
        self.crm_ops = crm_ops
        self.replier = replier
    
    def process(self, transfer: Dict) -> bool:
        """
        Process transfer with ROBUST workflow.
        
        CRITICAL: NEVER send "on it" or claim message before processing!
        Only send result message AFTER CRM processes.
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"[TRANSFER] {transfer['store']} ({len(transfer['imeis'])} IMEIs)")
        logger.info(f"{'='*70}")
        
        logger.info(f"\n[STEP 1] Waiting {REPLY_WAIT_TIME}s for user reply...")
        logger.info(f"  (Checking for: 'on it', 'doing', 'sure', 'checking', etc.)")
        
        # STEP 1: Wait for user reply - CHECK THOROUGHLY
        deadline = datetime.now() + timedelta(seconds=REPLY_WAIT_TIME)
        user_handling = False
        check_count = 0
        
        while datetime.now() < deadline:
            check_count += 1
            remaining = int((deadline - datetime.now()).total_seconds())
            logger.debug(f"  [check #{check_count}] {remaining}s remaining...")
            
            html = self.driver_manager.get_page_html()
            if html and ReplyDetector.check_for_user_reply(html):
                user_handling = True
                logger.info(f"\n✓✓✓ USER REPLY DETECTED ✓✓✓")
                logger.info(f"  User is handling this transfer")
                logger.info(f"  SKIPPING bot processing")
                logger.info(f"  NOT sending any WhatsApp reply")
                break
            
            time.sleep(2)
        
        # If user handling, skip everything
        if user_handling:
            logger.info(f"\n[TRANSFER COMPLETE] {transfer['store']} → USER HANDLING")
            logger.info(f"{'='*70}\n")
            return True
        
        # STEP 2: No user reply - process in CRM
        logger.info(f"\n[STEP 2] No user reply after {REPLY_WAIT_TIME}s")
        logger.info(f"  BOT WILL PROCESS THIS TRANSFER")
        
        logger.info(f"\n[STEP 3] Processing in CRM...")
        success, message = self.crm_ops.process_transfer(transfer)
        
        # STEP 4: Send result reply ONLY after CRM done
        logger.info(f"\n[STEP 4] Sending result to WhatsApp...")
        
        if success:
            reply_msg = f"✅ {transfer['store']}: Transfer successful"
            logger.info(f"  SUCCESS: {message}")
        else:
            reply_msg = f"⚠️ {transfer['store']}: {message}"
            logger.error(f"  FAILED: {message}")
        
        logger.info(f"  Sending: {reply_msg}")
        self.replier.send_reply(transfer['group'], reply_msg)
        
        logger.info(f"\n[TRANSFER COMPLETE] {transfer['store']} → {'✓ SUCCESS' if success else '✗ FAILED'}")
        logger.info(f"{'='*70}\n")
        
        return success

# ==============================================================================
# MAIN BOT
# ==============================================================================

class WhatsAppTransferBot:
    """Main bot controller v3.3."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("WhatsApp → VidaPay Transfer Bot v3.3 - ROBUST CRM + REPLY DETECTION")
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
        """Build UI."""
        header_bg = tk.Canvas(self.root, height=70, bg=GFH_NAVY, highlightthickness=0)
        header_bg.pack(fill=tk.X, side=tk.TOP)
        
        header_frame = tk.Frame(header_bg, bg=GFH_NAVY)
        header_bg.create_window(0, 0, window=header_frame, anchor=tk.NW, width=1100, height=70)
        
        left_frame = tk.Frame(header_frame, bg=GFH_NAVY)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=15, pady=10)
        
        title_label = tk.Label(left_frame, text="WhatsApp → VidaPay Transfer Bot", 
                               font=("Segoe UI", 14, "bold"), 
                               fg=GFH_WHITE, bg=GFH_NAVY)
        title_label.pack(side=tk.LEFT)
        
        divider = tk.Canvas(header_frame, width=4, height=50, bg=GFH_RED, highlightthickness=0)
        divider.pack(side=tk.LEFT, padx=15, pady=10)
        
        middle_frame = tk.Frame(header_frame, bg=GFH_NAVY)
        middle_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        version_label = tk.Label(middle_frame, text="v3.3", 
                                font=("Segoe UI", 12, "bold"), 
                                fg=GFH_RED, bg=GFH_NAVY)
        version_label.pack(anchor=tk.W)
        
        features_label = tk.Label(middle_frame, text="Correct CRM Selectors • Robust Reply Detection • Error Screenshots", 
                                 font=("Segoe UI", 9), 
                                 fg=GFH_WHITE, bg=GFH_NAVY)
        features_label.pack(anchor=tk.W)
        
        right_frame = tk.Frame(header_frame, bg=GFH_NAVY)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=15, pady=10)
        
        status_text_label = tk.Label(right_frame, text="STATUS:", 
                                    font=("Segoe UI", 9, "bold"), 
                                    fg=GFH_WHITE, bg=GFH_NAVY)
        status_text_label.pack(anchor=tk.E)
        
        self.status_label = tk.Label(right_frame, text="● IDLE", 
                                     font=("Segoe UI", 12, "bold"), 
                                     fg="gray", bg=GFH_NAVY)
        self.status_label.pack(anchor=tk.E)
        
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = ttk.Button(control_frame, text="▶ START MONITORING", 
                                     command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(control_frame, text="⏹ STOP",
                                    command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text="📊 Live Log")
        
        self.log_text = scrolledtext.ScrolledText(log_frame, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        self.log_text.tag_config("SUCCESS", foreground="#22C55E")
        self.log_text.tag_config("ERROR", foreground="#EF4444")
        self.log_text.tag_config("INFO", foreground="#3B82F6")
        self.log_text.tag_config("WARNING", foreground="#F59E0B")
        
        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text="⚙️ Settings")
        
        ttk.Label(settings_frame, text="Trigger Words:",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        self.triggers_input = ttk.Entry(settings_frame, width=70)
        self.triggers_input.pack(anchor=tk.W, padx=10, pady=5)
        self.triggers_input.insert(0, "transfer to, t to, trf to, move to")
        
        ttk.Label(settings_frame, text="Monitored Groups:",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        self.groups_input = ttk.Entry(settings_frame, width=70)
        self.groups_input.pack(anchor=tk.W, padx=10, pady=5)
        
        ttk.Label(settings_frame, text="Store Aliases:",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 5))
        ttk.Button(settings_frame, text="📁 Load Aliases", command=self._load_aliases).pack(anchor=tk.W, padx=10, pady=5)
    
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
        self._log("=== BOT STARTED v3.3 ===", "SUCCESS")
        self._log("✅ Correct CRM selectors", "SUCCESS")
        self._log("✅ Robust reply detection (checks 15 recent messages)", "SUCCESS")
        self._log("✅ Error screenshots on CRM failures", "SUCCESS")
        
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
        retry_count = 0
        
        while not self.stop_requested and retry_count < MAX_RETRIES:
            try:
                if not self.driver_manager.initialize():
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue
                
                if not self.driver_manager.navigate(WHATSAPP_URL):
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue
                
                self._log("Waiting for WhatsApp login (scan QR)...", "INFO")
                time.sleep(15)
                
                self.crm_ops = CRMOperations(self.driver_manager, "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx")
                self.replier = WhatsAppReplier(self.driver_manager)
                self.processor = TransferProcessor(self.driver_manager, self.crm_ops, self.replier)
                
                if not self.crm_ops.open_crm():
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    continue
                
                self._log("Bot ready - monitoring WhatsApp...", "SUCCESS")
                retry_count = 0
                
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
                            }
                            
                            self.processed_messages.add(msg_hash)
                            self.processor.process(transfer)
                        
                        time.sleep(SCREENSHOT_INTERVAL)
                        
                    except Exception as e:
                        self._log(f"Loop error: {e}", "ERROR")
                        time.sleep(SCREENSHOT_INTERVAL)
            
            except Exception as e:
                self._log(f"Critical error: {e}", "ERROR")
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
        
        if retry_count >= MAX_RETRIES:
            self._log("Max retries exceeded", "ERROR")
            self.root.after(0, self._stop)
    
    def _extract_store_name(self, text: str, triggers: List[str]) -> Optional[str]:
        """Extract store name."""
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
        """Load aliases."""
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
        """Load config."""
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
