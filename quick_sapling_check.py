"""Quick Sapling-only check for both original and humanized."""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

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

def fill_contenteditable(driver, el, text):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].focus()", el)
    time.sleep(0.3)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
    time.sleep(0.2)
    driver.execute_script(
        "arguments[0].focus(); document.execCommand('selectAll',false,null); document.execCommand('insertText',false,arguments[1]);",
        el, text
    )
    time.sleep(0.5)

def test_sapling(driver, text, label):
    print(f"\n[Sapling] {label}")
    driver.get("https://sapling.ai/ai-content-detector")
    time.sleep(5)

    # Find contenteditable input
    ces = driver.find_elements(By.CSS_SELECTOR, "[contenteditable]")
    el = next((c for c in ces if c.is_displayed()), None)
    if not el:
        tas = driver.find_elements(By.TAG_NAME, "textarea")
        el = next((t for t in tas if t.is_displayed()), None)

    if not el:
        print("  ERROR: no input found")
        driver.save_screenshot(str(OUT / f"sapling_v2_{label}_error.png"))
        return

    print(f"  Input: {el.tag_name}")
    fill_contenteditable(driver, el, text[:2000])
    driver.save_screenshot(str(OUT / f"sapling_v2_{label}_pasted.png"))

    # Auto-submits; also try clicking any submit button
    for b in driver.find_elements(By.TAG_NAME, "button"):
        if any(w in b.text.lower() for w in ["submit","check","detect","analyze","score"]):
            if b.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click()", b)
                print(f"  Clicked: {b.text}")
                break

    time.sleep(12)
    driver.save_screenshot(str(OUT / f"sapling_v2_{label}_result.png"))
    print(f"  Screenshot: sapling_v2_{label}_result.png")

if __name__ == "__main__":
    driver = make_driver()
    try:
        test_sapling(driver, ORIGINAL,  "original")
        time.sleep(2)
        test_sapling(driver, HUMANIZED, "humanized")
    finally:
        driver.quit()
        print("\nDone.")
