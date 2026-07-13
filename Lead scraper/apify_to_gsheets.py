import os
import sys
import glob
import csv
import re
import requests
import logging
from Google import Create_Service
from googleapiclient.errors import HttpError

# Configure logging to show each step in the terminal
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

# Apify API endpoint and token
APIFY_ENDPOINT = f"https://api.apify.com/v2/actors/compass~crawler-google-places/run-sync-get-dataset-items?token={APIFY_TOKEN}"

# Google Sheets Configuration
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Column Headers for the Google Sheet
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

# Apify Payload - Configured for 1 result for testing, with social scraping enabled
APIFY_PAYLOAD = {
    "enableCompetitorAnalysis": False,
    "includeWebResults": False,
    "language": "en",
    "locationQuery": "New York, USA",  # Default location placeholder
    "maxCompetitorsToAnalyze": 30,
    "maxCrawledPlacesPerSearch": 1,    # Set to 1 for testing as requested
    "maximumLeadsEnrichmentRecords": 0,
    "scrapeContacts": True,
    "scrapeDirectories": False,
    "scrapeImageAuthors": False,
    "scrapeOrderOnline": False,
    "scrapePlaceDetailPage": False,
    "scrapeReviewsPersonalData": True,
    "scrapeSocialMediaProfiles": {
        "facebooks": True,             # Enabled to fetch Facebook links
        "instagrams": True,            # Enabled to fetch Instagram links
        "tiktoks": False,
        "twitters": True,              # Enabled to fetch Twitter links
        "youtubes": False
    },
    "scrapeTableReservationProvider": False,
    "searchStringsArray": [
        "restaurant"                  # Default search term placeholder
    ],
    "skipClosedPlaces": False,
    "verifyLeadsEnrichmentEmails": False
}

def get_google_sheets_service():
    """Authenticates and returns the Google Sheets service object using Client_Secret.json."""
    logging.info("Initializing Google Sheets API service using Client_Secret.json...")
    
    folder_path = os.path.dirname(os.path.abspath(__file__))
    client_secret_file = os.path.join(folder_path, 'Client_Secret.json')
    
    if not os.path.exists(client_secret_file):
        logging.error(f"Client_Secret.json not found in {folder_path}.")
        logging.error("Please ensure Client_Secret.json is located in the 'Lead scraper' folder.")
        sys.exit(1)
        
    try:
        service = Create_Service(client_secret_file, 'sheets', 'v4', SCOPES)
        if service is None:
            raise Exception("Create_Service returned None.")
        logging.info("Successfully connected to Google Sheets API service.")
        return service
    except Exception as e:
        logging.error(f"Failed to initialize Google Sheets service: {e}")
        sys.exit(1)

def get_first_sheet_title(service):
    """Retrieves the title of the first sheet (tab) in the spreadsheet."""
    logging.info(f"Retrieving sheet metadata for Spreadsheet ID: {SPREADSHEET_ID}...")
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheets = spreadsheet.get('sheets', [])
        if not sheets:
            raise Exception("No sheets found in the spreadsheet.")
        first_sheet_title = sheets[0].get('properties', {}).get('title', 'Sheet1')
        logging.info(f"Target sheet found: '{first_sheet_title}' (gid=0)")
        return first_sheet_title
    except HttpError as error:
        logging.error(f"Google API Error retrieving sheet metadata: {error}")
        raise

def ensure_headers(service, sheet_title):
    """Checks the first row of the sheet and writes/updates headers if they do not match HEADERS."""
    logging.info(f"Checking headers in sheet '{sheet_title}'...")
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_title}'!A1:M1"
        ).execute()
        values = result.get('values', [])
        
        if not values or values[0] != HEADERS:
            logging.info("Headers not found or mismatching. Initializing headers row...")
            body = {
                'values': [HEADERS]
            }
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{sheet_title}'!A1:M1",
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            logging.info("Headers successfully initialized.")
        else:
            logging.info("Headers already present and correct.")
    except HttpError as error:
        logging.error(f"Error checking/initializing headers: {error}")
        raise

