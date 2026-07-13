import os
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import base64
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send"
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1ez_S_uVbn7huUeEStH_dnMKxTTfihoEIvSF827GTSVY")
RANGE_NAME = "Sheet1!A:E"  # Assuming columns A-E: ID, Name, Email, Calendar ID, Meeting Date

def get_credentials():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(f"credentials.json not found at {CREDENTIALS_PATH}. Please add your Desktop App OAuth credentials.")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return creds


def update_lead_sheet(lead_id: str, name: str, email: str, calendar_id: str, meeting_date: str) -> str:
    """Updates the Google Sheet with lead information."""
    try:
        creds = get_credentials()
        service = build("sheets", "v4", credentials=creds)
        values = [[lead_id, name, email, calendar_id, meeting_date]]
        body = {"values": values}
        
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        return f"Successfully updated sheet. {result.get('updates').get('updatedCells')} cells updated."
    except Exception as e:
        return f"Error updating sheet: {e}"


def check_calendar_availability(date_time_iso: str, duration_minutes: int = 30) -> bool:
    """Checks if the user's primary calendar is free at the given ISO datetime."""
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        start_time = datetime.datetime.fromisoformat(date_time_iso)
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(datetime.timezone.utc)
        end_time = start_time + datetime.timedelta(minutes=duration_minutes)
        
        body = {
            "timeMin": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeMax": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "items": [{"id": "primary"}]
        }
        
        events_result = service.freebusy().query(body=body).execute()
        busy_times = events_result["calendars"]["primary"]["busy"]
        
        return len(busy_times) == 0
    except Exception as e:
        print(f"Error checking calendar: {e}")
        return False


def get_existing_meeting(client_email: str) -> str:
    """Checks if a meeting already exists for this email."""
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events_result = service.events().list(
            calendarId="primary",
            timeMin=now,
            q=client_email,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        
        for event in events:
            attendees = event.get("attendees", [])
            for attendee in attendees:
                if attendee.get("email") == client_email:
                    return event["id"]
        return None
    except Exception as e:
        print(f"Error checking existing meetings: {e}")
        return None


def book_meeting(client_name: str, client_email: str, date_time_iso: str) -> str:
    """Books a meeting if available and none exists for the user."""
    existing_id = get_existing_meeting(client_email)
    if existing_id:
        return f"User {client_email} already has a meeting booked (ID: {existing_id}). Cannot book more than one. Please ask if they want to cancel or reschedule it."
    
    if not check_calendar_availability(date_time_iso):
        return "The requested time is not available. Please suggest another time."
        
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        start_time = datetime.datetime.fromisoformat(date_time_iso)
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(datetime.timezone.utc)
        end_time = start_time + datetime.timedelta(minutes=30)
        
        event = {
            "summary": f"Meeting with {client_name}",
            "description": "Consultation meeting.",
            "start": {
                "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeZone": "UTC",
            },
            "attendees": [
                {"email": client_email},
            ],
            "reminders": {
                "useDefault": True,
            },
        }
        
        event = service.events().insert(calendarId="primary", body=event).execute()
        
        # Send confirmation email
        send_confirmation_email(client_name, client_email, date_time_iso)
        
        return f"Meeting booked successfully! Event ID: {event.get('id')}"
    except Exception as e:
        return f"Error booking meeting: {e}"


def cancel_meeting(client_email: str) -> str:
    """Cancels an existing meeting for the given email."""
    event_id = get_existing_meeting(client_email)
    if not event_id:
        return "No existing meeting found for this email."
        
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return "Meeting successfully canceled."
    except Exception as e:
        return f"Error canceling meeting: {e}"


def send_confirmation_email(client_name: str, client_email: str, date_time_iso: str):
    """Sends a confirmation email using Gmail API."""
    try:
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds)
        
        message = EmailMessage()
        
        # Format the date nicely
        dt = datetime.datetime.fromisoformat(date_time_iso)
        formatted_date = dt.strftime("%B %d, %Y at %I:%M %p")
        
        content = f"""
        Hi {client_name},
        
        Your meeting has been successfully booked for {formatted_date}.
        We look forward to speaking with you!
        
        Best regards,
        The Team
        """
        
        message.set_content(content)
        message["To"] = client_email
        message["From"] = "me"
        message["Subject"] = "Meeting Confirmation"
        
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"raw": encoded_message}
        
        service.users().messages().send(userId="me", body=create_message).execute()
        
    except Exception as e:
        print(f"Error sending email: {e}")

