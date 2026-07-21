# Hero SMS Automation - Portable PC Transfer & Setup Guide

This guide explains how to package and move the entire system to another Windows PC.

---

## Step 1: Prepare & Zip the Folder

1. **Delete the `.venv` folder** in `hero` before compressing (or exclude it when zipping).
   * *Why?* Virtual environments store hardcoded file paths from your current laptop. `run.bat` will automatically recreate a fresh `.venv` on the new PC.
2. Select all files in `c:\Users\L\Desktop\hero` and compress them into **`hero.zip`**.

### Folders to INCLUDE:
* `chrome_profiles/` (Contains saved active Facebook sessions)
* `chrome_profiles_fb/`
* `app.py`
* `hero_sms_automation.py`
* `clean_unused_profiles.py`
* `run.bat`
* `requirements.txt`
* `config.json`

---

## Step 2: Transfer & Extract on the New PC

1. Copy **`hero.zip`** to the new Windows PC (e.g., paste it onto the Desktop or `C:\Users\<Name>\Desktop\hero`).
2. Right-click **`hero.zip`** and select **Extract All...**.

---

## Step 3: One-Click Launch

1. Double-click **`run.bat`**.
2. `run.bat` will execute the following setup sequence automatically:
   - Checks for Python 3.10+ (if missing, it automatically installs Python for you).
   - Creates a fresh `.venv` environment.
   - Installs all dependencies from `requirements.txt`.
   - Downloads Playwright Chromium browser drivers.
   - **Automatically opens your web browser** straight to the Web UI Dashboard at **`http://127.0.0.1:5000`**.

---

## Step 4: VPN Setup on New PC (Surfshark)

1. Install **Surfshark** on the new PC.
2. Log in and enable **IP Rotator** in Surfshark settings.
3. Connect to your preferred VPN server location before starting the script.
