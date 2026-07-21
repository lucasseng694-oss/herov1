"""
Inspect the Hero SMS login page to find the correct CSS selectors.
Run this after logging into Hero SMS in the remote-debugged Chrome window.
"""

from playwright.sync_api import sync_playwright

CONFIG_DEBUG_URL = "http://127.0.0.1:9222"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CONFIG_DEBUG_URL)
    context = browser.contexts[0]
    
    # Find or create a page at the login URL
    page = None
    for p_obj in context.pages:
        if "hero-sms" in p_obj.url.lower():
            page = p_obj
            break
    
    if not page:
        page = context.new_page()
        page.goto("https://hero-sms.com/")
    
    page.bring_to_front()
    
    # Find all input fields
    inputs = page.locator("input").all()
    print(f"\nFound {len(inputs)} input fields:\n")
    
    for i, inp in enumerate(inputs):
        input_type = inp.get_attribute("type")
        input_name = inp.get_attribute("name")
        input_id = inp.get_attribute("id")
        input_placeholder = inp.get_attribute("placeholder")
        
        print(f"Input {i}:")
        print(f"  Type: {input_type}")
        print(f"  Name: {input_name}")
        print(f"  ID: {input_id}")
        print(f"  Placeholder: {input_placeholder}")
        print()
    
    # Find all buttons
    buttons = page.locator("button, input[type='submit']").all()
    print(f"\nFound {len(buttons)} buttons:\n")
    
    for i, btn in enumerate(buttons):
        btn_text = btn.inner_text() if btn.locator("..").is_visible() else "(hidden)"
        btn_type = btn.get_attribute("type")
        btn_name = btn.get_attribute("name")
        btn_id = btn.get_attribute("id")
        
        print(f"Button {i}:")
        print(f"  Type: {btn_type}")
        print(f"  Name: {btn_name}")
        print(f"  ID: {btn_id}")
        print(f"  Text: {btn_text}")
        print()
    
    print("\nUse these values to update the CONFIG in hero_sms_automation.py:")
    print("- username_selector: CSS selector for the email/username input")
    print("- password_selector: CSS selector for the password input")
    print("- submit_selector: CSS selector for the submit button")
