"""
Inspect the Hero SMS purchases table to find the correct phone number selector.
"""

from playwright.sync_api import sync_playwright

CONFIG_DEBUG_URL = "http://127.0.0.1:9222"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CONFIG_DEBUG_URL)
    context = browser.contexts[0]
    
    # Find the Hero SMS page
    page = None
    for p_obj in context.pages:
        if "hero-sms" in p_obj.url.lower():
            page = p_obj
            break
    
    if not page:
        print("Hero SMS page not found in open tabs")
        exit(1)
    
    page.bring_to_front()
    
    print("Inspecting Hero SMS purchases table...\n")
    
    # Look for the table rows with phone numbers
    # Check for table cells that might contain the number
    
    # Look for the "My purchases" section
    my_purchases_heading = page.locator("text=My purchases").first
    try:
        my_purchases_heading.wait_for(timeout=5000)
        print("✅ Found 'My purchases' section\n")
    except:
        print("⚠️  'My purchases' heading not found")
    
    # Find all rows in the table
    rows = page.locator("tr, [role='row']").all()
    print(f"Found {len(rows)} table rows\n")
    
    phone_cells = []
    
    for i, row in enumerate(rows):
        # Get all cells in the row
        cells = row.locator("td, [role='gridcell']").all()
        
        if len(cells) > 0:
            print(f"Row {i}: {len(cells)} cells")
            for j, cell in enumerate(cells):
                try:
                    cell_text = cell.inner_text(timeout=2000).strip()
                    cell_classes = cell.get_attribute("class")
                    
                    # Look for phone number patterns
                    if any(char in cell_text for char in ['+', '(', ')', '-', ' ']) and len(cell_text) > 5:
                        print(f"  Cell {j}: {cell_text}")
                        if '+' in cell_text or '(' in cell_text:
                            print(f"    ⭐ LOOKS LIKE PHONE NUMBER!")
                            phone_cells.append({
                                'row': i,
                                'cell': j,
                                'text': cell_text,
                                'classes': cell_classes
                            })
                except:
                    pass
    
    print("\n=== PHONE NUMBERS FOUND ===\n")
    for pc in phone_cells:
        print(f"Row {pc['row']}, Cell {pc['cell']}: {pc['text']}")
    
    if phone_cells:
        print("\n=== RECOMMENDED SELECTOR ===\n")
        print("Option 1: Get the latest (last) purchased number")
        print("purchased_number_selector: \"tr:last-child td\"  # Then extract phone from first cell")
        
        print("\nOption 2: Get all numbers and pick the last one")
        print("purchased_number_selector: \"td:first-child\"  # Cell containing the phone number")
        
        print("\nOption 3: Look for the number that was just added (first in the list)")
        print("purchased_number_selector: \"tr:first-of-type td:first-child\"")
        
        print("\nRecommendation: Use the most recently added number (usually at top)")
        print("Search for any cell containing a phone number pattern")
    
    # Also check if there's a copy button we can use
    copy_buttons = page.locator("button[title*='copy'], button[title*='Copy'], [aria-label*='copy']").all()
    print(f"\n\nFound {len(copy_buttons)} copy buttons")
    if copy_buttons:
        print("✅ There are copy buttons - we could click these instead!")
