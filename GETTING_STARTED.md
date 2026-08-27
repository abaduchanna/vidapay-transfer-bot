# WhatsApp Transfer Bot v2.0 - Getting Started Guide

**Developed by Abad Umair Channa | Copyright © 2026 | All rights reserved.**

---

## ⚡ Quick Start (5 minutes)

### Step 1: Install Dependencies
```bash
pip install -r requirements_fixed.txt
```

### Step 2: Copy Example Configs
```bash
cp config_example.json config.json
cp aliases_example.json store_aliases.json
```

### Step 3: Run Bot
```bash
python whatsapp_transfer_bot_fixed.py
```

### Step 4: Login to WhatsApp
- Browser opens with WhatsApp Web
- Scan QR code on your phone
- Wait for login to complete (app will auto-detect)

### Step 5: Configure & Start
1. Settings tab → Set trigger words (or use defaults)
2. Settings tab → Set monitored groups
3. Settings tab → Load Aliases (store_aliases.json)
4. Click **▶ START** button
5. Bot now monitors for transfer requests

---

## 📋 Configuration Files

### `config.json`
Contains bot settings. Copy from `config_example.json`:

```json
{
  "triggers": [
    "transfer to",
    "t to",
    "trf to"
  ],
  "groups": [
    "GFH TELECOM HOUSTON",
    "GFH TELECOM ARIZONA"
  ],
  "crm": {
    "url": "https://www.vidapaycrm.com/InventoryReassignmentTool.aspx",
    "timeout": 30
  },
  "whatsapp": {
    "check_interval": 10,
    "headless": false
  }
}
```

**Key Fields:**
- `triggers`: Words that trigger transfer processing
- `groups`: WhatsApp groups to monitor
- `crm.url`: Your VidaPay CRM URL
- `crm.timeout`: Max wait time for CRM operations (seconds)
- `whatsapp.check_interval`: How often to check for messages (seconds)

### `store_aliases.json`
Maps store names to Account IDs. Copy from `aliases_example.json`:

```json
{
  "aliases": {
    "windermere": "166839_GFH Telecom LLC",
    "downtown": "166840_GFH Telecom LLC",
    "houston": "166841_GFH Telecom LLC"
  }
}
```

**How it works:**
1. User message: "transfer to Windermere Store - 357612117960162"
2. Bot extracts store: "Windermere Store"
3. Bot looks in aliases: "windermere" → `166839_GFH Telecom LLC`
4. Uses Account ID `166839_GFH Telecom LLC` in CRM

**To add new stores:**
1. Open `store_aliases.json`
2. Add line: `"store_name_lowercase": "ACCOUNT_ID_FROM_CRM"`
3. Save file
4. Restart bot (or reload in Settings tab)

---

## 📱 How It Works

### User sends message in WhatsApp:
```
"transfer to Windermere - 357612117960162 and 351234567890123"
```

### Bot detects:
1. ✓ Trigger word detected: "transfer to"
2. ✓ Store name extracted: "Windermere"
3. ✓ IMEIs extracted: 357612117960162, 351234567890123
4. ✓ Account ID resolved: 166839_GFH Telecom LLC
5. ✓ Added to transfer queue

### Bot processes transfer:
1. Opens CRM in new tab
2. Navigates to Inventory Reassignment tool
3. Enters Account ID: 166839_GFH Telecom LLC
4. Enters each IMEI one by one
5. Submits transfer
6. Checks for confirmation message

### On success:
- Updates queue: ✓ Windermere (2 IMEI) - COMPLETED
- Sends WhatsApp reply: "✅ Transferred 2 devices to Windermere"
- Continues monitoring