def fetch_data_from_apify():
    """Triggers the Apify actor synchronously and fetches the dataset."""
    logging.info(f"Triggering Apify actor for location: '{APIFY_PAYLOAD['locationQuery']}' and search: '{APIFY_PAYLOAD['searchStringsArray'][0]}'")
    logging.info("Waiting for Apify to finish scraping (this might take a moment)...")
    
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(APIFY_ENDPOINT, json=APIFY_PAYLOAD, headers=headers)
        response.raise_for_status()
        data = response.json()
        logging.info(f"Successfully retrieved {len(data)} records from Apify.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling Apify API: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"Response content: {e.response.text}")
        raise

def fetch_last_run_from_apify():
    """Retrieves the dataset items from the last run of the actor on Apify."""
    logging.info("Fetching last run list from Apify API...")
    if "token=" not in APIFY_ENDPOINT:
        raise Exception("Apify token not found in endpoint URL.")
    
    token = APIFY_ENDPOINT.split("token=")[1]
    runs_url = f"https://api.apify.com/v2/actors/compass~crawler-google-places/runs?token={token}&limit=1&desc=true"
    
    try:
        response = requests.get(runs_url)
        response.raise_for_status()
        runs = response.json().get("data", {}).get("items", [])
        if not runs:
            raise Exception("No runs found for this actor on Apify.")
        
        last_run = runs[0]
        dataset_id = last_run.get("defaultDatasetId")
        run_id = last_run.get("id")
        logging.info(f"Last run found: ID={run_id}, DatasetID={dataset_id}")
        
        # Get items from the dataset
        items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}"
        logging.info("Fetching dataset items of last run...")
        items_response = requests.get(items_url)
        items_response.raise_for_status()
        data = items_response.json()
        logging.info(f"Successfully retrieved {len(data)} records from Apify last run dataset.")
        return data
    except Exception as e:
        logging.error(f"Error fetching last run from Apify API: {e}")
        raise

def load_from_local_csv():
    """Scans parent/current folders for local Google Places CSV files and loads selected file."""
    logging.info("Scanning for local Google Places CSV files...")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    
    csv_pattern_1 = os.path.join(current_dir, "dataset_crawler-google-places_*.csv")
    csv_pattern_2 = os.path.join(parent_dir, "dataset_crawler-google-places_*.csv")
    
    files = glob.glob(csv_pattern_1) + glob.glob(csv_pattern_2)
    files = list(set(files))  # De-duplicate
    
    if not files:
        logging.warning("No dataset_crawler-google-places_*.csv files found in standard locations.")
        manual_path = input("Enter the absolute path to your CSV file: ").strip()
        if os.path.exists(manual_path):
            files = [manual_path]
        else:
            raise Exception("No CSV file selected or found.")
            
    print("\nSelect a CSV file to import:")
    for idx, filepath in enumerate(files):
        print(f"{idx + 1}. {os.path.basename(filepath)}")
        
    choice = input(f"Choose file (1-{len(files)}): ").strip()
    try:
        selected_file = files[int(choice) - 1]
    except (ValueError, IndexError):
        raise Exception("Invalid file selection.")
        
    logging.info(f"Loading data from: {selected_file}")
    
    data = []
    with open(selected_file, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
            
    logging.info(f"Loaded {len(data)} rows from CSV file.")
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

def main():
    logging.info("--- Starting Apify to Google Sheets Integration ---")
    try:
        # 1. Setup Google Sheets API Service
        service = get_google_sheets_service()
        
        # 2. Get sheet name dynamically
        sheet_title = get_first_sheet_title(service)
        
        # 3. Ensure headers exist in Google Sheets
        ensure_headers(service, sheet_title)
        
        # 4. Display options menu
        print("\n================ SELECT ACTION ================")
        print("1. Run a new live scrape (1 result test) and upload")
        print("2. Fetch last run's dataset from Apify API and upload")
        print("3. Import data from a local CSV file and upload")
        print("===============================================")
        choice = input("Enter choice (1-3): ").strip()
        
        data = []
        if choice == '1':
            data = fetch_data_from_apify()
        elif choice == '2':
            data = fetch_last_run_from_apify()
        elif choice == '3':
            data = load_from_local_csv()
        else:
            print("Invalid choice. Exiting.")
            return
            
        # 5. Process and Append Data
        process_and_append_data(service, sheet_title, data)
        
    except Exception as e:
        logging.error(f"Script execution failed: {e}")
    finally:
        logging.info("--- Script Execution Completed ---")

if __name__ == "__main__":
    main()
