import os
import sys
import queue
import re
import logging
import requests
import time
import threading
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, Response
from Google import Create_Service
from googleapiclient.errors import HttpError

app = Flask(__name__)

# Configure local logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Configuration ---

def load_env():
    """Loads configuration variables from a local .env file into os.environ."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

# Load env variables
load_env()

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

if not APIFY_TOKEN or not SPREADSHEET_ID:
    logging.error("Missing APIFY_API_TOKEN or SPREADSHEET_ID in .env file. Please create a .env file first.")
    sys.exit(1)

APIFY_ENDPOINT = f"https://api.apify.com/v2/actors/compass~crawler-google-places/run-sync-get-dataset-items?token={APIFY_TOKEN}"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

HEADERS = [
    "Business Name", 
    "Category Name", 
    "Description", 
    "Domain", 
    "Email", 
    "Instagram", 
    "Phone", 
    "State", 
    "Address", 
    "Website", 
    "Image URL", 
    "Facebook URL", 
    "Twitter URL"
]

# Thread-safe log queue for SSE
log_queue = queue.Queue()

class SSELogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        log_queue.put(log_entry)

# Initialize SSE logging handler
sse_handler = SSELogHandler()
sse_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Attach the handler to the root logger so backend logs automatically populate our SSE queue
logging.getLogger().addHandler(sse_handler)

def validate_inputs(location, search_terms_str, max_places):
    """Validates parameters for security and correctness."""
    errors = []
    
    # Check location (letters, numbers, spaces, commas, periods, hyphens)
    if not location or not location.strip():
        errors.append("Location query cannot be empty.")
    elif len(location) > 100:
        errors.append("Location query is too long (maximum 100 characters).")
    elif not re.match(r'^[a-zA-Z0-9\s,.-]+$', location):
        errors.append("Location contains invalid characters. Only letters, numbers, spaces, commas, periods, and hyphens are allowed.")
        
    # Check search terms
    if not search_terms_str or not search_terms_str.strip():
        errors.append("Search terms cannot be empty.")
    elif not re.match(r'^[a-zA-Z0-9\s,.-]+$', search_terms_str):
        errors.append("Search terms contain invalid characters. Only letters, numbers, spaces, commas, periods, and hyphens are allowed.")
        
    # Check max places
    try:
        places_int = int(max_places)
        if places_int < 1:
            errors.append("Number of places must be at least 1.")
        elif places_int > 100:
            errors.append("Number of places cannot exceed 100.")
    except (ValueError, TypeError):
        errors.append("Number of places must be a valid integer.")
        
    return errors

def get_google_sheets_service():
    """Authenticates and returns the Google Sheets service object using Client_Secret.json."""
    logging.info("Initializing Google Sheets API service...")
    folder_path = os.path.dirname(os.path.abspath(__file__))
    client_secret_file = os.path.join(folder_path, 'Client_Secret.json')
    
    if not os.path.exists(client_secret_file):
        raise Exception(f"Client_Secret.json not found in {folder_path}.")
        
    service = Create_Service(client_secret_file, 'sheets', 'v4', SCOPES)
    if service is None:
        raise Exception("Create_Service failed to initialize. Check Client_Secret.json or token pickles.")
    return service

def get_first_sheet_title(service):
    spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = spreadsheet.get('sheets', [])
    if not sheets:
        raise Exception("No sheets found in the spreadsheet.")
    return sheets[0].get('properties', {}).get('title', 'Sheet1')

def ensure_headers(service, sheet_title):
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_title}'!A1:M1"
    ).execute()
    values = result.get('values', [])
    
    if not values or values[0] != HEADERS:
        body = {'values': [HEADERS]}
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A1:M1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logging.info("Headers row initialized successfully.")

def run_apify_scrape(location, search_terms, max_places):
    """Triggers the Apify run synchronously with search specifications."""
    logging.info(f"Triggering Apify sync-run for Location: '{location}', Terms: {search_terms}, Limit: {max_places}...")
    
    payload = {
        "enableCompetitorAnalysis": False,
        "includeWebResults": False,
        "language": "en",
        "locationQuery": location,
        "maxCompetitorsToAnalyze": 30,
        "maxCrawledPlacesPerSearch": max_places,
        "maximumLeadsEnrichmentRecords": 0,
        "scrapeContacts": True,
        "scrapeDirectories": False,
        "scrapeImageAuthors": False,
        "scrapeOrderOnline": False,
        "scrapePlaceDetailPage": False,
        "scrapeReviewsPersonalData": True,
        "scrapeSocialMediaProfiles": {
            "facebooks": True,
            "instagrams": True,
            "tiktoks": False,
            "twitters": True,
            "youtubes": False
        },
        "scrapeTableReservationProvider": False,
        "searchStringsArray": search_terms,
        "skipClosedPlaces": False,
        "verifyLeadsEnrichmentEmails": False
    }
    
    headers = {'Content-Type': 'application/json'}
    response = requests.post(APIFY_ENDPOINT, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    logging.info(f"Apify call completed. Retrieved {len(data)} results.")
    return data

def is_valid_email(email_str):
    """Checks if a string is a valid email format."""
    if not email_str or not isinstance(email_str, str):
        return False
    # Simple regex check for valid email
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email_str.strip()))

def is_valid_social_url(url_str, domain_keyword):
    """Checks if a string contains the correct social media domain keyword."""
    if not url_str or not isinstance(url_str, str):
        return False
    return domain_keyword in url_str.lower()

def extract_fields_from_item(item):
    """Robustly extracts requested fields from a JSON dictionary or CSV row dictionary."""
    name = item.get("title", "")
    category = item.get("categoryName", "")
    description = item.get("description", "")
    domain = item.get("domain", "")
    
    # --- Robust Email Filtering & Validation ---
    email_candidates = []
    
    # 1. From JSON structures (lists/dicts matched case-insensitively)
    for key, val in item.items():
        key_lower = key.lower()
        if key_lower in ["emails", "contactinfo", "email"]:
            if isinstance(val, list):
                email_candidates.extend(val)
            elif isinstance(val, dict):
                for k_sub, v_sub in val.items():
                    if k_sub.lower() == "emails" and isinstance(v_sub, list):
                        email_candidates.extend(v_sub)
                    elif k_sub.lower() == "email" and isinstance(v_sub, str):
                        email_candidates.append(v_sub)
            elif isinstance(val, str) and val:
                email_candidates.append(val)
                
    # 2. From CSV flat keys (emails/0, Emails/1, contactInfo/emails/0...)
    for k, v in item.items():
        k_lower = k.lower()
        if "emails/" in k_lower or "email/" in k_lower or k_lower == "email" or k_lower == "emails":
            if isinstance(v, str) and v:
                email_candidates.append(v)
            elif isinstance(v, list):
                email_candidates.extend(v)
            
    # Clean and check candidates for actual valid email patterns
    valid_emails = []
    for candidate in email_candidates:
        if isinstance(candidate, str):
            candidate_cleaned = candidate.strip()
            # Split by common delimiters (commas, spaces, semicolons)
            parts = re.split(r'[,\s;]+', candidate_cleaned)
            for p in parts:
                p_cleaned = p.strip()
                if is_valid_email(p_cleaned) and p_cleaned not in valid_emails:
                    valid_emails.append(p_cleaned)
                
    email = ", ".join(valid_emails)

    # --- Robust Instagram Filtering & Validation ---
    instagram_candidates = []
    for key, val in item.items():
        key_lower = key.lower()
        if key_lower in ["instagrams", "socialmediaprofiles", "instagram", "instagramurl"]:
            if isinstance(val, list):
                instagram_candidates.extend(val)
            elif isinstance(val, dict):
                ig_val = val.get("instagramUrl", val.get("instagram", ""))
                if ig_val:
                    instagram_candidates.append(ig_val)
            elif isinstance(val, str) and val:
                instagram_candidates.append(val)
                
    for k, v in item.items():
        k_lower = k.lower()
        if "instagram" in k_lower or "instagrams/" in k_lower:
            if isinstance(v, str) and v:
                instagram_candidates.append(v)
            
    valid_instagrams = [i.strip() for i in instagram_candidates if isinstance(i, str) and is_valid_social_url(i, "instagram.com")]
    instagram = valid_instagrams[0] if valid_instagrams else ""

    # 3. Phone (phoneUnformatted or phone)
    phone = item.get("phoneUnformatted", item.get("phone", ""))
    
    # 4. State
    state = item.get("state", "")
    
    # 5. Address
    address = item.get("address", "")
    
    # 6. Website
    website = item.get("website", "")
    
    # 7. ImageUrl
    image_url = item.get("imageUrl", "")
    
    # --- Robust Facebook Filtering & Validation ---
    facebook_candidates = []
    for key, val in item.items():
        key_lower = key.lower()
        if key_lower in ["facebooks", "socialmediaprofiles", "facebook", "facebookurl"]:
            if isinstance(val, list):
                facebook_candidates.extend(val)
            elif isinstance(val, dict):
                fb_val = val.get("facebookUrl", val.get("facebook", ""))
                if fb_val:
                    facebook_candidates.append(fb_val)
            elif isinstance(val, str) and val:
                facebook_candidates.append(val)
                
    for k, v in item.items():
        k_lower = k.lower()
        if "facebook" in k_lower or "facebooks/" in k_lower:
            if isinstance(v, str) and v:
                facebook_candidates.append(v)
            
    valid_facebooks = [f.strip() for f in facebook_candidates if isinstance(f, str) and is_valid_social_url(f, "facebook.com")]
    facebook = valid_facebooks[0] if valid_facebooks else ""
            
    # --- Robust Twitter Filtering & Validation ---
    twitter_candidates = []
    for key, val in item.items():
        key_lower = key.lower()
        if key_lower in ["twitters", "socialmediaprofiles", "twitter", "twitterurl"]:
            if isinstance(val, list):
                twitter_candidates.extend(val)
            elif isinstance(val, dict):
                tw_val = val.get("twitterUrl", val.get("twitter", ""))
                if tw_val:
                    twitter_candidates.append(tw_val)
            elif isinstance(val, str) and val:
                twitter_candidates.append(val)
                
    for k, v in item.items():
        k_lower = k.lower()
        if "twitter" in k_lower or "twitters/" in k_lower or "x.com" in k_lower:
            if isinstance(v, str) and v:
                twitter_candidates.append(v)
            
    valid_twitters = [t.strip() for t in twitter_candidates if isinstance(t, str) and (is_valid_social_url(t, "twitter.com") or is_valid_social_url(t, "x.com"))]
    twitter = valid_twitters[0] if valid_twitters else ""
            
    return [
        name,
        category,
        description,
        domain,
        email,
        instagram,
        phone,
        state,
        address,
        website,
        image_url,
        facebook,
        twitter
    ]

def get_existing_leads(service, sheet_title):
    """Retrieves existing Business Names (Col A) and Emails (Col E) from the sheet for deduplication."""
    logging.info(f"Retrieving existing leads from sheet '{sheet_title}' for deduplication...")
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A:E"
        ).execute()
        rows = result.get('values', [])
        
        existing_names = set()
        existing_emails = set()
        
        if len(rows) > 1:
            for row in rows[1:]:
                if len(row) > 0 and row[0]:
                    existing_names.add(row[0].strip().lower())
                if len(row) > 4 and row[4]:
                    emails_in_row = [e.strip().lower() for e in row[4].split(",") if e.strip()]
                    for email in emails_in_row:
                        existing_emails.add(email)
                        
        logging.info(f"Retrieved {len(existing_names)} unique business names and {len(existing_emails)} unique emails for deduplication.")
        return existing_names, existing_emails
    except HttpError as error:
        logging.error(f"Error fetching existing leads: {error}")
        return set(), set()

def process_and_append_data(service, sheet_title, data):
    """Parses the data, checks for duplicates, and appends unique rows to the Google Sheet."""
    if not data:
        logging.warning("No data found to append.")
        return

    # Fetch existing names and emails from the sheet
    existing_names, existing_emails = get_existing_leads(service, sheet_title)
    
    logging.info("Processing data for Google Sheets mapping & deduplication...")
    
    rows_to_append = []
    new_names_batch = set()
    new_emails_batch = set()

    for item in data:
        row = extract_fields_from_item(item)
        name = row[0].strip()
        email_str = row[4].strip()
        
        name_lower = name.lower()
        
        # Check name duplicate
        if name_lower in existing_names or name_lower in new_names_batch:
            logging.info(f"Skipping duplicate business (by Name): '{name}'")
            continue
            
        # Check email duplicate (if email is present)
        is_duplicate_email = False
        if email_str:
            emails_to_check = [e.strip().lower() for e in email_str.split(",") if e.strip()]
            for e in emails_to_check:
                if e in existing_emails or e in new_emails_batch:
                    is_duplicate_email = True
                    break
                    
        if is_duplicate_email:
            logging.info(f"Skipping duplicate business (by Email): '{name}' (Email: {email_str})")
            continue
            
        # Unique! Add to batch sets and append row
        rows_to_append.append(row)
        new_names_batch.add(name_lower)
        if email_str:
            emails_to_add = [e.strip().lower() for e in email_str.split(",") if e.strip()]
            for e in emails_to_add:
                new_emails_batch.add(e)
                
        logging.info(f"Parsed unique lead: {name} | Email: {email_str} | IG: {row[5]}")

    if rows_to_append:
        logging.info(f"Appending {len(rows_to_append)} unique row(s) to Google Sheet (columns A-M)...")
        try:
            body = {
                'values': rows_to_append
            }
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{sheet_title}'!A:M",
                valueInputOption='USER_ENTERED',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            logging.info("Successfully appended unique data to Google Sheet!")
        except HttpError as error:
            logging.error(f"Google Sheets API Error appending row: {error}")
            raise
    else:
        logging.info("No new unique leads to append. All parsed leads were duplicates.")

# --- Website Scraping Backend Logic ---

NEW_HEADERS = [
    "Website Visited",
    "Failed to Open",
    "Scraped Title",
    "Scraped Description",
    "Scraped Keywords",
    "Scraped Extra Info"
]

def col_idx_to_letter(col_idx):
    """Converts a 0-based column index to Excel-style column letter (0 -> A, 27 -> AB)."""
    letter = ""
    while col_idx >= 0:
        letter = chr(65 + (col_idx % 26)) + letter
        col_idx = (col_idx // 26) - 1
    return letter

def prepare_and_align_headers(service, sheet_title):
    """
    Checks the current headers in the Google Sheet. 
    Appends any missing NEW_HEADERS to the sheet headers.
    Returns a dictionary mapping header names to their 0-based column indices.
    """
    logging.info("Checking and preparing headers in the Google Sheet...")
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_title}'!1:1"
    ).execute()
    
    rows = result.get('values', [])
    if not rows or not rows[0]:
        raise Exception("The spreadsheet appears to be completely empty.")
        
    headers = [h.strip() for h in rows[0]]
    headers_updated = False
    for nh in NEW_HEADERS:
        if nh not in headers:
            headers.append(nh)
            headers_updated = True
            
    if headers_updated:
        logging.info(f"Adding new scraper metadata columns to sheet headers: {NEW_HEADERS}")
        body = {
            'values': [headers]
        }
        end_col_letter = col_idx_to_letter(len(headers) - 1)
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A1:{end_col_letter}1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logging.info("Sheet headers updated successfully.")
    else:
        logging.info("All scraper metadata columns are already present in headers.")

    return {h: i for i, h in enumerate(headers)}

def clean_url(url):
    """Normalizes and prepares the URL for HTTP request."""
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url

currently_scraping_rows = {}

def scrape_website_info(url):
    """
    Visits the URL using requests, parses content with BeautifulSoup,
    and returns (success, error_msg, title, description, keywords, extra_info, emails_str, phones_str).
    """
    url = clean_url(url)
    if not url:
        return False, "Empty URL", "", "", "", "", "", ""
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    logging.info(f"Attempting to scrape URL: {url}")
    try:
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except requests.exceptions.SSLError:
            logging.warning("SSL verification failed, retrying without verification...")
            response = requests.get(url, headers=headers, timeout=10, verify=False)
            
        response.raise_for_status()
    except Exception as e:
        err_msg = type(e).__name__
        if hasattr(e, 'message') and e.message:
            err_msg += f": {e.message}"
        elif str(e):
            err_msg += f": {str(e)[:50]}"
        logging.error(f"Failed to fetch {url}: {err_msg}")
        return False, err_msg, "", "", "", "", "", ""

    try:
        html_content = response.text
        soup = BeautifulSoup(response.content, 'html.parser')
        
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            og_title = soup.find('meta', property=re.compile(r'^og:title$', re.I))
            if og_title and og_title.get('content'):
                title = og_title.get('content').strip()
                
        description = ""
        desc_meta = soup.find('meta', attrs={"name": re.compile(r'^description$', re.I)})
        if desc_meta and desc_meta.get('content'):
            description = desc_meta.get('content').strip()
        else:
            og_desc = soup.find('meta', property=re.compile(r'^og:description$', re.I))
            if og_desc and og_desc.get('content'):
                description = og_desc.get('content').strip()
                
        keywords = ""
        kw_meta = soup.find('meta', attrs={"name": re.compile(r'^keywords$', re.I)})
        if kw_meta and kw_meta.get('content'):
            keywords = kw_meta.get('content').strip()
            
        h1_tags = soup.find_all('h1')
        h1_texts = [h.get_text().strip() for h in h1_tags if h.get_text().strip()]
        h1_texts = list(dict.fromkeys(h1_texts))[:3]
        extra_info = ", ".join(h1_texts) if h1_texts else "No H1 tags found"
        
        title = re.sub(r'\s+', ' ', title)[:300]
        description = re.sub(r'\s+', ' ', description)[:500]
        keywords = re.sub(r'\s+', ' ', keywords)[:300]
        extra_info = re.sub(r'\s+', ' ', extra_info)[:400]
        
        # --- Contact Extraction ---
        # Extract emails
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        found_emails = re.findall(email_pattern, html_content)
        valid_emails = []
        for e in found_emails:
            e_clean = e.strip()
            if is_valid_email(e_clean) and e_clean not in valid_emails:
                valid_emails.append(e_clean)
        emails_str = ", ".join(valid_emails)
        
        # Extract phone numbers
        phone_pattern = r'(?:\+?\d{1,3}[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}'
        found_phones = re.findall(phone_pattern, html_content)
        unique_phones = []
        for p in found_phones:
            p_clean = p.strip()
            if len(re.sub(r'\D', '', p_clean)) >= 10 and p_clean not in unique_phones:
                unique_phones.append(p_clean)
        phones_str = ", ".join(unique_phones)
        
        logging.info(f"Successfully scraped: Title='{title[:30]}...', Emails='{emails_str[:30]}...', Phones='{phones_str[:30]}...'")
        return True, "", title, description, keywords, extra_info, emails_str, phones_str
    except Exception as e:
        err_msg = f"ParsingError: {str(e)[:50]}"
        logging.error(f"Error parsing HTML of {url}: {err_msg}")
        return False, err_msg, "", "", "", "", "", ""

def scrape_row_website_helper(service, sheet_title, row_num):
    """Scrapes a single row's website and updates Google Sheets."""
    header_map = prepare_and_align_headers(service, sheet_title)
    
    web_idx = header_map.get("Website")
    visited_idx = header_map.get("Website Visited")
    failed_idx = header_map.get("Failed to Open")
    title_idx = header_map.get("Scraped Title")
    desc_idx = header_map.get("Scraped Description")
    kw_idx = header_map.get("Scraped Keywords")
    extra_idx = header_map.get("Scraped Extra Info")
    email_idx = header_map.get("Email")
    phone_idx = header_map.get("Phone")
    
    if web_idx is None:
        logging.error("Could not find a 'Website' column in the spreadsheet.")
        return False
        
    max_col_letter = col_idx_to_letter(max(header_map.values()))
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_title}'!A{row_num}:{max_col_letter}{row_num}"
    ).execute()
    
    rows = result.get('values', [])
    if not rows:
        logging.error(f"Row {row_num} not found in the spreadsheet.")
        return False
        
    row = rows[0]
    while len(row) <= max(header_map.values()):
        row.append("")
        
    url = row[web_idx].strip()
    if not url:
        logging.warning(f"Row {row_num} website is empty.")
        return False
        
    currently_scraping_rows[str(row_num)] = 'scraping'
    try:
        success, err_msg, title, desc, keywords, extra, found_emails, found_phones = scrape_website_info(url)
        
        visited_val = "Yes"
        failed_to_open_val = "No" if success else f"Yes ({err_msg})"
        
        new_cols = ["Website Visited", "Failed to Open", "Scraped Title", "Scraped Description", "Scraped Keywords", "Scraped Extra Info"]
        new_col_indices = [header_map[c] for c in new_cols]
        
        min_new_idx = min(new_col_indices)
        max_new_idx = max(new_col_indices)
        start_col_letter = col_idx_to_letter(min_new_idx)
        end_col_letter = col_idx_to_letter(max_new_idx)
        
        update_vals = [visited_val, failed_to_open_val, title, desc, keywords, extra]
        range_str = f"'{sheet_title}'!{start_col_letter}{row_num}:{end_col_letter}{row_num}"
        body = {'values': [update_vals]}
        
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=range_str,
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logging.info(f"Row {row_num} metadata updated successfully.")
        
        if success:
            orig_email = row[email_idx].strip() if email_idx is not None else ""
            orig_phone = row[phone_idx].strip() if phone_idx is not None else ""
            
            if not orig_email and found_emails and email_idx is not None:
                col_letter = col_idx_to_letter(email_idx)
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_title}'!{col_letter}{row_num}",
                    valueInputOption='USER_ENTERED',
                    body={'values': [[found_emails]]}
                ).execute()
                logging.info(f"Updated missing Email for row {row_num}: {found_emails}")
                
            if not orig_phone and found_phones and phone_idx is not None:
                col_letter = col_idx_to_letter(phone_idx)
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_title}'!{col_letter}{row_num}",
                    valueInputOption='USER_ENTERED',
                    body={'values': [[found_phones]]}
                ).execute()
                logging.info(f"Updated missing Phone for row {row_num}: {found_phones}")
                
        currently_scraping_rows[str(row_num)] = 'completed' if success else 'failed'
        return True
    except Exception as e:
        currently_scraping_rows[str(row_num)] = 'failed'
        logging.error(f"Error scraping row {row_num}: {e}")
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream-logs')
def stream_logs():
    def generate():
        # Clear log queue first
        while not log_queue.empty():
            try:
                log_queue.get_nowait()
            except queue.Empty:
                break
                
        while True:
            try:
                msg = log_queue.get(timeout=15)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield "data: keep-alive\n\n"
    return Response(generate(), mimetype='text/event-stream')

