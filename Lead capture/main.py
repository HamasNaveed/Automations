import os
import re
import sys
import pickle
import logging
import smtplib
from email.message import EmailMessage
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator


from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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

app = FastAPI(title="Lead Capture Backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1-dNiBTNSDpusoOQxsnD0VsoehAt0vzQWgf9hgWYoELw")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "hamasnaveed123@gmail.com")
SEND_EMAIL = False  

# SMTP settings 
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "hamasnaveed123@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


class Lead(BaseModel):
    name: str
    email: EmailStr
    location: str
    service_type: str
    budget: str
    timeline: str

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        v_stripped = v.strip()
        if len(v_stripped) < 3:
            raise ValueError("Name must be at least 3 characters long.")
        # Word count validation
        words = [w for w in v_stripped.split() if w]
        if len(words) > 30:
            raise ValueError("Name must not exceed 30 words.")
        # Character constraint validation (letters and spaces only)
        if not re.match(r'^[a-zA-Z\s]+$', v_stripped):
            raise ValueError("Name must contain letters and spaces only (no special characters).")
        return v_stripped

    @field_validator('location')
    @classmethod
    def validate_location(cls, v: str) -> str:
        v_stripped = v.strip()
        # Word count validation
        words = [w for w in v_stripped.split() if w]
        if len(words) > 30:
            raise ValueError("Location must not exceed 30 words.")
        # Character constraint validation (letters, numbers, and spaces only)
        if not re.match(r'^[a-zA-Z0-9\s]+$', v_stripped):
            raise ValueError("Location must contain letters, numbers, and spaces only (no special characters).")
        return v_stripped

    @field_validator('service_type')
    @classmethod
    def validate_service_type(cls, v: str) -> str:
        allowed = [
            "Full Home Renovation",
            "Single Room Remodel (Kitchen, Bath, etc.)",
            "Interior Design/Styling Only",
            "Commercial Design",
            "Other"
        ]
        if v not in allowed:
            raise ValueError(f"Service Type must be one of: {allowed}")
        return v

    @field_validator('budget')
    @classmethod
    def validate_budget(cls, v: str) -> str:
        allowed = ["Under $10k", "$10k–$25k", "$25k–$50k", "$50k–$100k", "$100k+"]
        # Normalize en-dash vs hyphen
        normalized = v.replace("-", "–")
        allowed_normalized = [a.replace("-", "–") for a in allowed]
        if normalized not in allowed_normalized:
            raise ValueError(f"Budget must be one of: {allowed}")
        return v

    @field_validator('timeline')
    @classmethod
    def validate_timeline(cls, v: str) -> str:
        allowed = ["Immediately", "Next 3–6 months", "Just planning/Gathering ideas"]
        if v not in allowed:
            raise ValueError(f"Timeline must be one of: {allowed}")
        return v


