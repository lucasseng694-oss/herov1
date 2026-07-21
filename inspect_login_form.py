"""
Click the Log in button and inspect the login form that appears.
"""

from playwright.sync_api import sync_playwright
import time

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
    
    print("Looking for 'Log in' button...")
    
    # Click the Log in button (there are multiple, try the first visible one)
    try:
        page.get_by_text("Log in").first.click(timeout=5000)
        print("Clicked 'Log in' button")
        time.sleep(2)  # Wait for modal/form to appear
    except Exception as e:
        print(f"Could not click Log in button: {e}")
    
    page.bring_to_front()
    
    # Now look for login form fields
    inputs = page.locator("input").all()
    print(f"\nFound {len(inputs)} input fields:\n")
    
    login_inputs = []
    for i, inp in enumerate(inputs):
        input_type = inp.get_attribute("type")
        input_name = inp.get_attribute("name")
        input_id = inp.get_attribute("id")
        input_placeholder = inp.get_attribute("placeholder")
        
        # Only show text/email/password inputs (filter out radio/checkbox)
        if input_type in ["text", "email", "password", None]:
            print(f"Input {i}:")
            print(f"  Type: {input_type}")
            print(f"  Name: {input_name}")
            print(f"  ID: {input_id}")
            print(f"  Placeholder: {input_placeholder}")
            print()
            login_inputs.append({
                'index': i,
                'type': input_type,
                'name': input_name,
                'id': input_id,
                'placeholder': input_placeholder
            })
    
    # Build selectors based on what we found
    print("\n=== RECOMMENDED SELECTORS ===\n")
    
    email_input = next((inp for inp in login_inputs if inp['type'] in ['email', 'text'] and inp['placeholder'] and 'email' in inp['placeholder'].lower()), None)
    if not email_input:
        email_input = next((inp for inp in login_inputs if inp['name'] and 'email' in inp['name'].lower()), None)
    if not email_input:
        email_input = next((inp for inp in login_inputs if inp['id'] and 'email' in inp['id'].lower()), None)
    if not email_input and login_inputs:
        email_input = login_inputs[0]
    
    password_input = next((inp for inp in login_inputs if inp['type'] == 'password'), None)
    if not password_input:
        password_input = next((inp for inp in login_inputs if inp['name'] and 'password' in inp['name'].lower()), None)
    if not password_input and len(login_inputs) > 1:
        password_input = login_inputs[1]
    
    if email_input:
        if email_input['id']:
            print(f"username_selector: \"#{email_input['id']}\"")
        elif email_input['name']:
            print(f"username_selector: \"input[name='{email_input['name']}']\""
)
        else:
            print(f"username_selector: \"input[placeholder*='{email_input['placeholder']}']\""  if email_input['placeholder'] else "# Could not determine email selector")
    
    if password_input:
        if password_input['id']:
            print(f"password_selector: \"#{password_input['id']}\"")
        elif password_input['name']:
            print(f"password_selector: \"input[name='{password_input['name']}']\""
)
        else:
            print(f"password_selector: \"input[type='password']\"")
    
    print(f"submit_selector: \"button[type='submit'], button:has-text('Log in'), button:has-text('Sign in')\"")
