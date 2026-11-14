"""
ConnectPilot - Legacy Standalone Runner

For full features (dedicated persistent browser profile, Google SSO, multi-layered
invitation verification, SQLite rate limiting, and statistics), run `main.py` or `cli.py`:
    python main.py --limit 15
    python cli.py run --limit 15
    python cli.py stats

All paths and credentials are loaded from `.env` (see `.env.example` and `SETUP.md`).
"""

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from dotenv import load_dotenv
from pathlib import Path
import logging
import os
import time

REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LINKEDIN_USERNAME = os.getenv("LINKEDIN_USERNAME", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")
MAX_RETRIES = 5

SEARCH_LINK = os.getenv(
    "SEARCH_LINK",
    "https://www.linkedin.com/search/results/people/?keywords=software%20engineer",
)
BASE_CONNECTION_MESSAGE = os.getenv(
    "CONNECTION_MESSAGE_TEMPLATES",
    "Hi there, I came across your profile and would love to connect!",
).split("|")[0]

MAX_CONNECT_REQUESTS = int(os.getenv("MAX_CONNECT_REQUESTS_PER_SESSION", "15"))


def login_to_linkedin(driver, username, password):
    try:
        driver.get("https://www.linkedin.com/login")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "username")))

        username_field = driver.find_element(By.ID, "username")
        username_field.send_keys(username)

        password_field = driver.find_element(By.ID, "password")
        password_field.send_keys(password)
        password_field.send_keys(Keys.RETURN)
        time.sleep(5)
        WebDriverWait(driver, 20).until(EC.url_contains("/feed"))
        logging.info("Successfully logged into LinkedIn.")
        time.sleep(5)
    except Exception as e:
        logging.error(f"Error during LinkedIn login: {e}")


def go_to_next_page(driver):
    try:
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        next_page_button = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//button[@aria-label='Next']"))
        )
        next_page_button.click()
        logging.info("Navigated to the next page")
        time.sleep(5)
    except NoSuchElementException as e:
        logging.error(f"Element not found: {e}")
        return False
    except Exception as e:
        logging.error(f"Error navigating to the next page: {e}")
        return False
    return True


def scroll_down(driver):
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(5)
    except Exception as e:
        logging.error(f"Error during scrolling down: {e}")


def refresh_page(driver, retries):
    for attempt in range(1, retries + 1):
        try:
            logging.info(f"Attempt {attempt}/{retries}: Refreshing the page.")
            driver.refresh()
            time.sleep(5)
            return True
        except Exception as e:
            logging.error(f"Error during page refresh: {e}")

        if attempt == retries:
            logging.error("Maximum retries reached. Exiting the program.")
            driver.quit()
            raise SystemExit(1)
    return False


def handle_connect_button_with_retry(driver, button):
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            button.click()
            time.sleep(2)

            add_note_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//button[@aria-label='Add a note']"))
            )
            add_note_button.click()
            time.sleep(2)

            message_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//textarea[@name='message']"))
            )
            message_box.send_keys(BASE_CONNECTION_MESSAGE)
            time.sleep(2)

            send_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//span[contains(@class, 'artdeco-button__text') and text()='Send']")
                )
            )
            send_button.click()
            logging.info("Sent connection request with a custom note.")
            time.sleep(2)
            return
        except ElementClickInterceptedException as e:
            logging.error(f"Error: Element not clickable, retrying... {e}")
            retry_count += 1
            if not refresh_page(driver, retry_count):
                logging.error("Unable to resolve the error after retries. Exiting.")
                break
        except Exception as e:
            logging.error(f"Error handling 'Connect' button: {e}")
            break


def handle_follow_button(button):
    try:
        button.click()
        logging.info("Followed the user.")
        time.sleep(1)
    except Exception as e:
        logging.error(f"Error handling 'Follow' button: {e}")


def process_buttons(driver):
    try:
        driver.get(SEARCH_LINK)
        scroll_down(driver)
        time.sleep(5)

        connect_requests_sent = 0
        working = True

        while working:
            buttons = driver.find_elements(By.TAG_NAME, "button")
            connect_buttons_count = sum(1 for b in buttons if b.text.strip().lower() == "connect")
            follow_buttons_count = sum(1 for b in buttons if b.text.strip().lower() == "follow")
            logging.info(f"Total 'Connect' buttons on the page: {connect_buttons_count}")
            logging.info(f"Total 'Follow' buttons on the page: {follow_buttons_count}")

            for button in buttons:
                button_text = button.text.strip().lower()
                if button_text == "connect" and connect_requests_sent < MAX_CONNECT_REQUESTS:
                    handle_connect_button_with_retry(driver, button)
                    connect_requests_sent += 1
                    if connect_requests_sent >= MAX_CONNECT_REQUESTS:
                        logging.info(
                            f"Reached the limit of {MAX_CONNECT_REQUESTS} connection requests. Stopping."
                        )
                        working = False
                        break
                    time.sleep(5)
                elif button_text == "follow":
                    handle_follow_button(button)
                    time.sleep(5)

            if not go_to_next_page(driver):
                logging.info("No more pages to process. Exiting.")
                break

            scroll_down(driver)
            time.sleep(5)

    except Exception as e:
        logging.error(f"Error while processing buttons: {e}")


def _create_legacy_driver():
    browser_type = os.getenv("BROWSER_TYPE", "edge").strip().lower()
    headless = (os.getenv("HEADLESS") or os.getenv("HEADLESS_MODE") or "false").strip().lower() == "true"

    if browser_type == "edge":
        edge_options = EdgeOptions()
        edge_options.add_argument("--disable-blink-features=AutomationControlled")
        if headless:
            edge_options.add_argument("--headless=new")
        edge_bin = os.getenv("EDGE_BINARY_PATH", "").strip()
        if edge_bin:
            edge_options.binary_location = edge_bin
        return webdriver.Edge(options=edge_options)

    if browser_type == "chrome" or os.getenv("USE_CHROME", "false").lower() == "true":
        chrome_options = ChromeOptions()
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        if headless:
            chrome_options.add_argument("--headless=new")
        chrome_bin = os.getenv("CHROME_BINARY_PATH", "").strip()
        if chrome_bin:
            chrome_options.binary_location = chrome_bin
        return webdriver.Chrome(options=chrome_options)

    firefox_options = FirefoxOptions()
    firefox_bin = os.getenv("FIREFOX_BINARY_PATH", "").strip()
    if firefox_bin:
        firefox_options.binary_location = firefox_bin
    if headless:
        firefox_options.add_argument("--headless")
    geckodriver_path = os.getenv("GECKODRIVER_PATH", "").strip()
    if geckodriver_path and Path(geckodriver_path).exists():
        return webdriver.Firefox(service=FirefoxService(geckodriver_path), options=firefox_options)
    return webdriver.Firefox(options=firefox_options)


if __name__ == "__main__":
    driver = _create_legacy_driver()
    try:
        login_to_linkedin(driver, LINKEDIN_USERNAME, LINKEDIN_PASSWORD)
        process_buttons(driver)
    finally:
        driver.quit()
