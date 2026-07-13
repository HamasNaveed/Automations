import os
import sys
import time
import logging
import re
import requests
from celery import Celery
from celery.schedules import crontab

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add pathways to sys.path so we can import modules
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

lead_capture_dir = os.path.join(os.path.dirname(current_dir), 'Lead capture')
if lead_capture_dir not in sys.path:
    sys.path.append(lead_capture_dir)

# Import Apify and Website scraper logic
import apify_to_gsheets
import scrape_websites
from Google import Create_Service
from googleapiclient.errors import HttpError

# Import Lead Capture email sending logic
try:
    from main import send_emails, Lead
except ImportError as e:
    logger.error(f"Failed to import Lead capture components: {e}")
    send_emails = None
    Lead = None

# Configure Celery
# We allow overriding broker URL via env, defaulting to SQLite
BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'sqla+sqlite:///celerydb.sqlite')
RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'db+sqlite:///celeryresults.sqlite')

app = Celery('celery_tasks', broker=BROKER_URL, backend=RESULT_BACKEND)

# Set timezone to Pakistan Time
app.conf.timezone = 'Asia/Karachi'

# Schedule tasks at 10:00 AM Pakistan Time daily
app.conf.beat_schedule = {
    'daily-scrape-and-email-at-10am': {
        'task': 'celery_tasks.scrape_and_email_flow',
        'schedule': crontab(hour=10, minute=0),
    },
}

# Define new column headers (including Email Status) to align
NEW_HEADERS = [
    "Website Visited",
    "Failed to Open",
    "Scraped Title",
    "Scraped Description",
    "Scraped Keywords",
    "Scraped Extra Info",
    "Addional Emails",
    "Email Status"
]