@app.route('/run-scrape', methods=['POST'])
def run_scrape():
    # Clear log queue so frontend starts fresh
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
            
    payload = request.get_json()
    if not payload:
        return jsonify({'status': 'error', 'message': 'No parameters provided.'}), 400
        
    location = payload.get('location', '').strip()
    search_terms_str = payload.get('searchTerms', '').strip()
    max_places = payload.get('maxPlaces')
    
    # Backend input checks
    validation_errors = validate_inputs(location, search_terms_str, max_places)
    if validation_errors:
        logging.error(f"Input validation failed: {validation_errors}")
        return jsonify({'status': 'error', 'message': '; '.join(validation_errors)}), 400
        
    # Split terms by comma and strip spaces
    search_terms = [t.strip() for t in search_terms_str.split(',') if t.strip()]
    max_places_int = int(max_places)
    
    try:
        # 1. Google Sheets client connection
        service = get_google_sheets_service()
        sheet_title = get_first_sheet_title(service)
        
        # 2. Check and write sheet headers
        ensure_headers(service, sheet_title)
        
        # 3. Call Apify synchronously
        data = run_apify_scrape(location, search_terms, max_places_int)
        
        # 4. Map fields and append to spreadsheet
        process_and_append_data(service, sheet_title, data)
        
        # 5. Automatically trigger website scraping for new leads in background
        def run_scraper_thread():
            try:
                logging.info("=== Starting Website Info Scraper in Background ===")
                header_map = prepare_and_align_headers(service, sheet_title)
                
                web_idx = header_map.get("Website")
                visited_idx = header_map.get("Website Visited")
                
                if web_idx is None:
                    logging.error("Could not find a 'Website' column in the spreadsheet. Scraping aborted.")
                    logging.info("=== Web Scraping Job Complete ===")
                    return
                    
                max_col_letter = col_idx_to_letter(max(header_map.values()))
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_title}'!A2:{max_col_letter}"
                ).execute()
                
                rows = result.get('values', [])
                if not rows:
                    logging.info("No rows of data found in the spreadsheet to process.")
                    logging.info("=== Web Scraping Job Complete ===")
                    return
                    
                scraped_count = 0
                failed_count = 0
                skipped_count = 0
                
                for idx, row in enumerate(rows):
                    row_num = idx + 2
                    while len(row) <= max(header_map.values()):
                        row.append("")
                        
                    url = row[web_idx].strip()
                    visited = row[visited_idx].strip()
                    
                    if not url:
                        skipped_count += 1
                        continue
                        
                    if visited.lower() == "yes":
                        skipped_count += 1
                        continue
                        
                    # Call single-row scraping helper
                    success = scrape_row_website_helper(service, sheet_title, row_num)
                    if success:
                        scraped_count += 1
                    else:
                        failed_count += 1
                        
                    time.sleep(0.5)
                    
                logging.info(f"=== Web Scraping Job Complete === Scraped: {scraped_count} | Failed: {failed_count} | Skipped: {skipped_count} | Total: {len(rows)}")
            except Exception as th_err:
                logging.error(f"Background scraper thread error: {th_err}")
                logging.info("=== Web Scraping Job Complete ===")

        thread = threading.Thread(target=run_scraper_thread)
        thread.start()
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        err_msg = str(e)
        logging.error(f"Scraper execution error: {err_msg}")
        return jsonify({'status': 'error', 'message': err_msg}), 500

