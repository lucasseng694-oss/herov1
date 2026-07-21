"""
Inspect the Facebook recovery page to find the phone input field selector.
"""

from playwright.sync_api import sync_playwright

CONFIG_DEBUG_URL = "http://127.0.0.1:9222"

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CONFIG_DEBUG_URL)
    context = browser.contexts[0]
    
    page = context.new_page()
    
    facebook_url = "https://www.facebook.com/login/identify/?ci=AdDhNqxj3bubeKaJl2BAeZF5R84lr1pqkL5Cf2GECCYUaKqqwnbEqH8-EmPr5ktGAoEAQ36l_A8pTW5y1b-Bnht-2xbUC9edHV1cW7O-udVnmbHAM1ZPy-PZmgDLgOHviiToDLhpwlAxn0WywiZA6Y8Wyn-_n9oildrH-L31Wc8bBkOgGVO7udBoB-1zGQIygpu91hfBLtBXTilJ4JnkELUeSBYYFPVtNmnr6RLDvSZ2acPoDdiDrnAOgQOAsKE15PE6ztB0mkwvJZO-LZYfUJXpRt1u"
    
    print(f"Navigating to Facebook recovery page...")
    page.goto(facebook_url, wait_until="domcontentloaded")
    
    page.bring_to_front()
    
    print("Page loaded. Searching for phone input fields...\n")
    
    # Get all input fields
    inputs = page.locator("input").all()
    print(f"Total inputs found: {len(inputs)}\n")
    
    phone_inputs = []
    
    for i, inp in enumerate(inputs):
        input_type = inp.get_attribute("type")
        input_name = inp.get_attribute("name")
        input_id = inp.get_attribute("id")
        input_placeholder = inp.get_attribute("placeholder")
        input_class = inp.get_attribute("class")
        input_aria_label = inp.get_attribute("aria-label")
        
        # Look for phone-related inputs
        is_phone_like = any(word in str([input_type, input_name, input_id, input_placeholder, input_aria_label]).lower() for word in ["phone", "tel", "mobile", "number"])
        
        print(f"Input {i}:")
        print(f"  Type: {input_type}")
        print(f"  Name: {input_name}")
        print(f"  ID: {input_id}")
        print(f"  Placeholder: {input_placeholder}")
        print(f"  Aria-label: {input_aria_label}")
        print(f"  Class: {input_class}")
        if is_phone_like:
            print(f"  ⭐ LOOKS LIKE PHONE FIELD!")
            phone_inputs.append({
                'index': i,
                'type': input_type,
                'name': input_name,
                'id': input_id,
                'placeholder': input_placeholder,
                'aria_label': input_aria_label
            })
        print()
    
    if phone_inputs:
        print("\n=== RECOMMENDED SELECTORS ===\n")
        for ph in phone_inputs:
            if ph['id']:
                print(f"target_phone_selector: \"#{ph['id']}\"")
            elif ph['name']:
                print(f"target_phone_selector: \"input[name='{ph['name']}']\""
)
            elif ph['aria_label']:
                print(f"target_phone_selector: \"input[aria-label*='{ph['aria_label']}']\""
)
            else:
                print(f"target_phone_selector: \"input[type='{ph['type']}']\"")
    else:
        print("\n⚠️  No obvious phone fields found.")
        print("Trying to find any visible input that might be for phone...")
        
        # Look at visible inputs
        visible_inputs = []
        for i, inp in enumerate(inputs):
            try:
                if inp.is_visible(timeout=1000):
                    input_type = inp.get_attribute("type")
                    input_name = inp.get_attribute("name")
                    input_id = inp.get_attribute("id")
                    input_placeholder = inp.get_attribute("placeholder")
                    
                    visible_inputs.append({
                        'index': i,
                        'type': input_type,
                        'name': input_name,
                        'id': input_id,
                        'placeholder': input_placeholder
                    })
            except:
                pass
        
        print(f"\nVisible inputs: {len(visible_inputs)}")
        for vi in visible_inputs:
            print(f"\nInput {vi['index']}:")
            print(f"  Type: {vi['type']}")
            print(f"  Name: {vi['name']}")
            print(f"  ID: {vi['id']}")
            print(f"  Placeholder: {vi['placeholder']}")