def extract_emails_from_site_url(url):
    """Visits site url and pulls any valid emails found in html content."""
    url = scrape_websites.clean_url(url)
    if not url:
        return ""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    try:
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except requests.exceptions.SSLError:
            response = requests.get(url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
        
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        found_emails = re.findall(email_pattern, response.text)
        
        valid_emails = []
        for e in found_emails:
            e_clean = e.strip()
            # Basic validation
            if (re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', e_clean) 
                and e_clean not in valid_emails 
                and not any(x in e_clean.lower() for x in ['.png', '.jpg', '.jpeg', '.gif', 'bootstrap', 'jquery'])):
                valid_emails.append(e_clean)
        return ", ".join(valid_emails)
    except Exception as e:
        logger.warning(f"Could not extract emails from {url}: {e}")
        return ""

def prepare_extended_headers(service, spreadsheet_id, sheet_title):
    """Checks spreadsheet headers, appends any missing extended headers, and returns index mapping."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!1:1"
    ).execute()
    
    rows = result.get('values', [])
    if not rows or not rows[0]:
        raise Exception("The spreadsheet is completely empty. Initialize original headers first.")
        
    headers = [h.strip() for h in rows[0]]
    headers_updated = False
    
    for nh in NEW_HEADERS:
        if nh not in headers:
            headers.append(nh)
            headers_updated = True
            
    if headers_updated:
        body = {'values': [headers]}
        end_col_letter = scrape_websites.col_idx_to_letter(len(headers) - 1)
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_title}'!A1:{end_col_letter}1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logger.info(f"Headers updated in sheet with new fields: {NEW_HEADERS}")
        
    return {h: i for i, h in enumerate(headers)}

@app.task
def scrape_and_email_flow():
    logger.info("=== Starting Celery Scheduled Task: Lead Scrape and Email Flow ===")
    
    # 1. Fetch spreadsheet ID from env
    apify_to_gsheets.load_env()
    SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
    if not SPREADSHEET_ID:
        logger.error("SPREADSHEET_ID is missing from environment. Aborting.")
        return "Failed: Missing SPREADSHEET_ID"

    # 2. Setup Google Sheets API Service
    service = apify_to_gsheets.get_google_sheets_service()
    if not service:
        logger.error("Failed to connect to Google Sheets API. Aborting.")
        return "Failed: Sheets API connection failed"

    # 3. Get sheet name dynamically
    sheet_title = apify_to_gsheets.get_first_sheet_title(service)
    
    # 4. Ensure original headers exist
    apify_to_gsheets.ensure_headers(service, sheet_title)

    # 5. Fetch leads from Apify and append unique leads to sheet
    logger.info("Triggering Apify to fetch latest leads...")
    try:
        data = apify_to_gsheets.fetch_data_from_apify()
        apify_to_gsheets.process_and_append_data(service, sheet_title, data)
    except Exception as e:
        logger.error(f"Error during Apify scraping: {e}")
        # Continue to scraping websites even if Apify step fails/no new records to handle existing ones

    # 6. Align and prepare extra headers (including Website Visited, Scraped info, and Email Status)
    logger.info("Aligning extended headers in the sheet...")
    header_map = prepare_extended_headers(service, SPREADSHEET_ID, sheet_title)
    
    # 7. Fetch all rows of data to scrape websites and send emails
    max_col_idx = max(header_map.values())
    max_col_letter = scrape_websites.col_idx_to_letter(max_col_idx)
    
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A2:{max_col_letter}"
        ).execute()
    except Exception as e:
        logger.error(f"Failed to fetch rows from spreadsheet: {e}")
        return f"Failed: Spreadsheet fetch error {e}"

    rows = result.get('values', [])
    if not rows:
        logger.info("No lead data rows found to process.")
        return "Completed: No rows to process"

    logger.info(f"Retrieved {len(rows)} rows of data. Processing websites and emails...")
    
    # Get column indices
    web_idx = header_map.get("Website")
    visited_idx = header_map.get("Website Visited")
    failed_idx = header_map.get("Failed to Open")
    title_idx = header_map.get("Scraped Title")
    desc_idx = header_map.get("Scraped Description")
    kw_idx = header_map.get("Scraped Keywords")
    extra_idx = header_map.get("Scraped Extra Info")
    add_emails_idx = header_map.get("Addional Emails")
    email_status_idx = header_map.get("Email Status")
    orig_email_idx = header_map.get("Email")
    name_idx = header_map.get("Business Name")
    state_idx = header_map.get("State")
    address_idx = header_map.get("Address")

    scraped_count = 0
    emailed_count = 0

    for idx, row in enumerate(rows):
        row_num = idx + 2  # 1-based index (header is 1, data starts at 2)
        
        # Ensure row is padded to cover all column indices
        while len(row) <= max_col_idx:
            row.append("")
            
        business_name = row[name_idx].strip() if (name_idx is not None and name_idx < len(row)) else f"Lead #{row_num}"
        url = row[web_idx].strip() if (web_idx is not None and web_idx < len(row)) else ""
        visited = row[visited_idx].strip() if (visited_idx is not None and visited_idx < len(row)) else ""
        
        # Determine if website needs scraping
        if url and visited.lower() != "yes":
            logger.info(f"Scraping website for Row {row_num}: {url}")
            success, err_msg, title, desc, keywords, extra = scrape_websites.scrape_website_info(url)
            
            visited_val = "Yes"
            failed_val = "No" if success else f"Yes ({err_msg})"
            
            # Extract additional emails if success
            found_emails_str = ""
            if success:
                found_emails_str = extract_emails_from_site_url(url)
                
            # Write website scraping results back to sheet
            update_vals = [
                visited_val,
                failed_val,
                title,
                desc,
                keywords,
                extra,
                found_emails_str
            ]
            
            start_col = scrape_websites.col_idx_to_letter(visited_idx)
            end_col = scrape_websites.col_idx_to_letter(add_emails_idx)
            
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_title}'!{start_col}{row_num}:{end_col}{row_num}",
                    valueInputOption='USER_ENTERED',
                    body={'values': [update_vals]}
                ).execute()
                scraped_count += 1
                
                # Update local row representation so subsequent email logic can use it immediately
                row[visited_idx] = visited_val
                row[failed_idx] = failed_val
                row[title_idx] = title
                row[desc_idx] = desc
                row[kw_idx] = keywords
                row[extra_idx] = extra
                row[add_emails_idx] = found_emails_str
            except Exception as update_err:
                logger.error(f"Failed to write scraped data to Row {row_num}: {update_err}")

        # 8. Send Email logic
        orig_email = row[orig_email_idx].strip() if (orig_email_idx is not None and orig_email_idx < len(row)) else ""
        add_emails = row[add_emails_idx].strip() if (add_emails_idx is not None and add_emails_idx < len(row)) else ""
        
        email_status = row[email_status_idx].strip() if (email_status_idx is not None and email_status_idx < len(row)) else ""
        
        # Check if email is not sent/attempted
        if email_status not in ("Sent", "Suppressed", "Success"):
            # Gather unique valid emails
            all_emails = [e.strip() for e in (orig_email + "," + add_emails).split(",") if e.strip()]
            valid_emails = []
            for em in all_emails:
                if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', em) and em not in valid_emails:
                    valid_emails.append(em)
            
            if valid_emails:
                target_email = valid_emails[0] # send to first valid email
                logger.info(f"Preparing to send email to lead '{business_name}' at {target_email}...")
                
                if send_emails and Lead:
                    location_val = row[address_idx].strip() if (address_idx is not None and address_idx < len(row) and row[address_idx].strip()) else ""
                    if not location_val:
                        location_val = row[state_idx].strip() if (state_idx is not None and state_idx < len(row) and row[state_idx].strip()) else "Unknown Location"
                        
                    # Standardize budget and timeline formats to satisfy Lead validator regex constraints
                    clean_name = re.sub(r'[^a-zA-Z\s]', '', business_name).strip()
                    if len(clean_name) < 3:
                        clean_name = "Valued Client Name"
                    
                    clean_location = re.sub(r'[^a-zA-Z0-9\s]', '', location_val).strip()
                    if not clean_location:
                        clean_location = "New York City"

                    try:
                        lead_obj = Lead(
                            name=clean_name,
                            email=target_email,
                            location=clean_location,
                            service_type="Other",
                            budget="Under $10k",
                            timeline="Immediately"
                        )
                        
                        status = send_emails(lead_obj)
                        
                        from main import SEND_EMAIL as MAIN_SEND_EMAIL
                        if not MAIN_SEND_EMAIL:
                            final_status = "Suppressed"
                        elif status == "Success":
                            final_status = "Sent"
                        else:
                            final_status = "Failed"
                            
                        logger.info(f"Email task completed for {business_name}: Status = {final_status}")
                    except Exception as lead_err:
                        logger.error(f"Validation error constructing Lead object for Row {row_num}: {lead_err}")
                        final_status = f"Validation Error: {str(lead_err)[:40]}"
                else:
                    logger.error("Lead Capture modules not imported. Skipping email sending.")
                    final_status = "Error: Modules Missing"

                # Update sheet row with email status
                status_col = scrape_websites.col_idx_to_letter(email_status_idx)
                try:
                    service.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=f"'{sheet_title}'!{status_col}{row_num}",
                        valueInputOption='USER_ENTERED',
                        body={'values': [[final_status]]}
                    ).execute()
                    emailed_count += 1
                except Exception as update_err:
                    logger.error(f"Failed to write email status to Row {row_num}: {update_err}")

        # Sleep a little to be polite to Google APIs and target websites
        time.sleep(0.5)

    msg = f"Completed successfully. Scraped: {scraped_count} | Emailed/Updated: {emailed_count}"
    logger.info(f"=== {msg} ===")
    return msg
