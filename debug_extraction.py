"""
Debug script to see what the bot is actually reading from the table.
"""

from playwright.sync_api import sync_playwright
import re

CONFIG_DEBUG_URL = "http://127.0.0.1:9222"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CONFIG_DEBUG_URL)
    context = browser.contexts[0]
    
    # Find Hero SMS page
    page = None
    for p_obj in context.pages:
        if "hero-sms" in p_obj.url.lower():
            page = p_obj
            break
    
    if not page:
        print("❌ Hero SMS page not found")
        exit(1)
    
    page.bring_to_front()
    
    print("🔍 Debugging table extraction...\n")
    
    # Test the exact selector the script uses
    selector = "tr:last-child td:first-child"
    print(f"Using selector: {selector}")
    
    try:
        cell = page.locator(selector).first
        text = cell.inner_text(timeout=5000).strip()
        print(f"✅ Extracted text: '{text}'")
        print(f"   Length: {len(text)}")
        print(f"   Contains '+': {'+' in text}")
        print(f"   Contains digits: {any(c.isdigit() for c in text)}")
        
        # Check if it would pass the validation
        if text and len(text) > 5 and ('+' in text or any(c.isdigit() for c in text)):
            print(f"\n✅ This SHOULD work! Number is valid.")
        else:
            print(f"\n❌ This FAILS validation. Issue:")
            if not text:
                print("   - Text is empty")
            if len(text) <= 5:
                print(f"   - Text too short: {len(text)} chars")
            if '+' not in text and not any(c.isdigit() for c in text):
                print("   - No + and no digits")
    except Exception as e:
        print(f"❌ Error extracting: {e}")
    
    # Also try finding all table cells to see the structure
    print("\n\n📋 All table rows and first columns:")
    rows = page.locator("tr").all()
    print(f"Total rows: {len(rows)}\n")
    
    for i, row in enumerate(rows[-3:]):  # Last 3 rows
        try:
            first_cell = row.locator("td:first-child").first
            text = first_cell.inner_text(timeout=2000).strip()
            print(f"Row {i}: '{text}'")
        except Exception as e:
            print(f"Row {i}: Error - {e}")
