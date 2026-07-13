import os
import datetime
import uuid
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

# No hardcoded fallback: a stray default here would silently write leads into
# someone else's spreadsheet. Each deployment must point at its own sheet.
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
RANGE_NAME = "Sheet1!A:E"  # Columns A-E: ID, Name, Email, Calendar ID, Meeting Date


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    """Normalizes a datetime to UTC-aware. Naive datetimes are assumed to already be UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def get_current_datetime() -> str:
    """Returns the real current date/time (UTC) and weekday name.

    The LLM has no reliable sense of "today" on its own (it can only guess
    from training data), so it must call this before interpreting relative
    dates like "tomorrow" or "next Monday", or before booking any meeting.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ") + " UTC, " + now.strftime("%A, %B %d, %Y")


def get_credentials(allow_interactive: bool = False):
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_PATH, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                raise RuntimeError(f"Failed to refresh Google credentials token: {e}. Please run auth.py manually to re-authenticate.")
        else:
            if not allow_interactive:
                raise RuntimeError(
                    "Google API credentials token is missing, expired, or invalid, and interactive authentication is disabled. "
                    "Please run the authentication flow script (python auth.py) manually on the server to authenticate."
                )
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(f"credentials.json not found at {CREDENTIALS_PATH}. Please add your Desktop App OAuth credentials.")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())
    return creds


def check_google_calendar_access() -> tuple[bool, str]:
    """Verifies if the Google Calendar API is fully authenticated and accessible."""
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("calendar", "v3", credentials=creds)
        # Verify access by listing calendar list with small limit
        service.calendarList().list(maxResults=1).execute()
        return True, "Google Calendar API is authenticated and accessible."
    except Exception as e:
        return False, f"Google Calendar API access check failed: {e}"


def check_google_sheets_access() -> tuple[bool, str]:
    """Verifies if the Google Sheets API is fully authenticated and SPREADSHEET_ID is readable."""
    if not SPREADSHEET_ID:
        return False, "Google Sheets error: SPREADSHEET_ID is not set in environment variables. Add it to Rag Agent/.env."
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("sheets", "v4", credentials=creds)
        # Verify access by reading spreadsheet metadata
        service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        return True, "Google Sheets API is authenticated and the spreadsheet is accessible."
    except Exception as e:
        return False, f"Google Sheets API access check failed: {e}"


def update_lead_sheet(lead_id: str, name: str, email: str, calendar_id: str, meeting_date: str) -> str:
    """Updates the Google Sheet with lead information."""
    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot write to Google Sheet. Details: {sheet_msg}"
    try:
        creds = get_credentials(allow_interactive=False)
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
    cal_ok, cal_msg = check_google_calendar_access()
    if not cal_ok:
        raise RuntimeError(f"Google Calendar API is not available: {cal_msg}")

    try:
        start_time = _to_utc(datetime.datetime.fromisoformat(date_time_iso))
        if start_time <= datetime.datetime.now(datetime.timezone.utc):
            # A time that's already passed is never "available" to book.
            return False

        creds = get_credentials(allow_interactive=False)
        service = build("calendar", "v3", credentials=creds)

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
        raise RuntimeError(f"Error checking calendar availability: {e}")


def get_existing_meeting(client_email: str) -> str:
    """Checks if a meeting already exists for this email."""
    cal_ok, cal_msg = check_google_calendar_access()
    if not cal_ok:
        raise RuntimeError(f"Google Calendar API is not available: {cal_msg}")

    try:
        creds = get_credentials(allow_interactive=False)
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
        raise RuntimeError(f"Error checking existing meetings: {e}")


def book_meeting(client_name: str, client_email: str, date_time_iso: str) -> str:
    """Books a meeting if available and none exists for the user."""
    # Before using API, verify calendar and sheets can be used
    cal_ok, cal_msg = check_google_calendar_access()
    if not cal_ok:
        return f"Error: Cannot book meeting. Google Calendar API is not available: {cal_msg}"

    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot book meeting. Google Sheets API is not available: {sheet_msg}"

    try:
        start_time = _to_utc(datetime.datetime.fromisoformat(date_time_iso))
    except ValueError:
        return f"Error: '{date_time_iso}' is not a valid ISO 8601 datetime, e.g. 2026-07-15T14:00:00."

    now = datetime.datetime.now(datetime.timezone.utc)
    if start_time <= now:
        return (
            f"Error: {date_time_iso} is in the past. The real current date/time is "
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} ({now.strftime('%A, %B %d, %Y')}). "
            "Call get_current_datetime and ask the user to confirm a future date/time, then try again."
        )

    try:
        existing_id = get_existing_meeting(client_email)
        if existing_id:
            return f"User {client_email} already has a meeting booked (ID: {existing_id}). Cannot book more than one. Please ask if they want to cancel or reschedule it."
    except Exception as e:
        return f"Error checking existing meeting: {e}"

    try:
        if not check_calendar_availability(date_time_iso):
            return "The requested time is not available. Please suggest another time."
    except Exception as e:
        return f"Error checking calendar availability: {e}"

    try:
        creds = get_credentials(allow_interactive=False)
        service = build("calendar", "v3", credentials=creds)

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
        event_id = event.get("id")

        # Send confirmation email
        send_confirmation_email(client_name, client_email, date_time_iso)

        # Record the lead in the Google Sheet automatically, so lead capture
        # never depends on the agent remembering to call update_lead_sheet.
        sheet_status = update_lead_sheet(
            lead_id=str(uuid.uuid4())[:8],
            name=client_name,
            email=client_email,
            calendar_id=event_id,
            meeting_date=date_time_iso,
        )

        return f"Meeting booked successfully! Event ID: {event_id}. {sheet_status}"
    except Exception as e:
        return f"Error booking meeting: {e}"


def cancel_meeting(client_email: str) -> str:
    """Cancels an existing meeting for the given email."""
    # Before using API, verify calendar access
    cal_ok, cal_msg = check_google_calendar_access()
    if not cal_ok:
        return f"Error: Cannot cancel meeting. Google Calendar API is not available: {cal_msg}"

    try:
        event_id = get_existing_meeting(client_email)
        if not event_id:
            return "No existing meeting found for this email."

        creds = get_credentials(allow_interactive=False)
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return "Meeting successfully canceled."
    except Exception as e:
        return f"Error canceling meeting: {e}"


def send_confirmation_email(client_name: str, client_email: str, date_time_iso: str):
    """Sends a confirmation email using Gmail API."""
    try:
        creds = get_credentials(allow_interactive=False)
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