@app.route('/get-sheet-data', methods=['GET'])
def get_sheet_data():
    try:
        service = get_google_sheets_service()
        sheet_title = get_first_sheet_title(service)
        
        # Read a wide range of columns (A-Z)
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A1:Z"
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return jsonify({'headers': [], 'rows': [], 'scrapingStatus': {}})
            
        headers = [h.strip() for h in values[0]]
        rows = values[1:] if len(values) > 1 else []
        
        # Pad all rows to match headers length
        padded_rows = []
        for r in rows:
            row_padded = list(r)
            while len(row_padded) < len(headers):
                row_padded.append("")
            padded_rows.append(row_padded)
            
        return jsonify({
            'headers': headers,
            'rows': padded_rows,
            'sheetTitle': sheet_title,
            'scrapingStatus': currently_scraping_rows
        })
    except Exception as e:
        logging.error(f"Error fetching sheet data: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/scrape-row-website', methods=['POST'])
def scrape_row_website():
    payload = request.get_json() or {}
    row_num = payload.get('rowNum')
    if not row_num:
        return jsonify({'status': 'error', 'message': 'Missing row number'}), 400
        
    try:
        service = get_google_sheets_service()
        sheet_title = get_first_sheet_title(service)
        
        # Run it in a background thread so UI doesn't block
        def run_single():
            scrape_row_website_helper(service, sheet_title, int(row_num))
            
        thread = threading.Thread(target=run_single)
        thread.start()
        return jsonify({'status': 'started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/scrape-sheet-websites', methods=['POST'])
def scrape_sheet_websites():
    # Clear log queue so frontend starts fresh
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
            
    try:
        service = get_google_sheets_service()
        sheet_title = get_first_sheet_title(service)
        
        def run_scraper_thread():
            try:
                logging.info("=== Starting Website Info Scraper in Background ===")
                header_map = prepare_and_align_headers(service, sheet_title)
                
                web_idx = header_map.get("Website")
                visited_idx = header_map.get("Website Visited")
                
                if web_idx is None:
                    logging.error("Could not find a 'Website' column in the spreadsheet. Scraping aborted.")
                    logging.info("=== Web Scraping Job Complete ===")
                    return
                    
                max_col_letter = col_idx_to_letter(max(header_map.values()))
                result = service.spreadsheets().values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_title}'!A2:{max_col_letter}"
                ).execute()
                
                rows = result.get('values', [])
                if not rows:
                    logging.info("No rows of data found in the spreadsheet to process.")
                    logging.info("=== Web Scraping Job Complete ===")
                    return
                    
                scraped_count = 0
                failed_count = 0
                skipped_count = 0
                
                for idx, row in enumerate(rows):
                    row_num = idx + 2
                    while len(row) <= max(header_map.values()):
                        row.append("")
                        
                    url = row[web_idx].strip()
                    visited = row[visited_idx].strip()
                    
                    if not url:
                        skipped_count += 1
                        continue
                        
                    if visited.lower() == "yes":
                        skipped_count += 1
                        continue
                        
                    # Call single-row scraping helper
                    success = scrape_row_website_helper(service, sheet_title, row_num)
                    if success:
                        scraped_count += 1
                    else:
                        failed_count += 1
                        
                    time.sleep(0.5)
                    
                logging.info(f"=== Web Scraping Job Complete === Scraped: {scraped_count} | Failed: {failed_count} | Skipped: {skipped_count} | Total: {len(rows)}")
            except Exception as th_err:
                logging.error(f"Background scraper thread error: {th_err}")
                logging.info("=== Web Scraping Job Complete ===")
                
        thread = threading.Thread(target=run_scraper_thread)
        thread.start()
        return jsonify({'status': 'started'})
        
    except Exception as e:
        err_msg = str(e)
        logging.error(f"Failed to start website scraper: {err_msg}")
        return jsonify({'status': 'error', 'message': err_msg}), 500

if __name__ == '__main__':
    logging.info("Starting Flask application on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
