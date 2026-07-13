import os
import sys
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from Google import Create_Service
from googleapiclient.errors import HttpError

# Configure logging to show step-by-step progress in the terminal
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

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
if not SPREADSHEET_ID:
    logging.error("Missing SPREADSHEET_ID in .env file. Please create/update your .env file.")
    sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

NEW_HEADERS = [
    "Website Visited",
    "Failed to Open",
    "Scraped Title",
    "Scraped Description",
    "Scraped Keywords",
    "Scraped Extra Info"
]

def get_google_sheets_service():
    """Authenticates and returns the Google Sheets service object using Client_Secret.json."""
    folder_path = os.path.dirname(os.path.abspath(__file__))
    client_secret_file = os.path.join(folder_path, 'Client_Secret.json')
    
    if not os.path.exists(client_secret_file):
        logging.error(f"Client_Secret.json not found in {folder_path}.")
        sys.exit(1)
        
    try:
        service = Create_Service(client_secret_file, 'sheets', 'v4', SCOPES)
        if service is None:
            raise Exception("Create_Service returned None.")
        return service
    except Exception as e:
        logging.error(f"Failed to initialize Google Sheets service: {e}")
        sys.exit(1)

def get_first_sheet_title(service):
    """Retrieves the title of the first sheet (tab) in the spreadsheet."""
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheets = spreadsheet.get('sheets', [])
        if not sheets:
            raise Exception("No sheets found in the spreadsheet.")
        return sheets[0].get('properties', {}).get('title', 'Sheet1')
    except HttpError as error:
        logging.error(f"Google API Error retrieving sheet metadata: {error}")
        raise

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
    # Fetch first row (headers)
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_title}'!1:1"
    ).execute()
    
    rows = result.get('values', [])
    if not rows or not rows[0]:
        raise Exception("The spreadsheet appears to be completely empty. Please make sure headers are set up.")
        
    headers = [h.strip() for h in rows[0]]
    original_len = len(headers)
    
    # Check if any new headers are missing and append them
    headers_updated = False
    for nh in NEW_HEADERS:
        if nh not in headers:
            headers.append(nh)
            headers_updated = True
            
    if headers_updated:
        logging.info(f"Adding new scraper metadata columns to sheet headers: {NEW_HEADERS}")
        # Write updated header list back to the sheet
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

    # Create mapping
    header_map = {h: i for i, h in enumerate(headers)}
    return header_map

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

def scrape_website_info(url):
    """
    Visits the URL using requests, parses content with BeautifulSoup,
    and returns (success, error_msg, title, description, keywords, extra_info).
    """
    url = clean_url(url)
    if not url:
        return False, "Empty URL", "", "", "", ""
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    logging.info(f"Attempting to scrape URL: {url}")
    
    try:
        # Request with a 10s timeout
        # Using verify=True first, but catching SSL issues if they happen
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except requests.exceptions.SSLError:
            logging.warning("SSL verification failed, retrying without SSL verification...")
            response = requests.get(url, headers=headers, timeout=10, verify=False)
            
        response.raise_for_status()
    except Exception as e:
        # Clean the exception name for the sheet status
        err_msg = type(e).__name__
        if hasattr(e, 'message') and e.message:
            err_msg += f": {e.message}"
        elif str(e):
            # Keep error message brief
            err_msg += f": {str(e)[:50]}"
        logging.error(f"Failed to fetch {url}: {err_msg}")
        return False, err_msg, "", "", "", ""

    try:
        # Use html.parser as it is standard and built-in
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 1. Title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            # Fallback to og:title
            og_title = soup.find('meta', property=re.compile(r'^og:title$', re.I))
            if og_title and og_title.get('content'):
                title = og_title.get('content').strip()
                
        # 2. Description
        description = ""
        desc_meta = soup.find('meta', attrs={"name": re.compile(r'^description$', re.I)})
        if desc_meta and desc_meta.get('content'):
            description = desc_meta.get('content').strip()
        else:
            # Fallback to og:description
            og_desc = soup.find('meta', property=re.compile(r'^og:description$', re.I))
            if og_desc and og_desc.get('content'):
                description = og_desc.get('content').strip()
                
        # 3. Keywords
        keywords = ""
        kw_meta = soup.find('meta', attrs={"name": re.compile(r'^keywords$', re.I)})
        if kw_meta and kw_meta.get('content'):
            keywords = kw_meta.get('content').strip()
            
        # 4. Extra Info: Collect text from H1 headers (up to 3) as related info
        h1_tags = soup.find_all('h1')
        h1_texts = [h.get_text().strip() for h in h1_tags if h.get_text().strip()]
        h1_texts = list(dict.fromkeys(h1_texts))[:3] # unique first 3 H1 tags
        extra_info = ", ".join(h1_texts) if h1_texts else "No H1 tags found"
        
        # Clean up whitespaces inside the texts
        title = re.sub(r'\s+', ' ', title)[:300]
        description = re.sub(r'\s+', ' ', description)[:500]
        keywords = re.sub(r'\s+', ' ', keywords)[:300]
        extra_info = re.sub(r'\s+', ' ', extra_info)[:400]
        
        logging.info(f"Successfully scraped: Title='{title[:40]}...', Desc='{description[:40]}...'")
        return True, "", title, description, keywords, extra_info
        
    except Exception as e:
        err_msg = f"ParsingError: {str(e)[:50]}"
        logging.error(f"Error parsing HTML of {url}: {err_msg}")
        return False, err_msg, "", "", "", ""

