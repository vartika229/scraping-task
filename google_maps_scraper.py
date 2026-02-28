import argparse
import logging
import random
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Basic regex for email extraction
EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

def is_place_url(url: str) -> bool:
    """Detect if the URL is for a single place listing."""
    return "/place/" in url or url.startswith("https://www.google.com/maps/place")

def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    time.sleep(random.uniform(min_sec, max_sec))

def extract_email_from_website(page: Page, website_url: str) -> Optional[str]:
    if not website_url or pd.isna(website_url):
        return None
    try:
        new_page = page.context.new_page()
        # Fast config: shorter timeout, wait until domcontentloaded to save time.
        new_page.goto(website_url, timeout=15000, wait_until="domcontentloaded")
        content = new_page.content()
        emails = re.findall(EMAIL_REGEX, content)
        new_page.close()
        
        # Filter out common false positives (e.g., image files)
        valid_emails = [e for e in emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))]
        if valid_emails:
            # Simple heuristic: prioritize info@, contact@, or just pick the first one
            for email in valid_emails:
                if email.lower().startswith(('info@', 'contact@', 'hello@', 'support@')):
                    return email
            return valid_emails[0]
    except Exception as e:
        logger.debug(f"Failed to extract email from {website_url}: {e}")
        try:
            new_page.close()
        except Exception:
            pass
    return None

def extract_place_details(page: Page, url: str, extract_email: bool = False) -> Dict:
    """Extracts details from a single place listing."""
    logger.info(f"Extracting details for: {url}")
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        logger.warning(f"Timeout while loading {url}, attempting to extract what is available.")
    random_delay(2, 4)

    data = {
        "Company Name": None,
        "Phone Number": None,
        "Email": None,
        "Website": None,
        "Rating": None,
        "Review Count": None,
        "Category": None,
        "Address": None,
        "Google Maps URL": url
    }

    # Extract Company Name
    try:
        title_element = page.locator("h1.DUwDvf").first
        if title_element.is_visible():
            data["Company Name"] = title_element.inner_text()
    except Exception:
        pass

    # Extract Rating and Reviews
    try:
        # Rating usually has an aria-label like "4.7 stars" or is placed right next to reviews
        rating_element = page.locator("div.F7nice > span > span[aria-hidden='true']").first
        if rating_element.is_visible():
            data["Rating"] = rating_element.inner_text()
            
        reviews_element = page.locator("div.F7nice span[aria-label*='reviews']").first
        if reviews_element.is_visible():
            text = reviews_element.inner_text().replace("(", "").replace(")", "").replace(",", "")
            # Filter just numbers in case of extra text
            nums = re.findall(r'\d+', text)
            if nums:
                data["Review Count"] = int(''.join(nums))
    except Exception:
        pass

    # Extract Category
    try:
        category_button = page.locator("button.DkEaL").first
        if category_button.is_visible():
            data["Category"] = category_button.inner_text()
    except Exception:
        pass

    # Extract Address
    try:
        address_element = page.locator("button[data-item-id='address'] div.Io6YTe").first
        if address_element.is_visible():
            data["Address"] = address_element.inner_text()
    except Exception:
        pass

    # Extract Phone
    try:
        phone_element = page.locator("button[data-item-id^='phone:tel:'] div.Io6YTe").first
        if phone_element.is_visible():
            data["Phone Number"] = phone_element.inner_text()
    except Exception:
        pass

    # Extract Website
    try:
        website_element = page.locator("a[data-item-id='authority']").first
        if website_element.is_visible():
            data["Website"] = website_element.get_attribute("href")
    except Exception:
        pass

    if extract_email and data["Website"]:
        data["Email"] = extract_email_from_website(page, data["Website"])

    return data

