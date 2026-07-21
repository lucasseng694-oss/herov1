import re
import time
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = None
        for p_obj in context.pages:
            if "hero-sms" in p_obj.url.lower():
                page = p_obj
                break
        
        if not page:
            print("No Hero SMS page found.")
            return

        print("\n--- Clicking Facebook ---")
        try:
            loc = page.get_by_text(re.compile("^facebook$", re.I)).first
            if loc.is_visible():
                loc.click(timeout=5000)
                print("Clicked Facebook!")
                time.sleep(3)
            else:
                print("Facebook not visible.")
        except Exception as e:
            print(f"Error clicking Facebook: {e}")

        print("\n--- Finding Country Search Input ---")
        try:
            # The country search box might have a placeholder like 'Search by country'
            country_search = page.get_by_placeholder(re.compile("country", re.I)).first
            if country_search.is_visible():
                print("Found country search input! Typing 'Brazil'...")
                country_search.fill("Brazil")
                time.sleep(2)
            else:
                print("Country search input not visible. Trying alternative...")
                # Try finding any input that is visible after clicking facebook
                inputs = page.locator("input").all()
                for inp in inputs:
                    if inp.is_visible():
                        print(f"Found visible input, placeholder: {inp.get_attribute('placeholder')}")
                        inp.fill("Brazil")
                        time.sleep(2)
                        break
        except Exception as e:
            print(f"Error filling search: {e}")

        print("\n--- Looking for Brazil Button ---")
        try:
            brazil_loc = page.locator("li[role='button']").filter(has_text=re.compile("Brazil", re.I)).first
            if brazil_loc.is_visible():
                print(f"Found Brazil button! Text: {brazil_loc.inner_text().strip()!r}")
            else:
                print("Brazil button still not visible.")
        except Exception as e:
            print(f"Error looking for Brazil button: {e}")

if __name__ == "__main__":
    main()