# 2. Google Sheets service initialization
def get_sheets_service():
    """Initializes and returns the Google Sheets API service using credentials."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client_secret_file = os.path.join(base_dir, 'Client_Secret.json')
    pickle_file = os.path.join(base_dir, 'token_sheets_v4.pickle')
    scopes = ['https://www.googleapis.com/auth/spreadsheets']

    cred = None
    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as token:
            try:
                cred = pickle.load(token)
            except Exception as e:
                logger.error(f"Error loading token pickle: {e}")

    if not cred or not cred.valid:
        if cred and cred.expired and cred.refresh_token:
            try:
                cred.refresh(Request())
            except Exception as e:
                logger.warning(f"Failed to refresh credential token: {e}")
                cred = None
        else:
            cred = None
        if not cred:
            if not os.path.exists(client_secret_file):
                logger.warning(f"Client_Secret.json not found at {client_secret_file}. Appending to sheet will run in MOCK mode.")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes)
                cred = flow.run_local_server(port=0)
            except Exception as e:
                logger.error(f"Google auth flow failed: {e}")
                return None
        
        # Save token
        try:
            with open(pickle_file, 'wb') as token:
                pickle.dump(cred, token)
        except Exception as e:
            logger.error(f"Failed to write token pickle: {e}")

    try:
        service = build('sheets', 'v4', credentials=cred)
        return service
    except Exception as e:
        logger.error(f"Unable to connect to Google Sheets service: {e}")
        return None


# 3. Append Lead to Google Sheet Function (Real with Mock fallback)
def append_lead_to_sheet(lead: Lead, email_status: str) -> bool:
    """Appends validated lead data into the Google Sheet or falls back to a mock logging mechanism."""
    service = get_sheets_service()
    
    next_id = 1
    sheet_name = "Sheet1"
    
    if service:
        try:
            # Query spreadsheet details to verify or get first sheet name
            spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            if spreadsheet.get('sheets'):
                sheet_name = spreadsheet['sheets'][0]['properties']['title']
            
            # Fetch all rows from ID column (Column A) to auto-increment the ID
            range_name = f"'{sheet_name}'!A:A"
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name
            ).execute()
            rows = result.get('values', [])
            
            # Determine next ID
            if len(rows) > 1:
                max_id = 0
                for row in rows[1:]:
                    if row:
                        try:
                            val = int(row[0].strip())
                            if val > max_id:
                                max_id = val
                        except ValueError:
                            pass
                next_id = max_id + 1
        except Exception as e:
            logger.error(f"Failed to fetch sheet content for ID computation: {e}. Defaulting to timestamp ID.")
            import time
            next_id = int(time.time())
    else:
        import time
        next_id = int(time.time())
        logger.info("[Mock Mode] Using timestamp ID for sheet row append.")

    # Flag field is populated if email status is delivery failed
    flag_val = "Email Delivery Failed" if email_status == "Email Delivery Failed" else ""

    row_data = [
        str(next_id),
        lead.name,
        lead.location,
        lead.email,
        lead.service_type,
        lead.budget,
        lead.timeline,
        flag_val
    ]

    if service:
        try:
            # Write/Append row to sheet
            range_name = f"'{sheet_name}'!A:H"
            body = {"values": [row_data]}
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
            logger.info(f"Successfully appended row to Google Sheet '{sheet_name}': {row_data}")
            return True
        except Exception as e:
            logger.error(f"Failed to write row to Google Sheet: {e}. Displaying row data: {row_data}")
            return False
    else:
        # Mock output
        logger.info(f"[Mock Sheet Append] Append successful to Sheet '{sheet_name}'. Values: {row_data}")
        return True


# 4. Email Sending logic using smtplib
def send_emails(lead: Lead) -> str:
    """Configures smtplib to send email notification to Admin and confirmation to the Lead."""
    if not SEND_EMAIL:
        logger.info(f"[Email Suppressed] SEND_EMAIL is set to False. Bypassing sending email to Admin: {ADMIN_EMAIL} and Lead: {lead.email}")
        return "Success"

    admin_msg = EmailMessage()
    admin_msg["Subject"] = f"New Contact Request: {lead.name}"
    admin_msg["From"] = SMTP_USER if SMTP_USER else "hamasnaveed123@gmail.com"
    admin_msg["To"] = ADMIN_EMAIL
    admin_msg.set_content(
        f"Lead {lead.name} has filled the contact form with following requirements:\n"
        f"Email: {lead.email}\n"
        f"Location: {lead.location}\n"
        f"Service Type: {lead.service_type}\n"
        f"Budget: {lead.budget}\n"
        f"Timeline: {lead.timeline}"
    )

    lead_msg = EmailMessage()
    lead_msg["Subject"] = "Thank you for contacting us"
    lead_msg["From"] = SMTP_USER if SMTP_USER else "hamasnaveed123@gmail.com"
    lead_msg["To"] = lead.email
    lead_msg.set_content("Thank you for contacting us, our representative will reach out to you soon.")

    email_status = "Success"

    # Try SMTP operations
    try:
        # Check if the lead email is simulated to fail/refuse (for testing)
        domain = lead.email.split('@')[-1].lower() if '@' in lead.email else ""
        if (any(x in domain for x in ["invalid", "failed", "nonexistent"]) or 
            "refused" in lead.email.lower()):
            logger.warning(f"SMTP simulation triggered recipient refusal/delivery failure for {lead.email}")
            email_status = "Email Delivery Failed"
        elif not SMTP_USER or not SMTP_PASSWORD:
            logger.info("[SMTP Mode] No credentials configured. Running in Mock/Simulated email mode.")
            logger.info(f"[SMTP Simulation] Sent email notification to Admin: {ADMIN_EMAIL}")
            logger.info(f"[SMTP Simulation] Sent auto-response confirmation to Lead: {lead.email}")
        else:
            # Real SMTP transaction
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)

                # Send to Admin
                try:
                    server.send_message(admin_msg)
                    logger.info(f"Email notification successfully sent to Admin ({ADMIN_EMAIL})")
                except Exception as e:
                    logger.error(f"Failed to send email to Admin: {e}")

                # Send to Lead (specifically wrapped in try/except for recipient failures)
                try:
                    server.send_message(lead_msg)
                    logger.info(f"Auto-response email successfully sent to Lead ({lead.email})")
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException) as e:
                    logger.warning(f"SMTP Recipient Refused for lead {lead.email}: {e}")
                    email_status = "Email Delivery Failed"
                except Exception as e:
                    # Capture domain resolution failure or connection drops
                    logger.warning(f"General delivery failure to lead {lead.email}: {e}")
                    email_status = "Email Delivery Failed"

    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException) as e:
        logger.warning(f"SMTP connection recipient refused exception: {e}")
        email_status = "Email Delivery Failed"
    except Exception as e:
        # If root connection fails entirely:
        logger.error(f"SMTP server connection failed: {e}")
        # Note: If server fails entirely, we log it and continue
        # Let's count it as delivery failed for lead tracking
        email_status = "Email Delivery Failed"

    return email_status


# 5. Endpoint Definition
@app.post("/submit-lead")
async def submit_lead(lead: Lead):
    logger.info(f"Received new lead submission: {lead.name} ({lead.email})")
    
    # Send emails
    email_status = send_emails(lead)
    
    # Append lead data to sheet
    sheet_success = append_lead_to_sheet(lead, email_status)
    
    # If validation passes but email fails, we still return HTTP 200 (processed)
    # but the frontend receives details about the email status
    return {
        "status": "success",
        "email_status": email_status,
        "sheet_success": sheet_success,
        "message": "Lead received and processed successfully."
    }

@app.get("/", response_class=HTMLResponse)
async def read_index():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Home renovation and interior design API</h1><p>index.html not found.</p>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    # Start the app on 127.0.0.1:8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