def scrape_search_results(page: Page, url: str, max_results: int = 20, extract_email: bool = False) -> List[Dict]:
    """Scrolls down search results and extracts each listed place."""
    logger.info(f"Scraping search results for: {url}")
    page.goto(url, wait_until="networkidle", timeout=30000)
    random_delay(2, 4)

    # Click accept cookies if prompted (useful for European IP addresses)
    try:
        cookie_button = page.locator("button:has-text('Accept all')").first
        if cookie_button.is_visible(timeout=3000):
            cookie_button.click()
            random_delay(1, 2)
    except Exception:
        pass

    # Try to find the scrollable container. It typically has role="feed"
    feed_scrollable = page.locator("div[role='feed']").first
    if not feed_scrollable.is_visible(timeout=5000):
        # Fallback locator if google changes the role
        feed_scrollable = page.locator("div.m6QErb[aria-label*='Results']").first

    places = []
    processed_urls = set()

    last_places_count = 0
    consecutive_no_new = 0

    while len(places) < max_results:
        # All main listing wrappers are a tags with class hfpxzc
        links = page.locator("a.hfpxzc").all()
        
        for link in links:
            href = link.get_attribute("href")
            if href and href not in processed_urls:
                processed_urls.add(href)
                places.append(href)
                if len(places) >= max_results:
                    break
        
        if len(places) >= max_results:
            break
            
        if len(places) == last_places_count:
            consecutive_no_new += 1
            if consecutive_no_new >= 3:
                logger.info("No new places found after 3 scroll attempts. Breaking.")
                break
        else:
            consecutive_no_new = 0
            last_places_count = len(places)

        # Scroll down
        if feed_scrollable.is_visible():
            try:
                page.evaluate("element => element.scrollBy(0, 800)", feed_scrollable.element_handle())
                random_delay(1.5, 3.0)
                
                # Check for "You've reached the end of the list" element
                end_of_list = page.locator("text='You\\'ve reached the end of the list'").first
                if end_of_list.is_visible(timeout=1000):
                    logger.info("Reached end of list.")
                    break
            except Exception as e:
                logger.warning(f"Error scrolling: {e}")
                break
        else:
            logger.warning("Feed scrollable not found in DOM.")
            break

    logger.info(f"Collected {len(places)} listing URLs. Extracting specific details...")
    
    results = []
    for i, place_url in enumerate(places, 1):
        logger.info(f"Processing listing {i}/{len(places)}")
        try:
            details = extract_place_details(page, place_url, extract_email)
            results.append(details)
        except Exception as e:
            logger.error(f"Error extracting {place_url}: {e}")
            
    return results

def save_data(data: List[Dict], output_file: str, file_format: str):
    """Save extracted data to an output file."""
    if not data:
        logger.warning("No data to save.")
        return

    df = pd.DataFrame(data)
    
    try:
        if file_format == "csv":
            df.to_csv(output_file, index=False)
        elif file_format == "json":
            df.to_json(output_file, orient="records", indent=4)
        elif file_format == "xlsx":
            df.to_excel(output_file, index=False)
        else:
            logger.error(f"Unsupported format: {file_format}")
            return
            
        logger.info(f"Successfully saved {len(data)} records to {output_file}")
    except Exception as e:
        logger.error(f"Failed to save data: {e}")

def main():
    parser = argparse.ArgumentParser(description="Google Maps Scraper")
    parser.add_argument("--url", required=True, help="Google Maps Search or Place URL")
    parser.add_argument("--output", required=True, help="Output file name")
    parser.add_argument("--format", choices=["csv", "json", "xlsx"], default="csv", help="Output format (default: csv)")
    parser.add_argument("--max", type=int, default=20, help="Max results for search mode (default: 20)")
    parser.add_argument("--emails", action="store_true", help="Opt-in email extraction (scans business websites)")
    parser.add_argument("--visible", action="store_true", help="Show browser window for debugging")
    args = parser.parse_args()

    # Automatically ensure output has correct extension
    if not args.output.endswith(f".{args.format}"):
        file_name = args.output.split('.')[0] if '.' in args.output else args.output
        args.output = f"{file_name}.{args.format}"

    logger.info("Starting up browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.visible,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )
        page = context.new_page()

        # Try to block images to improve performance
        page.route("**/*", lambda route: route.continue_() if route.request.resource_type not in ["image", "media", "font"] else route.abort())

        try:
            if is_place_url(args.url):
                logger.info("Detected Single Place URL")
                data = extract_place_details(page, args.url, extract_email=args.emails)
                results = [data]
            else:
                logger.info("Detected Search Results URL")
                results = scrape_search_results(page, args.url, max_results=args.max, extract_email=args.emails)
                
            save_data(results, args.output, args.format)
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    main()