### On error:
- Logs error with details
- Sends WhatsApp reply: "⚠️ Transfer failed: [error details]"
- Continues monitoring (doesn't crash)

---

## 🔍 GUI Tabs Explained

### 📊 **Log Tab**
Real-time activity log with color coding:
- 🟢 **GREEN** = Success (transfer completed, login OK)
- 🔴 **RED** = Error (transfer failed, CRM error)
- 🔵 **BLUE** = Info (normal operations, checking for messages)
- 🟡 **YELLOW** = Warning (recoverable issues, retrying)

**Example log:**
```
[15:29:38] === MONITORING STARTED ===
[15:29:38] Triggers: transfer to, t to, trf to, tt, t t
[15:29:38] Groups: GFH TELECOM HOUSTON, All district management 2.0
[15:29:48] No unread messages in monitored groups.
[15:30:22] ✓ Transfer: GFH TELECOM HOUSTON → Windermere (1 IMEI)
[15:31:07] VidaPay Reassignment Tool ready.
[15:31:07] Processing: Windermere Store → 357612117960162
[15:31:20] ✓ Completed: Windermere Store
[15:31:22] Reply sent to 'GFH TELECOM HOUSTON': ✅ Transfer successful!
```

### ⚙️ **Settings Tab**
Configure bot behavior:

1. **Trigger Words**: What words trigger transfer processing
   - Default: "transfer to, t to, trf to, tt, t t"
   - You can add/remove as needed
   - Example: "send to, move to, reassign to"

2. **Monitored Groups**: Which WhatsApp groups to watch
   - List all group names separated by commas
   - Bot checks these groups every 10 seconds
   - Example: "GFH TELECOM HOUSTON, Inventory Transfers"

3. **Load Aliases**: Upload store mapping JSON
   - Click button → select `store_aliases.json`
   - Updates store-to-Account-ID mappings
   - No restart needed (live update)

### 📦 **Queue Tab**
Shows all transfer requests processed:

```
1. GFH TELECOM HOUSTON → Windermere (1 IMEI)
2. GFH TELECOM ARIZONA → Downtown (3 IMEI)
3. All district management 2.0 → Houston (2 IMEI)
```

---

## 🚀 Control Buttons

### ▶️ **START MONITORING**
- Opens browser with WhatsApp Web
- Waits for login (scan QR code)
- Enters continuous monitoring loop
- Checks every 10 seconds for new messages
- **Status**: Green "● MONITORING"

### ⏹️ **STOP MONITORING**
- Stops checking for messages
- Closes any open transfers
- Closes browser
- Safely shuts down
- **Status**: Red "● STOPPED"

### ⚙️ **Settings**
- Opens Settings tab
- Configure trigger words, groups, aliases
- Changes take effect immediately

---

## ❌ Troubleshooting

### Problem: "No unread messages" even when messages arrive
**Solution**: WhatsApp Web DOM structure changed
1. Open WhatsApp Web manually
2. Right-click unread badge (green circle with number)
3. Select "Inspect"
4. Note the HTML element class
5. Update `UNREAD_BADGE_SELECTORS` in code

### Problem: Transfer request not detected
**Check 1**: Message contains trigger word?
```
Good: "transfer to Windermere - 357612117960162"
Bad: "Windermere 357612117960162" (no trigger)
```

**Check 2**: Message contains 15-digit IMEI starting with 35 or 01?
```
Good: "357612117960162" (15 digits, starts with 35)
Bad: "9876543210" (too short)
Bad: "123456789012345" (starts with 1, not 01)
```

**Check 3**: Store name in aliases?
```
Check store_aliases.json
Add missing stores:
  "windermere": "ACCOUNT_ID_FROM_CRM"
```

### Problem: Bot crashes when processing transfer
**This should NOT happen in v2.0!** But if it does:
1. Check Log tab for error message
2. Bot auto-recovers (retries up to 3 times)
3. If still failing, stop bot and restart

### Problem: WhatsApp login not detecting
**Solution 1**: Wait longer for QR code
- Some networks are slow
- Wait 30 seconds after starting bot

**Solution 2**: Scan QR code manually
- Open WhatsApp Web in your browser
- Scan with phone camera
- Complete login

**Solution 3**: Check if WhatsApp already logged in
- Open Edge browser manually
- Navigate to `https://web.whatsapp.com`
- If already logged in, bot will detect it

### Problem: CRM login fails
**Check**: VidaPay CRM credentials
1. Test login manually in browser
2. Verify Account ID format matches CRM
3. Check CRM URL in config.json

---

## 📊 Log File Format

Each run creates detailed logs:

```
[15:29:38] === MONITORING STARTED ===
[15:29:38] Triggers: transfer to, t to, trf to, tt, t t
[15:29:38] Groups: GFH TELECOM HOUSTON, All district management 2.0
[15:29:48] No unread messages in monitored groups.
[15:29:58] Groups with unread: ['GFH TELECOM HOUSTON']
[15:30:01] Searching for group: 'GFH TELECOM HOUSTON'
[15:30:05]   BS4 extracted 5 messages (skipped Selenium selector fallback).
[15:30:05] [GFH TELECOM HOUSTON] Found 5 recent incoming messages.
[15:30:07] ✓ Transfer: GFH TELECOM HOUSTON → Windermere (1 IMEI)
[15:30:22] Processing: Windermere Store → 357612117960162
[15:30:30] Sent claim reply "on it" to 'GFH TELECOM HOUSTON'.
[15:31:07] VidaPay Reassignment Tool ready.
[15:31:07] Initiating transfer to Account ID: 166839_GFH Telecom LLC for 1 devices.
[15:31:20] ✓ Transfer completed successfully.
[15:31:22] Reply sent to 'GFH TELECOM HOUSTON': ✅ Transfer successful!
```

---

## 🔧 Advanced: Custom Trigger Words

### Current defaults:
```json
"triggers": [
  "transfer to",
  "t to",
  "trf to",
  "move to",
  "send to"
]
```

### To add custom triggers:
1. Edit `config.json`
2. Add new trigger to list
3. Save and restart bot

**Example: Add "reassign to"**
```json
"triggers": [
  "transfer to",
  "t to",
  "trf to",
  "reassign to"
]
```

### Trigger word tips:
- Use common abbreviations (t to, trf to)
- Be specific to avoid false matches
- All matching is case-insensitive
- Bot checks if trigger appears ANYWHERE in message

---

## 🔐 Security Notes

**Store credentials securely:**
- `config.json` contains CRM credentials - don't commit to GitHub
- `store_aliases.json` is safe to share (no secrets)
- Keep your Account IDs confidential
- Don't share WhatsApp session with others

---

## 📈 Performance Tips

1. **Fewer monitored groups** = Faster checking
2. **Shorter check interval** = More responsive (but more CPU usage)
3. **Smaller trigger word list** = Faster matching
4. **Fewer aliases** = Faster store lookup

---

## 📞 Support

**For issues:**
1. Check Log tab for error messages
2. Search troubleshooting section above
3. Restart bot (click STOP then START)
4. Check `store_aliases.json` for typos
5. Verify CRM URL is correct

---

**Version**: 2.0  
**Last Updated**: 2026-01-15  
**Status**: ✅ Production Ready
