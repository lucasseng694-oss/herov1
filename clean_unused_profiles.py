import os
import re
import shutil
import sqlite3
import tempfile

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
                temp_dir = tempfile.gettempdir()
                temp_cookie_path = os.path.join(temp_dir, f"temp_clean_check_{os.path.basename(profile_path)}.db")
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
                # To be safe, if we can't read it but the file exists, assume it might be logged in
                return True
    return False

def get_folder_size(path: str) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_folder_size(entry.path)
    except Exception:
        pass
    return total

def clean_profiles():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folders_to_scan = [
        os.path.join(base_dir, "chrome_profiles_fb"),
        os.path.join(base_dir, "chrome_profiles")
    ]
    
    total_freed = 0
    deleted_count = 0
    kept_count = 0

    print("=" * 60)
    print("CHROME PROFILE DISK CLEANUP UTILITY")
    print("=" * 60)

    for scan_dir in folders_to_scan:
        if not os.path.exists(scan_dir):
            continue
            
        print(f"\nScanning: {os.path.basename(scan_dir)}...")
        try:
            for item in os.listdir(scan_dir):
                item_path = os.path.join(scan_dir, item)
                if os.path.isdir(item_path) and re.match(r"^Profile\s*\d+$", item, re.I):
                    # Check if this profile has a logged-in session
                    if is_profile_logged_in(item_path):
                        print(f"Kept: {item} (Active Facebook session detected)")
                        kept_count += 1
                    else:
                        # Clean up this empty profile
                        size = get_folder_size(item_path)
                        try:
                            # Verify if there is a lock file indicating Chrome is running
                            lock_file = os.path.join(item_path, "lockfile")
                            if os.path.exists(lock_file):
                                print(f"Skipped: {item} (Folder is currently locked/in-use by Chrome)")
                                kept_count += 1
                                continue
                                
                            shutil.rmtree(item_path)
                            size_mb = size / (1024 * 1024)
                            print(f"Deleted empty profile: {item} ({size_mb:.1f} MB freed)")
                            total_freed += size
                            deleted_count += 1
                        except Exception as delete_err:
                            print(f"Failed to delete {item}: {delete_err}")
                            kept_count += 1
        except Exception as e:
            print(f"Error scanning directory: {e}")

    # Remove temporary database copies in the root folder if any exist
    for file in os.listdir(base_dir):
        if file.startswith("temp_") and file.endswith(".db"):
            try:
                os.remove(os.path.join(base_dir, file))
                print(f"Deleted temp database file: {file}")
            except:
                pass

    print("\n" + "=" * 60)
    print("CLEANUP SUMMARY:")
    print(f"   - Active profiles kept   : {kept_count}")
    print(f"   - Empty/failed profiles cleaned: {deleted_count}")
    print(f"   - Total Disk Space Saved  : {total_freed / (1024 * 1024 * 1024):.2f} GB")
    print("=" * 60)

if __name__ == "__main__":
    clean_profiles()