def main():
    logging.info("=== Starting Website Info Scraper ===")
    
    # 1. Setup service
    service = get_google_sheets_service()
    
    # 2. Get target sheet title
    sheet_title = get_first_sheet_title(service)
    logging.info(f"Target Google Sheet tab: '{sheet_title}'")
    
    # 3. Prepare and align headers, retrieve mapping
    header_map = prepare_and_align_headers(service, sheet_title)
    
    # Get column indices
    web_idx = header_map.get("Website")
    visited_idx = header_map.get("Website Visited")
    failed_idx = header_map.get("Failed to Open")
    title_idx = header_map.get("Scraped Title")
    desc_idx = header_map.get("Scraped Description")
    kw_idx = header_map.get("Scraped Keywords")
    extra_idx = header_map.get("Scraped Extra Info")
    
    if web_idx is None:
        logging.error("Could not find a 'Website' column in the spreadsheet. Please ensure your sheet contains a 'Website' column.")
        sys.exit(1)
        
    # 4. Fetch sheet data starting from row 2 (indices of data start from index 0)
    logging.info("Retrieving rows from Google Sheet...")
    # Fetch columns from A to the max column we have
    max_col_letter = col_idx_to_letter(max(header_map.values()))
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_title}'!A2:{max_col_letter}"
    ).execute()
    
    rows = result.get('values', [])
    if not rows:
        logging.info("No rows of data found in the spreadsheet to process.")
        return
        
    logging.info(f"Retrieved {len(rows)} rows of data. Starting URL scraping...")
    
    # Columns we want to write back
    new_cols = [
        "Website Visited",
        "Failed to Open",
        "Scraped Title",
        "Scraped Description",
        "Scraped Keywords",
        "Scraped Extra Info"
    ]
    new_col_indices = [header_map[c] for c in new_cols]
    
    # Find start and end range for our updates
    min_new_idx = min(new_col_indices)
    max_new_idx = max(new_col_indices)
    start_col_letter = col_idx_to_letter(min_new_idx)
    end_col_letter = col_idx_to_letter(max_new_idx)
    
    # Verify that the range to update covers exactly the columns in order
    # Our NEW_HEADERS list matches order: Visited, Failed, Title, Desc, Keywords, Extra
    # Let's ensure they are contiguous to update them in one go per row.
    # Check if they are contiguous:
    is_contiguous = True
    for i in range(len(new_col_indices) - 1):
        if new_col_indices[i] + 1 != new_col_indices[i+1]:
            is_contiguous = False
            break
            
    logging.info(f"Update Range columns: {start_col_letter} to {end_col_letter} (Contiguous: {is_contiguous})")
    
    scraped_count = 0
    failed_count = 0
    skipped_count = 0
    
    for idx, row in enumerate(rows):
        row_num = idx + 2  # 1-based index (header is 1, data starts at 2)
        
        # Ensure row list is padded to have index access
        while len(row) <= max(header_map.values()):
            row.append("")
            
        url = row[web_idx].strip()
        visited = row[visited_idx].strip()
        
        # Skip if website column is empty
        if not url:
            logging.info(f"Row {row_num}: Empty website field. Skipping.")
            skipped_count += 1
            continue
            
        # Skip if already visited
        if visited.lower() == "yes":
            logging.info(f"Row {row_num}: Website already marked as visited. Skipping.")
            skipped_count += 1
            continue
            
        logging.info(f"Processing Row {row_num} | URL: {url}")
        
        # Perform scraper
        success, err_msg, title, desc, keywords, extra = scrape_website_info(url)
        
        # Prepare values
        visited_val = "Yes"
        failed_to_open_val = "No" if success else f"Yes ({err_msg})"
        
        if success:
            scraped_count += 1
        else:
            failed_count += 1
            
        # Values in order of NEW_HEADERS:
        # ["Website Visited", "Failed to Open", "Scraped Title", "Scraped Description", "Scraped Keywords", "Scraped Extra Info"]
        update_vals = [
            visited_val,
            failed_to_open_val,
            title,
            desc,
            keywords,
            extra
        ]
        
        # Update row in Google Sheets
        range_str = f"'{sheet_title}'!{start_col_letter}{row_num}:{end_col_letter}{row_num}"
        body = {
            'values': [update_vals]
        }
        
        try:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_str,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            logging.info(f"Row {row_num} updated in Google Sheet successfully.")
        except HttpError as error:
            logging.error(f"Google Sheets API Error updating row {row_num}: {error}")
            # If we hit an API error, wait a bit and retry once
            time.sleep(2)
            try:
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=range_str,
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()
                logging.info(f"Row {row_num} updated successfully on retry.")
            except Exception as retry_err:
                logging.error(f"Retry failed: {retry_err}. Skipping write for this row to proceed.")
                
        # Be nice to Google Sheets API and target websites
        time.sleep(0.5)
        
    logging.info("=== Web Scraping Job Complete ===")
    logging.info(f"Summary: Scraped: {scraped_count} | Failed to open: {failed_count} | Skipped: {skipped_count} | Total rows: {len(rows)}")

if __name__ == "__main__":
    main()
