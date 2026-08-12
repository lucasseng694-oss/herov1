import os
import sys
import json
import re
import queue
import subprocess
import threading
import time
from flask import Flask, Response, jsonify, request, render_template_string

app = Flask(__name__)

# Global state to keep track of process and logs
log_queue = queue.Queue(maxsize=5000)
process = None
thread = None
is_running = False
session_start_time = None
session_stop_requested_time = None

# Location of config and output files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "recovered_accounts.txt")

# Default config dict
DEFAULT_CONFIG = {
    "chrome_debug_url": "http://127.0.0.1:9222",
    "chrome_profile_name": "Profile 1",
    "multiple_accounts": False,
    "service_text": "Facebook",
    "country_text": "Brazil",
    "buy_text": "Buy for $0.099",
    "min_price": 0.01,
    "max_price": 0.15,
    "target_url": "https://www.facebook.com/login/identify/?ci=AdDhNqxj3bubeKaJl2BAeZF5R84lr1pqkL5Cf2GECCYUaKqqwnbEqH8-EmPr5ktGAoEAQ36l_A8pTW5y1b-Bnht-2xbUC9edHV1cW7O-udVnmbHAM1ZPy-PZmgDLgOHviiToDLhpwlAxn0WywiZA6Y8Wyn-_n9oildrH-L31Wc8bBkOgGVO7udBoB-1zGQIygpu91hfBLtBXTilJ4JnkELUeSBYYFPVtNmnr6RLDvSZ2acPoDdiDrnAOgQOAsKE15PE6ztB0mkwvJZO-LZYfUJXpRt1u",
    "new_password": "HeroSmsRecover123!",
    "confirm_before_buy": True,
    "auto_login": False,
    "hero_username": "",
    "hero_password": "",
    "vpn_connection_name": "",
    "control_phone_number": "",
    "claudeotp_api_key": "",
    "claudeotp_service": 544,
    "claudeotp_country": "ID",
    "sms_provider": "hero-sms"
}

def load_config_data():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge defaults for any missing keys
            config = DEFAULT_CONFIG.copy()
            config.update(data)
            return config
        except Exception:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

def save_config_data(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except Exception:
        return False

def parse_recovered_accounts():
    accounts = []
    if os.path.exists(ACCOUNTS_PATH):
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Accounts are separated by "--- Recovered Account [...] ---" header
            # Table row is like: UID | Password | 2FA | Cookie
            entries = content.split("--- Recovered Account")
            for entry in entries:
                if not entry.strip():
                    continue
                lines = [l.strip() for l in entry.strip().split("\n") if l.strip()]
                if not lines:
                    continue
                
                # First line is header remnant like: " [2026-07-14 12:43:00 | Profile 1 ] ---"
                header_line = lines[0]
                date_time = "Unknown"
                profile_name = "Unknown"
                
                # Extract date/time and profile
                meta_match = re.search(r'\[(.*?)\]', header_line) if 're' in sys.modules else None
                # We can also just parse manually
                if "[" in header_line and "]" in header_line:
                    meta_content = header_line.split("[")[1].split("]")[0]
                    if "|" in meta_content:
                        date_time, profile_name = [x.strip() for x in meta_content.split("|")]
                    else:
                        date_time = meta_content.strip()
                
                # The next line contains the actual account pipe-separated details
                for line in lines[1:]:
                    if "|" in line:
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 4:
                            uid = parts[0]
                            password = parts[1]
                            two_fa = parts[2]
                            cookie = "|".join(parts[3:]) # handle cases where cookie itself has pipes
                            accounts.append({
                                "uid": uid,
                                "password": password,
                                "two_fa": two_fa if two_fa else "None",
                                "cookie": cookie,
                                "date": date_time,
                                "profile": profile_name
                            })
                            break
        except Exception as e:
            print(f"Error parsing accounts file: {e}")
    # Return reversed list so the newest entries are at the top
    return accounts[::-1]


def recalculate_recovered_counts():
    try:
        accounts = parse_recovered_accounts()
        counts = {}
        for acc in accounts:
            if "date" in acc and acc["date"] != "Unknown":
                date_part = acc["date"].split(" ")[0]
                counts[date_part] = counts.get(date_part, 0) + 1
                
        stats_path = os.path.join(BASE_DIR, "recovered_stats.json")
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
                                "failed_logins": 0,
                                "success_duration": 0
                            }
                except:
                    pass
                    
        updated = False
        for date, count in counts.items():
            entry = data.setdefault(date, {"recovered": 0, "spent": 0.0, "duration": 0, "failed_logins": 0, "success_duration": 0})
            if not isinstance(entry, dict):
                entry = {"recovered": int(entry), "spent": 0.0, "duration": 0, "failed_logins": 0, "success_duration": 0}
                data[date] = entry
            entry.setdefault("failed_logins", 0)
            entry.setdefault("success_duration", 0)
            if entry["recovered"] != count:
                entry["recovered"] = count
                updated = True
                
        if updated:
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Could not synchronize stats: {e}")


def update_duration_in_stats(start_time: float, end_time: float, is_success: bool = False) -> None:
    try:
        from datetime import datetime, timedelta
        import json
        
        stats_path = os.path.join(BASE_DIR, "recovered_stats.json")
        
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
                                "failed_logins": 0,
                                "success_duration": 0
                            }
                except:
                    pass
                    
        # Split elapsed duration day-by-day to prevent midnight timezone bleed
        current_time = start_time
        while current_time < end_time:
            dt = datetime.fromtimestamp(current_time)
            current_date_str = dt.strftime("%Y-%m-%d")
            
            # Find start of next day in local time
            next_day_dt = datetime(dt.year, dt.month, dt.day) + timedelta(days=1)
            next_day_timestamp = next_day_dt.timestamp()
            
            chunk_end = min(next_day_timestamp, end_time)
            chunk_seconds = int(chunk_end - current_time)
            
            entry = data.setdefault(current_date_str, {"recovered": 0, "spent": 0.0, "duration": 0, "failed_logins": 0, "success_duration": 0})
            if not isinstance(entry, dict):
                entry = {"recovered": int(entry), "spent": 0.0, "duration": 0, "failed_logins": 0, "success_duration": 0}
                data[current_date_str] = entry
                
            entry.setdefault("failed_logins", 0)
            entry.setdefault("success_duration", 0)
            
            entry["duration"] = entry.get("duration", 0) + chunk_seconds
            if is_success:
                entry["success_duration"] = entry.get("success_duration", 0) + chunk_seconds
                
            current_time = chunk_end
            
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Could not record duration from app.py: {e}")


session_recovered_baseline = 0

