import os
import re
from datetime import datetime

def analyze_recovery():
    accounts_file = "recovered_accounts.txt"
    if not os.path.exists(accounts_file):
        print(f"Error: {accounts_file} not found in the current directory.")
        return

    # Pattern to match: --- Recovered Account [2026-07-14 12:27:27 | Your Chrome ] ---
    pattern = re.compile(r"--- Recovered Account\s*\[([\d\-\s:]+)\s*\|.*\]\s*---")

    recoveries_by_day = {}

    with open(accounts_file, "r", encoding="utf-8") as f:
        for line in f:
            match = pattern.match(line.strip())
            if match:
                dt_str = match.group(1).strip()
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    day_str = dt.strftime("%Y-%m-%d")
                    recoveries_by_day.setdefault(day_str, []).append(dt)
                except Exception:
                    pass

    if not recoveries_by_day:
        print("No recovered accounts with valid timestamps found in the file.")
        return

    print("=" * 75)
    print(f"{'DATE':<12} | {'TOTAL RECOVERED':<15} | {'AVG TIME BETWEEN RECOVERIES':<30}")
    print("=" * 75)

    for day in sorted(recoveries_by_day.keys(), reverse=True):
        times = sorted(recoveries_by_day[day])
        count = len(times)
        
        if count <= 1:
            avg_str = "N/A (Single recovery)"
        else:
            # Calculate the differences between consecutive recoveries on this day
            intervals = []
            for i in range(1, len(times)):
                diff = (times[i] - times[i-1]).total_seconds()
                intervals.append(diff)
            
            avg_seconds = sum(intervals) / len(intervals)
            
            hours = int(avg_seconds // 3600)
            minutes = int((avg_seconds % 3600) // 60)
            seconds = int(avg_seconds % 60)
            
            parts = []
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0 or hours > 0:
                parts.append(f"{minutes}m")
            parts.append(f"{seconds}s")
            avg_str = " ".join(parts)

        print(f"{day:<12} | {count:<15} | {avg_str:<30}")
        
    print("=" * 75)
    print("\nNote: 'Avg Time Between Recoveries' measures the real-world time elapsed")
    print("   between consecutive successes on that day (including script run times, pause times,")
    print("   and manual interventions).")

if __name__ == "__main__":
    analyze_recovery()
