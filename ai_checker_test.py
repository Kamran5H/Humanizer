"""
AI Checker Automation — tests original vs humanized text on 3 platforms:
  1. ZeroGPT   (zerogpt.net)
  2. Sapling   (sapling.ai/ai-content-detector)
  3. ContentAtScale (contentatscale.ai/ai-content-detector)

Saves screenshots to BrandFinder/checker_results/
"""

import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

BASE = Path(__file__).parent
OUT  = BASE / "checker_results"
OUT.mkdir(exist_ok=True)

ORIGINAL  = (BASE / "test_ai_document.txt").read_text(encoding="utf-8").strip()
HUMANIZED = (BASE / "test_ai_document_humanized.txt").read_text(encoding="utf-8").strip()

def make_driver():
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(3)
    return driver

def ss(driver, name):
    path = str(OUT / f"{name}.png")
    driver.save_screenshot(path)
    print(f"  [screenshot] {name}.png")
    return path

def js_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click()", el)

def fill_textarea(driver, el, text):
    """Fill a real <textarea> or <input> via JS value setter."""
    driver.execute_script("""
        arguments[0].scrollIntoView({block:'center'});
        arguments[0].focus();
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));
        arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
    """, el, text)

def fill_contenteditable(driver, el, text):
    """Replace contenteditable content using execCommand so React/Vue state updates."""
    driver.execute_script("""
        arguments[0].scrollIntoView({block:'center'});
        arguments[0].focus();
    """, el)
    time.sleep(0.3)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
    time.sleep(0.2)
    driver.execute_script("""
        arguments[0].focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, arguments[1]);
    """, el, text)
    time.sleep(0.3)

def find_input(driver):
    """Find the primary text input on the page."""
    # Try textarea first
    tas = driver.find_elements(By.TAG_NAME, "textarea")
    for ta in tas:
        if ta.is_displayed() and ta.get_attribute("readonly") is None:
            return ta, "textarea"
    # Try any contenteditable (div, p, etc.)
    ces = driver.find_elements(By.CSS_SELECTOR, "[contenteditable]")
    for ce in ces:
        if ce.is_displayed():
            return ce, "contenteditable"
    return None, None

def fill_and_screenshot(driver, text, label_prefix, wait_secs=15):
    el, kind = find_input(driver)
    if el is None:
        print(f"  ERROR: no input found")
        ss(driver, f"{label_prefix}_error")
        return False

    print(f"  Found input: {kind} ({el.tag_name})")
    if kind == "textarea":
        fill_textarea(driver, el, text)
    else:
        fill_contenteditable(driver, el, text)

    time.sleep(0.5)
    ss(driver, f"{label_prefix}_pasted")
    return True


# ─────────────────────────────────────────────
# 1. ZeroGPT
# ─────────────────────────────────────────────

def safe_get(driver, url, retries=2, delay=3):
    for i in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            print(f"  [retry {i+1}] get failed: {e}")
            time.sleep(delay)
    return False

def test_zerogpt(driver, text, label):
    print(f"\n[ZeroGPT] {label}")
    if not safe_get(driver, "https://www.zerogpt.net"):
        print("  ERROR: could not load page")
        return
    time.sleep(5)

    if not fill_and_screenshot(driver, text[:2000], f"zerogpt_{label}"):
        return

    # Click detect via JS to bypass ad overlay
    clicked = False
    for sel in ["#checkButton", ".detector-btn", "button[class*='detect']",
                "button[class*='check']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            js_click(driver, els[0])
            print(f"  Clicked: {els[0].text or sel}")
            clicked = True
            break
    if not clicked:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            if any(w in b.text.lower() for w in ["detect", "check", "scan", "analyze"]):
                js_click(driver, b)
                print(f"  Clicked: {b.text}")
                clicked = True
                break
    if not clicked:
        print("  WARNING: no button found")

    time.sleep(20)
    ss(driver, f"zerogpt_{label}_result")


# ─────────────────────────────────────────────
# 2. Sapling AI Detector
# ─────────────────────────────────────────────

def test_sapling(driver, text, label):
    print(f"\n[Sapling] {label}")
    if not safe_get(driver, "https://sapling.ai/ai-content-detector"):
        print("  ERROR: could not load page")
        return
    time.sleep(5)

    # Dismiss banner
    try:
        close_btns = driver.find_elements(By.CSS_SELECTOR, "button.close, [aria-label='close'], .dismiss")
        for b in close_btns:
            if b.is_displayed():
                js_click(driver, b)
                break
    except:
        pass

    if not fill_and_screenshot(driver, text[:2000], f"sapling_{label}"):
        return

    # Submit — Sapling auto-detects on input; look for an explicit button too
    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        t = b.text.lower()
        if any(w in t for w in ["submit", "check", "detect", "analyze", "scan", "score"]):
            if b.is_displayed():
                js_click(driver, b)
                print(f"  Clicked: {b.text}")
                clicked = True
                break
    if not clicked:
        print("  (no submit button — Sapling may auto-analyze)")

    time.sleep(12)
    ss(driver, f"sapling_{label}_result")


# ─────────────────────────────────────────────
# 3. Content at Scale
# ─────────────────────────────────────────────

def test_contentatscale(driver, text, label):
    print(f"\n[ContentAtScale] {label}")
    if not safe_get(driver, "https://contentatscale.ai/ai-content-detector/"):
        print("  ERROR: could not load page")
        return
    time.sleep(5)

    # Dismiss cookie/popup
    try:
        for sel in ["button[class*='close']", "#onetrust-accept-btn-handler",
                    "button[class*='accept']", ".cookie-accept"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                if el.is_displayed():
                    js_click(driver, el)
                    time.sleep(0.5)
                    break
    except:
        pass

    if not fill_and_screenshot(driver, text[:2000], f"contentatscale_{label}"):
        return

    # Click the scan/check button
    clicked = False
    for b in driver.find_elements(By.TAG_NAME, "button"):
        t = b.text.lower()
        if any(w in t for w in ["scan", "check", "detect", "analyze", "submit"]):
            if b.is_displayed():
                js_click(driver, b)
                print(f"  Clicked: {b.text}")
                clicked = True
                break
    if not clicked:
        for sel in ["input[type='submit']", "button[type='submit']"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els and els[0].is_displayed():
                js_click(driver, els[0])
                clicked = True
                break
    if not clicked:
        print("  WARNING: no button found")

    time.sleep(15)
    ss(driver, f"contentatscale_{label}_result")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("AI CHECKER AUTOMATION — Round 2")
    print("=" * 60)
    print(f"Results: {OUT}\n")

    driver = make_driver()
    try:
        for fn, lbl in [
            (test_zerogpt,        "original"),
            (test_zerogpt,        "humanized"),
            (test_sapling,        "original"),
            (test_sapling,        "humanized"),
            (test_contentatscale, "original"),
            (test_contentatscale, "humanized"),
        ]:
            text = ORIGINAL if lbl == "original" else HUMANIZED
            try:
                fn(driver, text, lbl)
            except Exception as e:
                print(f"  SKIP ({fn.__name__}/{lbl}): {e}")
            time.sleep(2)

    finally:
        driver.quit()
        print("\n" + "=" * 60)
        print("DONE. Screenshots:", OUT)