def run_automation_process():
    global process, is_running, session_start_time, session_recovered_baseline
    is_running = True
    session_start_time = time.time()
    log_queue.put("[SYSTEM] Spawning browser automation process...\n")
    
    python_exec = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if not os.path.exists(python_exec):
        python_exec = "python"
        
    start_accounts_count = 0
    try:
        start_accounts_count = len(parse_recovered_accounts())
        session_recovered_baseline = start_accounts_count
    except:
        pass
        
    try:
        # Enforce UTF-8 encoding in Python subprocess on Windows
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["HERO_UI_RUN"] = "1"
        
        # Run with unbuffered output (-u) to capture stdout instantly
        process = subprocess.Popen(
            [python_exec, "-u", "hero_sms_automation.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=BASE_DIR,
            env=env
        )
        
        # Read stdout line by line
        for line in iter(process.stdout.readline, ''):
            log_queue.put(line)
            
        process.stdout.close()
        process.wait()
        
        # Send finish signal to queue
        rc = process.returncode
        log_queue.put(f"[SYSTEM_FINISH] Automation session terminated. Exit code: {rc}\n")
    except Exception as e:
        log_queue.put(f"[SYSTEM_FINISH] Failed to start subprocess: {e}\n")
    finally:
        global session_stop_requested_time
        if session_start_time:
            end_time = session_stop_requested_time if session_stop_requested_time else time.time()
            end_accounts_count = 0
            try:
                end_accounts_count = len(parse_recovered_accounts())
            except:
                pass
            is_success = (end_accounts_count > start_accounts_count)
            update_duration_in_stats(session_start_time, end_time, is_success)
            session_start_time = None
        session_stop_requested_time = None
        is_running = False
        process = None


@app.route('/')
def index():
    # Render UI using render_template_string for single-file delivery
    # Keep standard template outside Flask logic or inside, since we have template_html
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'POST':
        data = request.json
        if save_config_data(data):
            return jsonify({"status": "success", "message": "Configuration saved."})
        return jsonify({"status": "error", "message": "Failed to save configuration."}), 500
    return jsonify(load_config_data())


@app.route('/api/status', methods=['GET'])
def api_status():
    global is_running, session_start_time, session_stop_requested_time, session_recovered_baseline
    elapsed = 0
    start_time_val = None
    recovered_in_session = 0
    if is_running and session_start_time:
        end_time = session_stop_requested_time if session_stop_requested_time else time.time()
        elapsed = int(end_time - session_start_time)
        start_time_val = session_start_time
        try:
            current_count = len(parse_recovered_accounts())
            recovered_in_session = max(0, current_count - session_recovered_baseline)
        except:
            pass
    return jsonify({
        "is_running": is_running,
        "elapsed": elapsed,
        "start_time": start_time_val,
        "recovered": recovered_in_session
    })


@app.route('/api/start', methods=['POST'])
def api_start():
    global thread, is_running
    if is_running:
        return jsonify({"status": "error", "message": "Automation is already running."}), 400
    
    # Clear log queue
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
            
    thread = threading.Thread(target=run_automation_process, daemon=True)
    thread.start()
    return jsonify({"status": "success", "message": "Automation started."})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    global process, is_running, session_stop_requested_time, thread
    if not is_running or not process:
        return jsonify({"status": "error", "message": "Automation is not running."}), 400
        
    try:
        # Freeze elapsed time at the exact moment the user requests a stop
        session_stop_requested_time = time.time()
        
        # Write stop.flag to trigger a graceful shutdown
        flag_path = os.path.join(BASE_DIR, "stop.flag")
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("stop")
            
        log_queue.put("[SYSTEM] Stop request sent. Waiting for graceful shutdown...\n")
        
        # Wait up to 5 seconds for it to exit gracefully
        start_wait = time.time()
        graceful_exit = False
        while time.time() - start_wait < 5:
            if process.poll() is not None:
                graceful_exit = True
                break
            time.sleep(0.5)
            
        if not graceful_exit:
            # Force kill if it doesn't shut down in 5 seconds
            log_queue.put("[SYSTEM] Process did not exit within 5s. Force terminating...\n")
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
            
        # Wait for the thread to exit cleanly and commit stats to the JSON database
        if thread and thread.is_alive():
            thread.join(timeout=3)
            
        # Clean up stop.flag if it still exists
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
            except:
                pass
                
        is_running = False
        process = None
        return jsonify({"status": "success", "message": "Automation stopped."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to stop automation: {e}"}), 500


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
        os.path.join(profile_path, "Cookies")
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
            except Exception:
                pass
    return False


def generate_next_profile_name(user_data_dir: str = None) -> str:
    official_path = get_official_chrome_user_data_dir()
    standalone_path = os.path.join(BASE_DIR, "chrome_profiles")
    
    existing_profiles = set()
    max_num = 1
    
    for directory in [official_path, standalone_path]:
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

    # Scan existing profiles to check if they have a logged-in Facebook session
    for i in sorted(list(existing_profiles)):
        profile_folder_name = f"Profile {i}"
        
        standalone_dir = os.path.join(standalone_path, profile_folder_name)
        official_dir = os.path.join(official_path, profile_folder_name)
        
        is_logged = False
        if os.path.exists(standalone_dir):
            is_logged = is_logged or is_profile_logged_in(standalone_dir)
        if os.path.exists(official_dir):
            is_logged = is_logged or is_profile_logged_in(official_dir)
            
        if not is_logged:
            print(f"ℹ️ Profile {profile_folder_name} has no active Facebook session. Reusing it!")
            return profile_folder_name

    # If all existing profiles are active, return a new one
    return f"Profile {max_num + 1}"


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


@app.route('/api/launch_chrome', methods=['POST'])
def api_launch_chrome():
    try:
        import socket
        def is_port_free(p_num):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p_num))
                    return True
                except OSError:
                    return False

        port = 9222
        while not is_port_free(port):
            port += 1

        # Load configured profile name from config.json
        config_path = os.path.join(BASE_DIR, "config.json")
        profile_name = "Profile 1"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                    profile_name = cfg_data.get("chrome_profile_name", "Profile 1")
            except Exception:
                pass
        
        # Align User Data directory with hero_sms_automation.py to prevent session logs logout
        user_data_dir = os.path.join(BASE_DIR, "chrome_profiles_hero")
        os.makedirs(user_data_dir, exist_ok=True)
        print(f"ℹ️ Launching Hero SMS Chrome profile '{profile_name}' in shared folder: {user_data_dir}")
        
        # Find chrome
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files\Google\Chrome\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\chrome.exe",
        ]
        chrome_path = None
        for p_executable in paths:
            if os.path.exists(p_executable):
                chrome_path = p_executable
                break
        
        if not chrome_path:
            chrome_path = "chrome.exe" # system fallback
            
        cmd = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_name}",
            "--no-first-run",
            "--skip-first-run-ui",
            "--no-default-browser-check",
            "--disable-features=ProfilePicker,Translate,OptimizationGuideModelDownloading,OptimizationHints,OptimizationTargetPrediction,OptimizationGuide,OptimizationGuidePersonalizedHints",
            "--disable-blink-features=AutomationControlled"
        ]
        
        creation_flags = 0
        if sys.platform == 'win32':
            import subprocess
            creation_flags = subprocess.CREATE_NEW_CONSOLE
            
        # Launch Chrome detached
        subprocess.Popen(cmd, creationflags=creation_flags)
        
        # Auto update config.json
        config = load_config_data()
        config["chrome_debug_url"] = f"http://127.0.0.1:{port}"
        config["chrome_profile_name"] = profile_name
        save_config_data(config)
        
        return jsonify({
            "status": "success",
            "port": port,
            "chrome_debug_url": f"http://127.0.0.1:{port}",
            "profile_name": profile_name,
            "message": f"Successfully launched Chrome Profile on Port {port}"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to launch Chrome: {e}"}), 500


@app.route('/api/kill_chrome', methods=['POST'])
def api_kill_chrome():
    try:
        import subprocess
        if sys.platform == 'win32':
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
        return jsonify({"status": "success", "message": "All Chrome processes terminated successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to terminate Chrome: {e}"}), 500


@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    return jsonify(parse_recovered_accounts())


@app.route('/api/provider_stats', methods=['GET'])
def api_provider_stats():
    stats_file = os.path.join(BASE_DIR, "provider_stats.json")
    data = {}
    if os.path.exists(stats_file):
        try:
            with open(stats_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            pass
    return jsonify(data)


@app.route('/api/provider_stats/reset', methods=['POST'])
def api_provider_stats_reset():
    stats_file = os.path.join(BASE_DIR, "provider_stats.json")
    try:
        if os.path.exists(stats_file):
            os.remove(stats_file)
        return jsonify({"status": "success", "message": "Provider statistics reset."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def api_stats():
    recalculate_recovered_counts()
    stats_path = os.path.join(BASE_DIR, "recovered_stats.json")
    data = {}
    if os.path.exists(stats_path):
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if isinstance(v, dict):
                    data[k] = {
                        "recovered": v.get("recovered", 0),
                        "spent": v.get("spent", 0.0),
                        "duration": v.get("duration", 0),
                        "failed_logins": v.get("failed_logins", 0),
                        "success_duration": v.get("success_duration", 0)
                    }
                else:
                    data[k] = {
                        "recovered": int(v),
                        "spent": 0.0,
                        "duration": 0,
                        "failed_logins": 0,
                        "success_duration": 0
                    }
        except Exception:
            pass
    return jsonify(data)


@app.route('/api/clear_accounts', methods=['POST'])
def api_clear_accounts():
    try:
        if os.path.exists(ACCOUNTS_PATH):
            os.remove(ACCOUNTS_PATH)
        attempts_path = os.path.join(BASE_DIR, "phone_attempts_log.txt")
        if os.path.exists(attempts_path):
            try:
                os.remove(attempts_path)
            except:
                pass
        prov_stats_path = os.path.join(BASE_DIR, "provider_stats.json")
        if os.path.exists(prov_stats_path):
            try:
                os.remove(prov_stats_path)
            except:
                pass
        return jsonify({"status": "success", "message": "Accounts, attempts logs, and provider comparison stats cleared."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to clear history: {e}"}), 500


@app.route('/stream')
def stream_logs():
    def generate():
        while True:
            try:
                line = log_queue.get(timeout=10)
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: [PING]\n\n"
    return Response(generate(), mimetype='text/event-stream')


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hero SMS Automation Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --container-bg: rgba(17, 24, 39, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --primary-glow: #6366f1;
            --primary-glow-hover: #4f46e5;
            --success-color: #10b981;
            --danger-color: #ef4444;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --font-main: 'Outfit', sans-serif;
            --font-mono: 'Fira Code', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
        }

        body {
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(16, 185, 129, 0.1) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            font-family: var(--font-main);
            min-height: 100vh;
            overflow-x: hidden;
            display: flex;
            flex-direction: column;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.5rem 2.5rem;
            border-bottom: 1px solid var(--border-color);
            backdrop-filter: blur(12px);
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(11, 15, 25, 0.8);
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            width: 38px;
            height: 38px;
            background: linear-gradient(135deg, var(--primary-glow), #10b981);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 15px rgba(99, 102, 241, 0.4);
        }

        .logo-icon svg {
            width: 22px;
            height: 22px;
            fill: white;
        }

        .logo-text {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            background: linear-gradient(to right, #ffffff, #a5b4fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(255, 255, 255, 0.05);
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            border: 1px solid var(--border-color);
            font-size: 0.875rem;
            font-weight: 500;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--text-muted);
            box-shadow: 0 0 8px var(--text-muted);
        }

        .status-dot.running {
            background-color: var(--success-color);
            box-shadow: 0 0 12px var(--success-color);
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.8; }
            50% { transform: scale(1.1); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.8; }
        }

        .main-container {
            display: grid;
            grid-template-columns: 420px 1fr;
            gap: 2rem;
            padding: 2rem 2.5rem;
            flex-grow: 1;
            max-width: 1600px;
            margin: 0 auto;
            width: 100%;
        }

        /* Glassmorphism panel styling */
        .glass-panel {
            background: var(--container-bg);
            border-radius: 16px;
            border: 1px solid var(--border-color);
            backdrop-filter: blur(16px);
            padding: 1.75rem;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .panel-title {
            font-size: 1.25rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 0.75rem;
        }

        .panel-title svg {
            width: 20px;
            height: 20px;
            stroke: var(--primary-glow);
        }

        .input-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        label {
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-muted);
            letter-spacing: 0.025em;
            text-transform: uppercase;
        }

        input[type="text"], input[type="password"], input[type="number"], select {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            color: var(--text-main);
            font-family: var(--font-main);
            font-size: 0.95rem;
            transition: all 0.25s ease;
            outline: none;
            width: 100%;
        }

        /* Remove number input spin arrows for cleaner aesthetics */
        input[type="number"]::-webkit-outer-spin-button,
        input[type="number"]::-webkit-inner-spin-button {
            -webkit-appearance: none;
            margin: 0;
        }
        input[type="number"] {
            -moz-appearance: textfield;
        }

        input:focus, select:focus {
            border-color: var(--primary-glow);
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.2);
            background: rgba(255, 255, 255, 0.06);
        }

        .checkbox-container {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            cursor: pointer;
            padding: 0.25rem 0;
        }

        .checkbox-container input {
            display: none;
        }

        .custom-checkbox {
            width: 20px;
            height: 20px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.04);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }

        .checkbox-container input:checked + .custom-checkbox {
            background-color: var(--primary-glow);
            border-color: var(--primary-glow);
            box-shadow: 0 0 8px rgba(99, 102, 241, 0.4);
        }

        .custom-checkbox::after {
            content: "";
            width: 6px;
            height: 10px;
            border: solid white;
            border-width: 0 2px 2px 0;
            transform: rotate(45deg);
            display: none;
        }

        .checkbox-container input:checked + .custom-checkbox::after {
            display: block;
        }

        .btn {
            border-radius: 8px;
            font-weight: 600;
            font-size: 1rem;
            padding: 0.875rem 1.5rem;
            cursor: pointer;
            transition: all 0.25s ease;
            outline: none;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            width: 100%;
            border: none;
        }

        .btn-primary {
            background: linear-gradient(135deg, var(--primary-glow), #4f46e5);
            color: white;
            box-shadow: 0 4px 15px rgba(99, 102, 241, 0.35);
        }

        .btn-primary:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(99, 102, 241, 0.45);
        }

        .btn-primary:active:not(:disabled) {
            transform: translateY(0);
        }

        .btn-danger {
            background: linear-gradient(135deg, var(--danger-color), #dc2626);
            color: white;
            box-shadow: 0 4px 15px rgba(239, 68, 68, 0.3);
        }

        .btn-danger:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(239, 68, 68, 0.45);
        }

        .btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-main);
        }

        .btn-secondary:hover:not(:disabled) {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.15);
        }

        .btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            box-shadow: none;
        }

        .actions-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.75rem;
        }

        /* Log console styling */
        .console-panel {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            background: rgba(5, 7, 13, 0.95);
            border-radius: 16px;
            border: 1px solid var(--border-color);
            overflow: hidden;
            box-shadow: inset 0 4px 20px rgba(0,0,0,0.8);
            height: 520px;
        }

        .console-header {
            background: rgba(17, 24, 39, 0.5);
            border-bottom: 1px solid var(--border-color);
            padding: 0.75rem 1.25rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .console-tab {
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-muted);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .console-tab::before {
            content: "";
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background-color: var(--primary-glow);
        }

        .console-actions {
            display: flex;
            gap: 0.75rem;
            align-items: center;
        }

        .autoscroll-toggle {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.75rem;
            color: var(--text-muted);
            cursor: pointer;
        }

        .autoscroll-toggle input {
            display: none;
        }

        .autoscroll-toggle .indicator {
            width: 12px;
            height: 12px;
            border-radius: 3px;
            border: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .autoscroll-toggle input:checked + .indicator {
            background-color: var(--primary-glow);
            border-color: var(--primary-glow);
        }

        .autoscroll-toggle input:checked + .indicator::after {
            content: "";
            width: 3px;
            height: 6px;
            border: solid white;
            border-width: 0 1.5px 1.5px 0;
            transform: rotate(45deg);
        }

        .clear-btn {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 0.75rem;
            cursor: pointer;
            padding: 0.25rem;
        }

        .clear-btn:hover {
            color: var(--text-main);
        }

        .console-body {
            flex-grow: 1;
            padding: 1.25rem;
            overflow-y: auto;
            font-family: var(--font-mono);
            font-size: 0.85rem;
            line-height: 1.5;
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
            user-select: text;
        }

        .console-line {
            white-space: pre-wrap;
            word-break: break-all;
        }

        /* Color classes for different logs */
        .line-info { color: #f3f4f6; }
        .line-success { color: #10b981; font-weight: 500; }
        .line-warning { color: #f59e0b; }
        .line-error { color: #ef4444; font-weight: 500; }
        .line-system { color: #818cf8; font-weight: 600; border-top: 1px dashed rgba(255,255,255,0.05); border-bottom: 1px dashed rgba(255,255,255,0.05); padding: 0.25rem 0; margin: 0.25rem 0; }
        .line-code { color: #38bdf8; font-weight: 600; }

        /* Bottom table section */
        .bottom-section {
            grid-column: 1 / -1;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .table-container {
            width: 100%;
            overflow-x: auto;
            max-height: 550px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 12px;
            background: var(--container-bg);
            backdrop-filter: blur(16px);
        }
        
        .table-container::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        .table-container::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.02);
            border-radius: 4px;
        }
        .table-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }
        .table-container::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }

        th, td {
            padding: 1rem 1.25rem;
            border-bottom: 1px solid var(--border-color);
        }

        th {
            position: sticky;
            top: 0;
            z-index: 2;
            background: #111827 !important;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        .copy-btn {
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.25);
            color: #a5b4fc;
            padding: 0.4rem 0.75rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
        }

        .copy-btn:hover {
            background: var(--primary-glow);
            color: white;
            border-color: var(--primary-glow);
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.35);
        }

        .copy-btn:active {
            transform: scale(0.95);
        }

        .toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: rgba(16, 185, 129, 0.95);
            color: white;
            border-radius: 8px;
            padding: 1rem 1.5rem;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 1000;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-weight: 500;
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }

        /* Responsive */
        @media (max-width: 960px) {
            .main-container {
                grid-template-columns: 1fr;
            }
        }

        @keyframes dot-pulse {
            0% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            }
            70% {
                transform: scale(1);
                box-shadow: 0 0 0 5px rgba(16, 185, 129, 0);
            }
            100% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
            }
        }

        #stats-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 1rem;
            margin-top: 1rem;
            max-height: 380px;
            overflow-y: auto;
            padding-right: 4px;
        }

        #stats-grid::-webkit-scrollbar {
            width: 6px;
        }

        #stats-grid::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.02);
            border-radius: 3px;
        }

        #stats-grid::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }

        #stats-grid::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        /* Segmented Switch */
        .segmented-switch {
            display: flex;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 4px;
            margin-bottom: 1.25rem;
            position: relative;
        }

        .switch-option {
            flex: 1;
            text-align: center;
            padding: 10px;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 2;
        }

        .switch-option:hover {
            color: var(--text-main);
        }

        .switch-option.active {
            color: #ffffff;
            background: linear-gradient(135deg, var(--primary-glow), #4f46e5);
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-section">
            <div class="logo-icon">
                <svg viewBox="0 0 24 24">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
                </svg>
            </div>
            <div class="logo-text">Hero SMS Automation</div>
        </div>
        <div class="status-badge">
            <div class="status-dot" id="status-dot"></div>
            <span id="status-text">Idle</span>
        </div>
    </header>

    <div class="main-container">
        <!-- Configuration panel -->
        <div class="glass-panel">
            <div class="panel-title">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                Configurations
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr auto auto; gap: 0.5rem; align-items: flex-end;">
                <div class="input-group" style="flex-grow: 1;">
                    <label>Chrome Debug Port URL</label>
                    <input type="text" id="chrome_debug_url" placeholder="http://127.0.0.1:9222">
                </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr auto auto; gap: 0.5rem; align-items: flex-end;">
                <button class="btn btn-secondary" id="launch-chrome-btn" onclick="launchChrome()" style="width: auto; height: 42px; padding: 0 1rem; font-size: 0.85rem; margin-bottom: 2px;">
                    Launch Profile
                </button>
                <button class="btn btn-danger" id="kill-chrome-btn" onclick="killChrome()" style="width: auto; height: 42px; padding: 0 1rem; font-size: 0.85rem; margin-bottom: 2px; background: rgba(239, 68, 68, 0.15); border-color: rgba(239, 68, 68, 0.35); color: #f87171;">
                    Reset Chrome
                </button>
            </div>

            <div class="input-group">
                <label>Chrome Profile Name</label>
                <input type="text" id="chrome_profile_name" placeholder="Profile 1">
            </div>

            <div class="input-group">
                <label>SMS Procurement Method</label>
                <div class="segmented-switch">
                    <div class="switch-option active" id="opt-hero" onclick="selectProvider('hero-sms')">HeroSMS (Browser)</div>
                    <div class="switch-option" id="opt-claude" onclick="selectProvider('claudeotp')">ClaudeOTP (API)</div>
                </div>
                <input type="hidden" id="sms_provider" value="hero-sms">
            </div>

            <!-- HeroSMS Specific Fields -->
            <div id="hero-sms-only-fields" style="display: flex; flex-direction: column; gap: 0.75rem;">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                    <div class="input-group">
                        <label>Target Service</label>
                        <input type="text" id="service_text" placeholder="Facebook">
                    </div>
                    <div class="input-group">
                        <label>Target Country</label>
                        <input type="text" id="country_text" placeholder="Brazil">
                    </div>
                </div>

                <div class="input-group">
                    <label>Buy Button Text / Price Selector</label>
                    <input type="text" id="buy_text" placeholder="Buy for $0.099">
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                    <div class="input-group">
                        <label>Min Price Acceptable (USD)</label>
                        <input type="number" step="0.001" id="min_price" placeholder="0.01">
                    </div>
                    <div class="input-group">
                        <label>Max Price Acceptable (USD)</label>
                        <input type="number" step="0.001" id="max_price" placeholder="0.15">
                    </div>
                </div>

                <div style="border: 1px dashed var(--border-color); padding: 1rem; border-radius: 8px; margin: 0.25rem 0; background: rgba(255,255,255,0.01);">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem;">
                        <label class="checkbox-container" style="padding: 0;">
                            <input type="checkbox" id="auto_login" onchange="toggleAutoLoginFields()">
                            <span class="custom-checkbox"></span>
                            Enable Hero SMS Auto-Login
                        </label>
                    </div>
                    <div id="auto-login-fields" style="display: none; flex-direction: column; gap: 0.75rem;">
                        <div class="input-group">
                            <label style="font-size: 0.75rem;">Hero SMS Username / Email</label>
                            <input type="text" id="hero_username" placeholder="your-email@example.com">
                        </div>
                        <div class="input-group">
                            <label style="font-size: 0.75rem;">Hero SMS Password</label>
                            <input type="password" id="hero_password" placeholder="••••••••">
                        </div>
                    </div>
                </div>
            </div>

            <!-- ClaudeOTP Specific Fields -->
            <div id="claudeotp-only-fields" style="display: none; flex-direction: column; gap: 0.75rem;">
                <div style="border: 1px dashed var(--border-color); padding: 1rem; border-radius: 8px; margin: 0.25rem 0; background: rgba(255,255,255,0.01);">
                    <div style="display: flex; flex-direction: column; gap: 0.75rem;">
                        <div class="input-group">
                            <label style="font-size: 0.75rem;">ClaudeOTP API Key</label>
                            <input type="text" id="claudeotp_api_key" placeholder="Enter API Key">
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                            <div class="input-group">
                                <label style="font-size: 0.75rem;">Service ID</label>
                                <input type="number" id="claudeotp_service" placeholder="544">
                            </div>
                            <div class="input-group">
                                <label style="font-size: 0.75rem;">Country ISO Code</label>
                                <input type="text" id="claudeotp_country" placeholder="ID">
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="input-group">
                <label>New Account Password</label>
                <input type="password" id="new_password" placeholder="HeroSmsRecover123!">
            </div>

            <div class="input-group">
                <label>Target Recovery Page URL</label>
                <input type="text" id="target_url" placeholder="Facebook Identify URL">
            </div>

            <div class="input-group">
                <label>Windows VPN Connection Name (e.g. Surfshark - leave empty to disable)</label>
                <input type="text" id="vpn_connection_name" placeholder="Surfshark">
            </div>

            <div class="input-group">
                <label>Control Phone Number (used to verify if Facebook is shadow-blocking the IP/session)</label>
                <input type="text" id="control_phone_number" placeholder="628XXXXXXXXX">
            </div>

            <div style="display: flex; flex-direction: column; gap: 0.75rem;">
                <label class="checkbox-container">
                    <input type="checkbox" id="multiple_accounts">
                    <span class="custom-checkbox"></span>
                    Loop indefinitely (Multiple Accounts)
                </label>
                
                <label class="checkbox-container">
                    <input type="checkbox" id="confirm_before_buy">
                    <span class="custom-checkbox"></span>
                    Confirm manually before buying
                </label>
            </div>

            <div class="actions-grid">
                <button class="btn btn-secondary" id="save-config-btn" onclick="saveConfig()">Save Settings</button>
                <button class="btn btn-primary" id="start-btn" onclick="startAutomation()">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
                        <path d="M11.596 8.697l-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393z"/>
                    </svg>
                    Start Automation
                </button>
                <button class="btn btn-danger" id="stop-btn" onclick="stopAutomation()" disabled>
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
                        <path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/>
                    </svg>
                    Stop Automation
                </button>
            </div>
        </div>

        <!-- Right Column -->
        <div style="display: flex; flex-direction: column; gap: 2rem; overflow: hidden;">
            <!-- Terminal log panel -->
            <div class="glass-panel" style="padding: 0; flex-grow: 1;">
                <div class="console-panel" style="height: 480px;">
                    <div class="console-header">
                        <div class="console-tab">Live Output Console</div>
                        <div class="console-actions">
                            <label class="autoscroll-toggle">
                                <input type="checkbox" id="autoscroll-check" checked>
                                <span class="indicator"></span>
                                Auto-scroll
                            </label>
                            <button class="clear-btn" onclick="clearConsole()">Clear console</button>
                        </div>
                    </div>
                    <div class="console-body" id="console-body">
                        <div class="console-line line-system">[SYSTEM] Console initialized. Adjust settings and click 'Start Automation' to begin.</div>
                    </div>
                </div>
            </div>

            <!-- Daily Analytics panel -->
            <div class="glass-panel">
                <div class="panel-title" style="border: none; padding-bottom: 0;">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" style="width: 20px; height: 20px; vertical-align: middle; margin-right: 0.5rem; stroke: var(--primary-color);">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z" />
                    </svg>
                    Daily Recovery Analytics
                </div>
                <div id="stats-grid">
                    <!-- Dynamic stats cards will go here -->
                    <div style="text-align: center; color: var(--text-muted); padding: 1rem; grid-column: 1 / -1;">No daily history recorded yet.</div>
                </div>
            </div>
        </div>

    <!-- Summary Modal -->
    <div id="summary-modal" style="position: fixed; inset: 0; background: rgba(0,0,0,0.85); backdrop-filter: blur(8px); display: none; justify-content: center; align-items: center; z-index: 10000; opacity: 0; transition: opacity 0.3s ease;">
        <div class="glass-panel" style="max-width: 480px; width: 90%; gap: 1.5rem; border: 1px solid rgba(99, 102, 241, 0.25); box-shadow: 0 20px 50px rgba(0,0,0,0.5); padding: 2rem;">
            <div class="panel-title" style="border: none; font-size: 1.5rem; justify-content: center; color: var(--success-color); padding-bottom: 0; margin-bottom: 0.5rem;">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5" style="width: 28px; height: 28px; stroke: var(--success-color); margin-right: 0.5rem; vertical-align: middle;">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Session Summary
            </div>
            
            <p style="text-align: center; color: var(--text-muted); font-size: 0.95rem; margin-bottom: 1.5rem;">The automation run has ended. Here are your stats:</p>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0;">
                <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Duration</div>
                    <div id="modal-duration" style="font-size: 1.1rem; font-weight: 600; margin-top: 0.25rem;">0m 0s</div>
                </div>
                <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Recovered</div>
                    <div id="modal-recovered" style="font-size: 1.25rem; font-weight: 700; color: var(--success-color); margin-top: 0.15rem;">0</div>
                </div>
                <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Numbers Tried</div>
                    <div id="modal-tried" style="font-size: 1.1rem; font-weight: 600; margin-top: 0.25rem;">0</div>
                </div>
                <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; text-align: center;">
                    <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Amount Spent</div>
                    <div id="modal-spent" style="font-size: 1.1rem; font-weight: 600; color: #f59e0b; margin-top: 0.25rem;">$0.00</div>
                </div>
            </div>
            
            <button class="btn btn-primary" onclick="closeSummaryModal()" style="margin-top: 1.5rem; width: 100%;">Done</button>
        </div>
    </div>

        <!-- Accounts & Attempts section -->
        <div class="bottom-section">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.25rem;">
                <div style="display: flex; gap: 1rem;">
                    <div id="tab-recovered" onclick="switchBottomTab('recovered')" style="font-size: 1.2rem; font-weight: 600; padding: 0.5rem 1rem; cursor: pointer; color: var(--primary-color); border-bottom: 3px solid var(--primary-color); transition: all 0.2s ease; display: flex; align-items: center; gap: 0.5rem;">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5" style="width: 18px; height: 18px;">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                        </svg>
                        Recovered Accounts
                        <span id="filter-badge" style="display: none; font-size: 0.75rem; padding: 0.1rem 0.5rem; border-radius: 20px; background: rgba(99, 102, 241, 0.15); border: 1px solid var(--primary-color); color: #818cf8; font-weight: 600; cursor: pointer; margin-left: 0.5rem;" onclick="clearDateFilter(event)">
                            Filtered: <span id="filter-date-text"></span> ✕
                        </span>
                    </div>
                    <div id="tab-comparison" onclick="switchBottomTab('comparison')" style="font-size: 1.2rem; font-weight: 500; padding: 0.5rem 1rem; cursor: pointer; color: var(--text-muted); border-bottom: 3px solid transparent; transition: all 0.2s ease; display: flex; align-items: center; gap: 0.5rem;">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5" style="width: 18px; height: 18px;">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                        </svg>
                        Provider Comparison
                    </div>
                </div>
                <button class="btn btn-secondary" style="width: auto; font-size: 0.8rem; padding: 0.5rem 1rem;" onclick="clearAccounts()">Clear History</button>
            </div>
            
            <!-- Recovered Accounts Table -->
            <div id="container-recovered" class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 50px;">No</th>
                            <th>Recovered Time</th>
                            <th>Chrome Profile</th>
                            <th>Facebook UID / Phone</th>
                            <th>New Password</th>
                            <th>2FA Required</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="accounts-tbody">
                        <tr>
                            <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">No recovered accounts found yet. Running automation successfully will add items here.</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Provider Comparison Table -->
            <div id="container-comparison" class="table-container" style="display: none;">
                <div style="display: flex; justify-content: flex-end; gap: 0.75rem; margin-bottom: 0.75rem; padding: 0.5rem 0.25rem; align-items: center;">
                    <span style="font-size: 0.85rem; color: var(--text-muted); align-self: center; font-weight: 500;">Timeframe:</span>
                    <select id="comparison-timeframe" onchange="renderComparison()" style="background: var(--bg-card); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 4px; padding: 0.35rem 0.65rem; font-size: 0.85rem; cursor: pointer; outline: none; transition: border-color 0.2s ease;">
                        <option value="today" selected>Today</option>
                        <option value="yesterday">Yesterday</option>
                        <option value="all">All Time</option>
                    </select>
                    <button class="btn" onclick="resetComparisonStats()" style="height: 32px; padding: 0 0.75rem; font-size: 0.8rem; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); color: #f87171; border-radius: 4px; cursor: pointer; transition: all 0.2s ease; display: inline-flex; align-items: center; justify-content: center; font-weight: 500;">
                        Reset All Stats
                    </button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>SMS Provider</th>
                            <th>Total Tried</th>
                            <th>Success (Recovered)</th>
                            <th>No Account Found</th>
                            <th>No SMS Sent (Timeout)</th>
                            <th>Rate Limited / Blocks</th>
                            <th>Success Rate</th>
                        </tr>
                    </thead>
                    <tbody id="comparison-tbody">
                        <tr>
                            <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">No provider stats recorded yet. Running attempts will populate comparative stats.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="toast" id="toast">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
            <path d="M16 8A8 8 0 1 1 0 8a8 8 0 0 1 16 0zm-3.97-3.03a.75.75 0 0 0-1.08.022L7.477 9.417 5.384 7.323a.75.75 0 0 0-1.06 1.06L6.97 11.03a.75.75 0 0 0 1.079-.02l3.992-4.99a.75.75 0 0 0-.01-1.05z"/>
        </svg>
        <span id="toast-text">Action successful</span>
    </div>

    <script>
        let eventSource = null;

        // On Page Load
        window.addEventListener('load', () => {
            loadConfig();
            loadAccounts();
            loadStats();
            checkStatus();
            // Polling status
            setInterval(checkStatus, 3000);
        });

        function showToast(text, type = 'success') {
            const toast = document.getElementById('toast');
            const toastText = document.getElementById('toast-text');
            toastText.textContent = text;
            
            if (type === 'danger') {
                toast.style.background = 'rgba(239, 68, 68, 0.95)';
            } else {
                toast.style.background = 'rgba(16, 185, 129, 0.95)';
            }
            
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        function toggleAutoLoginFields() {
            const check = document.getElementById('auto_login');
            const container = document.getElementById('auto-login-fields');
            if (check.checked) {
                container.style.display = 'flex';
            } else {
                container.style.display = 'none';
            }
        }

        function selectProvider(provider) {
            document.getElementById('sms_provider').value = provider;
            
            const optHero = document.getElementById('opt-hero');
            const optClaude = document.getElementById('opt-claude');
            const heroFields = document.getElementById('hero-sms-only-fields');
            const claudeFields = document.getElementById('claudeotp-only-fields');
            
            if (provider === 'claudeotp') {
                optHero.classList.remove('active');
                optClaude.classList.add('active');
                heroFields.style.display = 'none';
                claudeFields.style.display = 'flex';
            } else {
                optHero.classList.add('active');
                optClaude.classList.remove('active');
                heroFields.style.display = 'flex';
                claudeFields.style.display = 'none';
            }
        }

        function loadConfig() {
            fetch('/api/config')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('chrome_debug_url').value = data.chrome_debug_url || '';
                    document.getElementById('chrome_profile_name').value = data.chrome_profile_name || '';
                    document.getElementById('service_text').value = data.service_text || '';
                    document.getElementById('country_text').value = data.country_text || '';
                    document.getElementById('buy_text').value = data.buy_text || '';
                    document.getElementById('min_price').value = data.min_price !== undefined ? data.min_price : 0.01;
                    document.getElementById('max_price').value = data.max_price !== undefined ? data.max_price : 0.15;
                    document.getElementById('new_password').value = data.new_password || '';
                    document.getElementById('target_url').value = data.target_url || '';
                    document.getElementById('multiple_accounts').checked = !!data.multiple_accounts;
                    document.getElementById('confirm_before_buy').checked = !!data.confirm_before_buy;
                    document.getElementById('vpn_connection_name').value = data.vpn_connection_name || '';
                    document.getElementById('control_phone_number').value = data.control_phone_number || '';
                    
                    document.getElementById('auto_login').checked = !!data.auto_login;
                    document.getElementById('hero_username').value = data.hero_username || '';
                    document.getElementById('hero_password').value = data.hero_password || '';
                    toggleAutoLoginFields();

                    document.getElementById('claudeotp_api_key').value = data.claudeotp_api_key || '';
                    document.getElementById('claudeotp_service').value = data.claudeotp_service || 544;
                    document.getElementById('claudeotp_country').value = data.claudeotp_country || 'ID';
                    
                    const provider = data.sms_provider || (data.claudeotp_api_key ? 'claudeotp' : 'hero-sms');
                    selectProvider(provider);
                });
        }

        function saveConfig(silent = false) {
            const config = {
                chrome_debug_url: document.getElementById('chrome_debug_url').value,
                chrome_profile_name: document.getElementById('chrome_profile_name').value,
                service_text: document.getElementById('service_text').value,
                country_text: document.getElementById('country_text').value,
                buy_text: document.getElementById('buy_text').value,
                min_price: parseFloat(document.getElementById('min_price').value) || 0.01,
                max_price: parseFloat(document.getElementById('max_price').value) || 0.15,
                new_password: document.getElementById('new_password').value,
                target_url: document.getElementById('target_url').value,
                multiple_accounts: document.getElementById('multiple_accounts').checked,
                confirm_before_buy: document.getElementById('confirm_before_buy').checked,
                vpn_connection_name: document.getElementById('vpn_connection_name').value,
                control_phone_number: document.getElementById('control_phone_number').value,
                
                auto_login: document.getElementById('auto_login').checked,
                hero_username: document.getElementById('hero_username').value,
                hero_password: document.getElementById('hero_password').value,

                sms_provider: document.getElementById('sms_provider').value,
                claudeotp_api_key: document.getElementById('claudeotp_api_key').value,
                claudeotp_service: parseInt(document.getElementById('claudeotp_service').value) || 544,
                claudeotp_country: document.getElementById('claudeotp_country').value || 'ID'
            };

            return fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    if (!silent) showToast('Configuration saved successfully!');
                } else {
                    showToast('Failed to save configuration.', 'danger');
                }
            });
        }

        function launchChrome() {
            const launchBtn = document.getElementById('launch-chrome-btn');
            launchBtn.disabled = true;
            launchBtn.textContent = 'Launching...';
            
            fetch('/api/launch_chrome', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    launchBtn.disabled = false;
                    launchBtn.textContent = 'Launch Profile';
                    
                    if (data.status === 'success') {
                        showToast(`Launched Chrome profile on port ${data.port}!`);
                        // Auto load config update
                        loadConfig();
                    } else {
                        showToast(data.message, 'danger');
                    }
                })
                .catch(err => {
                    launchBtn.disabled = false;
                    launchBtn.textContent = 'Launch Profile';
                    showToast('Network error launching Chrome.', 'danger');
                });
        }


        function killChrome() {
            if (confirm("This will close ALL running Chrome windows and background processes on your PC. Proceed?")) {
                const killBtn = document.getElementById('kill-chrome-btn');
                killBtn.disabled = true;
                killBtn.textContent = 'Killing...';
                
                fetch('/api/kill_chrome', { method: 'POST' })
                    .then(res => res.json())
                    .then(data => {
                        killBtn.disabled = false;
                        killBtn.textContent = 'Reset Chrome';
                        if (data.status === 'success') {
                            showToast("All Chrome processes terminated successfully.");
                        } else {
                            showToast(data.message, 'danger');
                        }
                    })
                    .catch(err => {
                        killBtn.disabled = false;
                        killBtn.textContent = 'Reset Chrome';
                        showToast('Error resetting Chrome.', 'danger');
                    });
            }
        }


        let refreshInterval = null;

        function checkStatus() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    const statusDot = document.getElementById('status-dot');
                    const statusText = document.getElementById('status-text');
                    const startBtn = document.getElementById('start-btn');
                    const stopBtn = document.getElementById('stop-btn');
                    
                    if (data.is_running) {
                        statusDot.className = 'status-dot running';
                        statusText.textContent = 'Running';
                        startBtn.disabled = true;
                        stopBtn.disabled = false;
                        
                        window.activeElapsed = data.elapsed || 0;
                        window.activeStartTime = data.start_time || null;
                        window.activeSessionRecovered = data.recovered || 0;
                        
                        // Format dynamic running session duration for summary modal
                        const days = Math.floor(window.activeElapsed / 86400);
                        const hours = Math.floor((window.activeElapsed % 86400) / 3600);
                        const minutes = Math.floor((window.activeElapsed % 3600) / 60);
                        const seconds = window.activeElapsed % 60;
                        let parts = [];
                        if (days > 0) parts.push(`${days} day${days > 1 ? 's' : ''}`);
                        if (hours > 0) parts.push(`${hours} hr${hours > 1 ? 's' : ''}`);
                        if (minutes > 0) parts.push(`${minutes} min${minutes > 1 ? 's' : ''}`);
                        if (seconds > 0 || parts.length === 0) parts.push(`${seconds} sec${seconds > 1 ? 's' : ''}`);
                        sessionStats.duration = parts.join(', ');
                        
                        // Start log streaming if not active
                        if (!eventSource) {
                            startLogStream();
                        }
                        
                        // Set up real-time stats/accounts refresh interval
                        if (!refreshInterval) {
                            refreshInterval = setInterval(() => {
                                checkStatus();
                                loadAccounts();
                                loadStats();
                            }, 5000);
                        }
                    } else {
                        statusDot.className = 'status-dot';
                        statusText.textContent = 'Idle';
                        startBtn.disabled = false;
                        stopBtn.disabled = true;
                        
                        window.activeElapsed = 0;
                        window.activeStartTime = null;
                        window.activeSessionRecovered = 0;
                        
                        if (eventSource) {
                            eventSource.close();
                            eventSource = null;
                        }
                        
                        // Clear real-time refresh interval
                        if (refreshInterval) {
                            clearInterval(refreshInterval);
                            refreshInterval = null;
                        }
                    }
                });
        }

        function startAutomation() {
            // Reset stats
            sessionStats = {
                duration: '0m 0s',
                recovered: '0',
                tried: '0',
                spent: '$0.00'
            };
            // Auto-save configuration first
            saveConfig(true).then(() => {
                fetch('/api/start', { method: 'POST' })
                    .then(res => res.json())
                    .then(data => {
                        if (data.status === 'success') {
                            showToast('Automation process started.');
                            checkStatus();
                            // Load accounts periodically
                            setTimeout(loadAccounts, 5000);
                        } else {
                            showToast(data.message, 'danger');
                        }
                    });
            });
        }

        function stopAutomation() {
            fetch('/api/stop', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'success') {
                        showToast('Stopping automation...', 'danger');
                        checkStatus();
                        loadAccounts();
                    } else {
                        showToast(data.message, 'danger');
                    }
                });
        }

        function clearConsole() {
            const body = document.getElementById('console-body');
            body.innerHTML = '<div class="console-line line-system">[SYSTEM] Console cleared.</div>';
        }

        function startLogStream() {
            if (eventSource) eventSource.close();
            
            eventSource = new EventSource('/stream');
            const consoleBody = document.getElementById('console-body');
            
            eventSource.onmessage = (event) => {
                const line = event.data;
                if (line === '[PING]') return;
                
                // Parse log message style
                let className = 'line-info';
                if (line.includes('✅') || line.includes('SUCCESS') || line.includes('successfully')) {
                    className = 'line-success';
                } else if (line.includes('⚠️') || line.includes('WARNING') || line.includes('Timed out')) {
                    className = 'line-warning';
                } else if (line.includes('❌') || line.includes('Error') || line.includes('FAILED')) {
                    className = 'line-error';
                } else if (line.startsWith('[SYSTEM') || line.startsWith('[OK')) {
                    className = 'line-system';
                } else if (line.includes('🔑') || line.includes('Recovered Account')) {
                    className = 'line-code';
                }
                
                // Add line to terminal
                const lineDiv = document.createElement('div');
                lineDiv.className = `console-line ${className}`;
                lineDiv.textContent = line;
                consoleBody.appendChild(lineDiv);
                
                // Limit terminal lines buffer size (keep last 500)
                while (consoleBody.children.length > 500) {
                    consoleBody.removeChild(consoleBody.firstChild);
                }
                
                // Autoscroll
                if (document.getElementById('autoscroll-check').checked) {
                    consoleBody.scrollTop = consoleBody.scrollHeight;
                }
                
                // Capture stats printed by script
                if (line.includes('Total Time Elapsed')) {
                    sessionStats.duration = line.split(' : ')[1] || '0m 0s';
                } else if (line.includes('Total Accounts Recovered')) {
                    sessionStats.recovered = line.split(': ')[1] || '0';
                } else if (line.includes('Total Numbers Tried')) {
                    sessionStats.tried = line.split(': ')[1] || '0';
                } else if (line.includes('Total Amount Spent')) {
                    sessionStats.spent = line.split(': ')[1] || '$0.00';
                }
                
                // If the script finished, reload accounts table
                if (line.includes('[SYSTEM_FINISH]')) {
                    loadAccounts();
                    loadStats();
                    // Show summary if we tried numbers in this session
                    if (parseInt(sessionStats.tried) > 0 || parseInt(sessionStats.recovered) > 0) {
                        setTimeout(showSummaryModal, 1000);
                    }
                }
            };
            
            eventSource.onerror = () => {
                eventSource.close();
                eventSource = null;
            };
        }

        let selectedDateFilter = null;
        let accountsData = [];
        let activeBottomTab = 'recovered';
        let comparisonData = {};

        function switchBottomTab(tabName) {
            activeBottomTab = tabName;
            const tabRec = document.getElementById('tab-recovered');
            const tabComp = document.getElementById('tab-comparison');
            const conRec = document.getElementById('container-recovered');
            const conComp = document.getElementById('container-comparison');

            if (tabName === 'recovered') {
                tabRec.style.color = 'var(--primary-color)';
                tabRec.style.borderBottom = '3px solid var(--primary-color)';
                tabRec.style.fontWeight = '600';
                
                tabComp.style.color = 'var(--text-muted)';
                tabComp.style.borderBottom = '3px solid transparent';
                tabComp.style.fontWeight = '500';

                conRec.style.display = 'block';
                conComp.style.display = 'none';
                loadAccounts();
            } else {
                tabComp.style.color = 'var(--primary-color)';
                tabComp.style.borderBottom = '3px solid var(--primary-color)';
                tabComp.style.fontWeight = '600';
                
                tabRec.style.color = 'var(--text-muted)';
                tabRec.style.borderBottom = '3px solid transparent';
                tabRec.style.fontWeight = '500';

                conRec.style.display = 'none';
                conComp.style.display = 'block';
                loadComparison();
            }
        }

        function loadComparison() {
            fetch('/api/provider_stats')
                .then(res => res.json())
                .then(data => {
                    comparisonData = data;
                    renderComparison();
                });
        }

        function resetComparisonStats() {
            if (!confirm("Are you sure you want to permanently delete all accumulated provider stats?")) {
                return;
            }
            fetch('/api/provider_stats/reset', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'success') {
                        comparisonData = {};
                        renderComparison();
                    } else {
                        alert("Failed to reset stats: " + data.message);
                    }
                });
        }

        function renderComparison() {
            const tbody = document.getElementById('comparison-tbody');
            tbody.innerHTML = '';

            const timeframe = document.getElementById('comparison-timeframe').value;
            
            // Calculate target dates
            const todayStr = new Date().toISOString().split('T')[0];
            const yesterday = new Date();
            yesterday.setDate(yesterday.getDate() - 1);
            const yesterdayStr = yesterday.toISOString().split('T')[0];
            
            const aggregated = {};
            const defaultStats = () => ({
                total_tried: 0,
                success: 0,
                no_account_found: 0,
                sms_timeout: 0,
                rate_limited: 0
            });
            
            // Initialize defaults for comparison layout consistency
            aggregated['hero-sms'] = defaultStats();
            aggregated['claudeotp'] = defaultStats();
            
            Object.keys(comparisonData).forEach(dateKey => {
                // If legacy key (flat format direct provider string)
                const isLegacy = dateKey === 'hero-sms' || dateKey === 'claudeotp' || dateKey === 'herosms';
                
                let include = false;
                if (timeframe === 'all') {
                    include = true;
                } else if (timeframe === 'today' && (dateKey === todayStr || isLegacy)) {
                    include = true;
                } else if (timeframe === 'yesterday' && dateKey === yesterdayStr) {
                    include = true;
                }
                
                if (include) {
                    if (isLegacy) {
                        const targetKey = dateKey === 'herosms' ? 'hero-sms' : dateKey;
                        const src = comparisonData[dateKey];
                        const dest = aggregated[targetKey];
                        if (src && dest) {
                            Object.keys(dest).forEach(k => {
                                dest[k] += src[k] || 0;
                            });
                        }
                    } else {
                        const dateObj = comparisonData[dateKey];
                        if (dateObj) {
                            Object.keys(dateObj).forEach(prov => {
                                const targetKey = prov === 'herosms' ? 'hero-sms' : prov;
                                const src = dateObj[prov];
                                const dest = aggregated[targetKey] || (aggregated[targetKey] = defaultStats());
                                if (src && dest) {
                                    Object.keys(dest).forEach(k => {
                                        dest[k] += src[k] || 0;
                                    });
                                }
                            });
                        }
                    }
                }
            });

            const providers = Object.keys(aggregated);
            providers.forEach(prov => {
                const item = aggregated[prov];
                const tr = document.createElement('tr');

                // Provider Name Label
                const tdName = document.createElement('td');
                tdName.style.fontWeight = '600';
                tdName.style.fontSize = '0.95rem';
                if (prov === 'herosms' || prov === 'hero-sms') {
                    tdName.innerHTML = `<span style="color: #6366f1;">HeroSMS (Browser)</span>`;
                } else if (prov === 'claudeotp') {
                    tdName.innerHTML = `<span style="color: #10b981;">ClaudeOTP (API)</span>`;
                } else {
                    tdName.textContent = prov.toUpperCase();
                }
                tr.appendChild(tdName);

                // Total Tried
                const tdTried = document.createElement('td');
                tdTried.textContent = item.total_tried || 0;
                tdTried.style.fontWeight = '500';
                tr.appendChild(tdTried);

                // Success
                const tdSuccess = document.createElement('td');
                tdSuccess.textContent = item.success || 0;
                tdSuccess.style.color = '#10b981';
                tdSuccess.style.fontWeight = '600';
                tr.appendChild(tdSuccess);

                // No Account Found
                const tdNoAcc = document.createElement('td');
                tdNoAcc.textContent = item.no_account_found || 0;
                tdNoAcc.style.color = 'var(--text-muted)';
                tr.appendChild(tdNoAcc);

                // No SMS Sent (Timeout)
                const tdTimeout = document.createElement('td');
                tdTimeout.textContent = item.sms_timeout || 0;
                tdTimeout.style.color = '#f59e0b';
                tr.appendChild(tdTimeout);

                // Rate Limited / Blocks
                const tdBlocked = document.createElement('td');
                tdBlocked.textContent = item.rate_limited || 0;
                tdBlocked.style.color = '#ef4444';
                tr.appendChild(tdBlocked);

                // Success Rate
                const tdRate = document.createElement('td');
                const total = item.total_tried || 0;
                const rate = total > 0 ? ((item.success / total) * 100).toFixed(1) : '0.0';
                tdRate.innerHTML = `<strong style="color: #818cf8;">${rate}%</strong>`;
                tr.appendChild(tdRate);

                tbody.appendChild(tr);
            });
        }

        function loadAccounts() {
            fetch('/api/accounts')
                .then(res => res.json())
                .then(data => {
                    accountsData = data;
                    renderAccounts();
                });
            loadComparison();
        }

        function renderAccounts() {
            const tbody = document.getElementById('accounts-tbody');
            const badge = document.getElementById('filter-badge');
            const badgeText = document.getElementById('filter-date-text');
            
            if (selectedDateFilter) {
                let dateDisplay = selectedDateFilter;
                try {
                    const parsedDate = new Date(selectedDateFilter + 'T00:00:00');
                    dateDisplay = parsedDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                } catch (e) {}
                badgeText.textContent = dateDisplay;
                badge.style.display = 'inline-flex';
            } else {
                badge.style.display = 'none';
            }

            let filtered = accountsData;
            if (selectedDateFilter) {
                filtered = accountsData.filter(acc => acc.date && acc.date.startsWith(selectedDateFilter));
            }

            if (filtered.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                            ${selectedDateFilter ? 'No recovered accounts found for this day.' : 'No recovered accounts found yet. Running automation successfully will add items here.'}
                        </td>
                    </tr>`;
                return;
            }
            
            tbody.innerHTML = '';
            const displayData = filtered;
            displayData.forEach((acc, index) => {
                const tr = document.createElement('tr');
                
                // Number column ('No')
                const tdNo = document.createElement('td');
                tdNo.textContent = index + 1;
                tdNo.style.color = 'var(--text-muted)';
                tdNo.style.fontWeight = '600';
                tr.appendChild(tdNo);
                
                const parts = acc.date.split(' ');
                const dateStr = parts[0];
                const timeStr = parts[1];
                
                const today = new Date();
                const todayStr = today.getFullYear() + '-' + 
                                 String(today.getMonth() + 1).padStart(2, '0') + '-' + 
                                 String(today.getDate()).padStart(2, '0');
                
                const isToday = (dateStr === todayStr);
                
                let ampmTime = timeStr;
                if (timeStr) {
                    const tParts = timeStr.split(':');
                    if (tParts.length >= 2) {
                        let hour = parseInt(tParts[0]);
                        const min = tParts[1];
                        const sec = tParts[2] || '00';
                        const ampm = hour >= 12 ? 'PM' : 'AM';
                        hour = hour % 12;
                        hour = hour ? hour : 12;
                        ampmTime = `${String(hour).padStart(2, '0')}:${min}:${sec} ${ampm}`;
                    }
                }
                
                const tdTime = document.createElement('td');
                if (isToday) {
                    tr.style.background = 'rgba(16, 185, 129, 0.04)';
                    tdTime.innerHTML = `
                         <div style="display: flex; align-items: center; gap: 0.5rem;">
                             <span style="display: inline-block; width: 6px; height: 6px; background: #10b981; border-radius: 50%; animation: dot-pulse 1.8s infinite;"></span>
                             <span style="color: #10b981; font-weight: 600;">Today, ${ampmTime}</span>
                         </div>`;
                } else {
                    tdTime.innerHTML = `<span style="color: var(--text-muted);">${dateStr} ${ampmTime}</span>`;
                }
                tr.appendChild(tdTime);
                
                const tdProfile = document.createElement('td');
                tdProfile.textContent = acc.profile || 'Unknown';
                tdProfile.style.fontWeight = '500';
                if (isToday) {
                    tdProfile.style.color = '#e0e0e0';
                }
                tr.appendChild(tdProfile);
                
                const tdUid = document.createElement('td');
                tdUid.style.fontWeight = '500';
                tdUid.textContent = acc.uid;
                tr.appendChild(tdUid);
                
                const tdPass = document.createElement('td');
                tdPass.style.fontFamily = 'var(--font-mono)';
                tdPass.textContent = acc.password;
                tr.appendChild(tdPass);
                
                const td2fa = document.createElement('td');
                if (acc.two_fa && acc.two_fa.trim() === '2FA') {
                    td2fa.innerHTML = `<span style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; padding: 0.15rem 0.4rem; border-radius: 4px; font-weight: 600; font-size: 0.75rem;">2FA</span>`;
                } else {
                    td2fa.innerHTML = `<span style="color: var(--text-muted); font-size: 0.85rem;">None</span>`;
                }
                tr.appendChild(td2fa);
                
                const tdActions = document.createElement('td');
                const copyBtn = document.createElement('button');
                copyBtn.className = 'copy-btn';
                copyBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                    </svg>
                    Copy Cookies`;
                copyBtn.onclick = () => {
                    navigator.clipboard.writeText(acc.cookie).then(() => {
                        showToast('Cookies copied to clipboard!');
                    });
                };
                tdActions.appendChild(copyBtn);
                tr.appendChild(tdActions);
                
                tbody.appendChild(tr);
            });
        }

        function clearDateFilter(event) {
            if (event) event.stopPropagation();
            selectedDateFilter = null;
            loadStats();
            renderAccounts();
        }

        function toggleDateFilter(date) {
            if (selectedDateFilter === date) {
                selectedDateFilter = null;
            } else {
                selectedDateFilter = date;
            }
            loadStats();
            renderAccounts();
        }

        function clearAccounts() {
            if (confirm('Are you sure you want to clear the recovered accounts history?')) {
                fetch('/api/clear_accounts', { method: 'POST' })
                    .then(res => res.json())
                    .then(data => {
                        if (data.status === 'success') {
                            showToast('Accounts history cleared.', 'danger');
                            loadAccounts();
                        }
                    });
            }
        }

        let sessionStats = {
            duration: '0m 0s',
            recovered: '0',
            tried: '0',
            spent: '$0.00'
        };

        function showSummaryModal() {
            document.getElementById('modal-duration').textContent = sessionStats.duration;
            document.getElementById('modal-recovered').textContent = sessionStats.recovered;
            document.getElementById('modal-tried').textContent = sessionStats.tried;
            document.getElementById('modal-spent').textContent = sessionStats.spent;
            
            const modal = document.getElementById('summary-modal');
            modal.style.display = 'flex';
            // Force reflow
            modal.offsetHeight;
            modal.style.opacity = '1';
        }

        function closeSummaryModal() {
            const modal = document.getElementById('summary-modal');
            modal.style.opacity = '0';
            setTimeout(() => {
                modal.style.display = 'none';
            }, 300);
        }

        function loadStats() {
            fetch('/api/stats')
                .then(res => res.json())
                .then(data => {
                    const grid = document.getElementById('stats-grid');
                    const dates = Object.keys(data).sort().reverse(); // Show newest dates first
                    
                    if (dates.length === 0) {
                        grid.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 2rem; grid-column: 1 / -1;">No daily history recorded yet. Runs will show up here.</div>';
                        return;
                    }
                    
                    const localDate = new Date();
                    const year = localDate.getFullYear();
                    const month = String(localDate.getMonth() + 1).padStart(2, '0');
                    const day = String(localDate.getDate()).padStart(2, '0');
                    const todayStr = `${year}-${month}-${day}`;
                    
                    grid.innerHTML = '';
                    
                    // Filter dates to only include the last 30 days (recent month)
                    const thirtyDaysAgo = new Date();
                    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
                    
                    const recentMonthDates = dates.filter(date => {
                        try {
                            const parsedDate = new Date(date + 'T00:00:00');
                            return parsedDate >= thirtyDaysAgo;
                        } catch (e) {
                            return false;
                        }
                    });
                    
                    if (recentMonthDates.length === 0) {
                        grid.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 2rem; grid-column: 1 / -1;">No history recorded within the last 30 days.</div>';
                        return;
                    }
                    
                    // Calculate active running duration portion spent TODAY
                    let activeToday = 0;
                    if (window.activeStartTime) {
                        const midnight = new Date();
                        midnight.setHours(0, 0, 0, 0);
                        const midnightEpoch = Math.floor(midnight.getTime() / 1000);
                        
                        const activeStart = window.activeStartTime;
                        const activeEnd = Math.floor(Date.now() / 1000);
                        const dayStart = Math.max(activeStart, midnightEpoch);
                        activeToday = Math.floor(Math.max(0, activeEnd - dayStart));
                    }

                    recentMonthDates.forEach(date => {
                        const dayStats = data[date];
                        const count = dayStats.recovered;
                        const spent = parseFloat(dayStats.spent || 0).toFixed(2);
                        const failedLogins = parseInt(dayStats.failed_logins || 0);
                        
                        // Format duration with readable units (hr, min, sec) and pluralization
                        let durationSec = parseInt(dayStats.duration || 0);
                        if (date === todayStr && activeToday) {
                            durationSec += activeToday;
                        }
                        
                        let durationDisplay = '0s';
                        let avgDisplay = '0s';
                        if (durationSec > 0) {
                            const hours = Math.floor(durationSec / 3600);
                            const minutes = Math.floor((durationSec % 3600) / 60);
                            const seconds = durationSec % 60;
                            
                            let parts = [];
                            if (hours > 0) {
                                parts.push(`${hours}h`);
                            }
                            if (minutes > 0) {
                                parts.push(`${minutes}m`);
                            }
                            if (seconds > 0 || parts.length === 0) {
                                parts.push(`${seconds}s`);
                            }
                            durationDisplay = parts.join(' ');
                            
                            // Calculate average time per account using success_duration
                            if (count > 0) {
                                let successSec = parseInt(dayStats.success_duration || 0);
                                if (date === todayStr && activeToday && window.activeSessionRecovered > 0) {
                                    successSec += activeToday;
                                }
                                let avgSec = 0;
                                if (successSec > 0) {
                                    avgSec = Math.round(successSec / count);
                                } else {
                                    avgSec = Math.round(durationSec / count);
                                }
                                const avgHours = Math.floor(avgSec / 3600);
                                const avgMin = Math.floor((avgSec % 3600) / 60);
                                const avgRemainingSec = avgSec % 60;
                                
                                let avgParts = [];
                                if (avgHours > 0) {
                                    avgParts.push(`${avgHours}h`);
                                }
                                if (avgMin > 0) {
                                    avgParts.push(`${avgMin}m`);
                                }
                                if (avgRemainingSec > 0 || avgParts.length === 0) {
                                    avgParts.push(`${avgRemainingSec}s`);
                                }
                                avgDisplay = avgParts.join(' ');
                            }
                        }
                        
                        let dateDisplay = date;
                        try {
                            const parsedDate = new Date(date + 'T00:00:00');
                            dateDisplay = parsedDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                        } catch (e) {}
                        
                        const card = document.createElement('div');
                        card.style.cursor = 'pointer';
                        card.style.transition = 'all 0.2s ease-in-out';
                        card.style.borderRadius = '10px';
                        card.style.padding = '1rem 0.85rem';
                        card.style.display = 'flex';
                        card.style.flexDirection = 'column';
                        card.style.alignItems = 'center';
                        card.style.gap = '0.5rem';
                        card.style.minWidth = '155px';
                        
                        if (selectedDateFilter === date) {
                            card.style.background = 'rgba(99, 102, 241, 0.12)';
                            card.style.border = '1px solid var(--primary-color)';
                            card.style.boxShadow = '0 0 12px rgba(99, 102, 241, 0.25)';
                        } else {
                            card.style.background = 'rgba(255, 255, 255, 0.03)';
                            card.style.border = '1px solid var(--border-color)';
                            card.style.boxShadow = 'none';
                        }

                        card.onmouseenter = () => {
                            if (selectedDateFilter !== date) {
                                card.style.background = 'rgba(255, 255, 255, 0.06)';
                                card.style.borderColor = 'rgba(255, 255, 255, 0.2)';
                            }
                        };
                        card.onmouseleave = () => {
                            if (selectedDateFilter !== date) {
                                card.style.background = 'rgba(255, 255, 255, 0.03)';
                                card.style.borderColor = 'var(--border-color)';
                            }
                        };

                        card.onclick = () => {
                            toggleDateFilter(date);
                        };
                        
                        card.innerHTML = `
                            <span style="font-size: 0.85rem; color: #e2e8f0; font-weight: 600;">${dateDisplay}</span>
                            <span style="font-size: 1.6rem; font-weight: 800; color: var(--success-color); margin: 0.1rem 0;">${count}</span>
                            <span style="font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600;">Recovered</span>
                            <div style="width: 100%; border-top: 1px solid rgba(255,255,255,0.05); margin-top: 0.4rem; padding-top: 0.4rem; display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.75rem; text-align: left; align-self: flex-start;">
                                <div style="display: flex; justify-content: space-between; color: var(--text-muted);">
                                    <span>Spent:</span>
                                    <span style="color: #f59e0b; font-weight: 600;">$${spent}</span>
                                </div>
                                <div style="display: flex; justify-content: space-between; color: var(--text-muted);">
                                    <span>Failed Logins:</span>
                                    <span style="color: #ef4444; font-weight: 600;">${failedLogins}</span>
                                </div>
                                <div style="display: flex; justify-content: space-between; color: var(--text-muted);">
                                    <span>Time:</span>
                                    <span style="color: #38bdf8; font-weight: 500; font-size: 0.72rem;">${durationDisplay}</span>
                                </div>
                                <div style="display: flex; justify-content: space-between; color: var(--text-muted);">
                                    <span>Avg/Acct:</span>
                                    <span style="color: #10b981; font-weight: 600;">${avgDisplay}</span>
                                </div>
                                
                            </div>
                        `;
                        
                        grid.appendChild(card);
                    });
                });
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    # Parse CLI port if passed
    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
            
    print(f"==================================================")
    print(f" Hero SMS Web UI Server running at:")
    print(f" http://127.0.0.1:{port}")
    print(f"==================================================")
    
    app.run(host='127.0.0.1', port=port, debug=False)
