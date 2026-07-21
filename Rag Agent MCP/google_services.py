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
RANGE_NAME = "Sheet1!A:F"  # Columns A-F: ID, Name, Email, Calendar ID, Meeting Date, Address
TICKETS_RANGE_NAME = "Tickets!A:L"  # Columns A-L: Id, Name, Email, Calender ID, Date/Time, Location, Issue description, Priority(1-10), Resolved, Category, Escalated, Chat Summary


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    """Normalizes a datetime to UTC-aware. Naive datetimes are assumed to be in local system timezone."""
    if dt.tzinfo is None:
        return dt.astimezone(datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def get_current_datetime() -> str:
    """Returns the real current date/time (local) and weekday name.

    The LLM has no reliable sense of "today" on its own (it can only guess
    from training data), so it must call this before interpreting relative
    dates like "tomorrow" or "next Monday", or before booking any meeting.
    """
    now = datetime.datetime.now().astimezone()
    return now.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + now.strftime("%Z, %A, %B %d, %Y")


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


def update_lead_sheet(lead_id: str, name: str, email: str, calendar_id: str, meeting_date: str, address: str = "") -> str:
    """Updates the Google Sheet with lead information."""
    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot write to Google Sheet. Details: {sheet_msg}"
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("sheets", "v4", credentials=creds)
        values = [[lead_id, name, email, calendar_id, meeting_date, address]]
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
    print("\nAgent: Let me check the calendar if our team is available at that moment...")
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
    print("\nAgent: Let me check the calendar if our team is available at that moment...")
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


def book_meeting(client_name: str, client_email: str, date_time_iso: str, client_address: str = "") -> str:
    """Books a meeting if available and none exists for the user."""
    print("\nAgent: I am booking a meeting for you...")
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

        # Build a professionally formatted meeting description for Google Calendar
        formatted_meeting_time = start_time.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        calendar_description = (
            "🏡 Remodeling Consultation Meeting\n"
            "--------------------------------------------------\n"
            "📋 LEAD DETAILS:\n"
            f"  • Name: {client_name}\n"
            f"  • Email: {client_email}\n"
            f"  • Address: {client_address if client_address else 'Not provided'}\n"
            f"  • Time: {formatted_meeting_time}\n"
            "--------------------------------------------------\n"
            "📝 DESCRIPTION:\n"
            "Initial consultation to discuss home remodeling requirements, design "
            "preferences, and project scope.\n\n"
            "Apex Remodeling & Design\n"
            "📞 Phone: +1 (800) 555-0199 | ✉️ info@apexremodeling.com"
        )

        event = {
            "summary": f"Apex Remodeling Consultation: {client_name}",
            "description": calendar_description,
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
        send_confirmation_email(client_name, client_email, date_time_iso, client_address)

        # Record the lead in the Google Sheet automatically, so lead capture
        # never depends on the agent remembering to call update_lead_sheet.
        sheet_status = update_lead_sheet(
            lead_id=str(uuid.uuid4())[:8],
            name=client_name,
            email=client_email,
            calendar_id=event_id,
            meeting_date=date_time_iso,
            address=client_address,
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


def send_confirmation_email(client_name: str, client_email: str, date_time_iso: str, client_address: str = ""):
    """Sends a confirmation email using Gmail API."""
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("gmail", "v1", credentials=creds)

        # Format the date nicely
        dt = datetime.datetime.fromisoformat(date_time_iso)
        formatted_date = dt.strftime("%A, %B %d, %Y at %I:%M %p")

        # Plain text version
        plain_text_content = (
            f"Dear {client_name},\n\n"
            "Thank you for scheduling a consultation with Apex Remodeling & Design. "
            "We are pleased to confirm your meeting. Below are the details of your "
            "upcoming appointment:\n\n"
            "📅 APPOINTMENT DETAILS:\n"
            "--------------------------------------------------\n"
            f"  • Appointment: Initial Consultation\n"
            f"  • Date & Time: {formatted_date} (UTC)\n"
            f"  • Location/Address: {client_address if client_address else 'Not provided'}\n"
            "--------------------------------------------------\n\n"
            "Our consultant will contact you at the scheduled time. If you need to make any "
            "changes or reschedule, please reach out to us at least 24 hours in advance.\n\n"
            "We look forward to speaking with you!\n\n"
            "Best regards,\n"
            "The Consultation Team\n"
            "Apex Remodeling & Design\n\n"
            "--------------------------------------------------\n"
            "🏢 COMPANY CONTACT INFORMATION:\n"
            "  📞 Phone: +1 (800) 555-0199\n"
            "  ✉️ Email: info@apexremodeling.com\n"
            "  📍 Address: 123 Main Street, Suite 400, Seattle, WA 98101\n"
            "  ⏰ Office Hours: Mon - Fri, 9:00 AM - 5:00 PM\n"
        )

        # HTML version
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #333333; line-height: 1.6; margin: 0; padding: 0; }}
        .container {{ max-width: 600px; margin: 20px auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px; background-color: #ffffff; }}
        .header {{ background-color: #2c3e50; padding: 25px; text-align: center; border-top-left-radius: 8px; border-top-right-radius: 8px; color: #ffffff; }}
        .header h1 {{ margin: 0; font-size: 22px; font-weight: 400; letter-spacing: 0.5px; }}
        .content {{ padding: 25px 20px; }}
        .meeting-info {{ background-color: #f8f9fa; padding: 18px; border-left: 4px solid #3498db; margin: 20px 0; border-radius: 4px; }}
        .meeting-info p {{ margin: 6px 0; font-size: 15px; }}
        .footer {{ font-size: 12px; color: #7f8c8d; text-align: center; padding-top: 20px; border-top: 1px solid #e0e0e0; margin-top: 20px; line-height: 1.5; }}
        .company-name {{ font-weight: bold; color: #2c3e50; font-size: 14px; margin-bottom: 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Apex Remodeling &amp; Design</h1>
        </div>
        <div class="content">
            <p>Dear {client_name},</p>
            <p>Thank you for scheduling a consultation with us. We are pleased to confirm that your meeting has been successfully booked. Below are the details of your upcoming consultation:</p>
            
            <div class="meeting-info">
                <p><strong>Appointment:</strong> Initial Consultation</p>
                <p><strong>Date &amp; Time:</strong> {formatted_date} (UTC)</p>
                <p><strong>Location/Address:</strong> {client_address if client_address else 'Not provided'}</p>
            </div>
            
            <p>Our consultant will contact you at the scheduled time. If you need to make any changes or reschedule, please feel free to reach out to us at least 24 hours in advance.</p>
            
            <p>We look forward to collaborating with you on your home remodeling project!</p>
            
            <p>Best regards,<br>
            <strong>The Consultation Team</strong><br>
            Apex Remodeling &amp; Design</p>
        </div>
        <div class="footer">
            <p class="company-name">Apex Remodeling &amp; Design</p>
            <p>Phone: +1 (800) 555-0199 &nbsp;|&nbsp; Email: info@apexremodeling.com<br>
            Address: 123 Main Street, Suite 400, Seattle, WA 98101<br>
            Office Hours: Mon - Fri, 9:00 AM - 5:00 PM</p>
        </div>
    </div>
</body>
</html>
"""

        message = EmailMessage()
        message["Subject"] = "Meeting Confirmation - Apex Remodeling & Design"
        message["To"] = client_email
        message["From"] = "me"

        # Set the plain text version first
        message.set_content(plain_text_content)
        # Add the HTML version as an alternative
        message.add_alternative(html_content, subtype="html")

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"raw": encoded_message}

        service.users().messages().send(userId="me", body=create_message).execute()

    except Exception as e:
        print(f"Error sending email: {e}")


def create_support_ticket(
    name: str,
    email: str,
    location: str,
    issue_description: str,
    priority: int = 5,
    category: str = "General Support",
    calendar_id: str = "",
    meeting_date: str = "",
    chat_summary: str = ""
) -> str:
    """Creates a support ticket in the Google Sheet (Tickets tab) and triggers human escalation if high priority."""
    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot access Google Sheet. Details: {sheet_msg}"

    try:
        priority = max(1, min(10, int(priority)))
        ticket_id = f"TICK-{uuid.uuid4().hex[:6].upper()}"
        now_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

        # Auto-escalation condition for priority 8-10 or severe issues
        is_escalated = "Yes" if priority >= 8 else "No"
        resolved = "No"

        creds = get_credentials(allow_interactive=False)
        service = build("sheets", "v4", credentials=creds)

        values = [[
            ticket_id,
            name,
            email,
            calendar_id,
            now_str if not meeting_date else meeting_date,
            location,
            issue_description,
            priority,
            resolved,
            category,
            is_escalated,
            chat_summary
        ]]
        body = {"values": values}

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=TICKETS_RANGE_NAME,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        # Send confirmation email to client
        _send_ticket_email(email, name, ticket_id, priority, category, issue_description, is_escalated)

        # If escalated, send urgent notification to manager
        if is_escalated == "Yes":
            _send_escalation_alert_email(name, email, ticket_id, priority, category, issue_description, chat_summary)

        res_msg = f"Support ticket created successfully! Ticket ID: {ticket_id} | Priority: {priority}/10 | Category: {category}."
        if is_escalated == "Yes":
            res_msg += " This high-priority issue has been automatically escalated to a senior manager."
        return res_msg

    except Exception as e:
        return f"Error creating support ticket: {e}"


def get_ticket_status(identifier: str) -> str:
    """Retrieves ticket details by Ticket ID (e.g. TICK-123456) or client Email."""
    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot access Google Sheet. Details: {sheet_msg}"

    try:
        creds = get_credentials(allow_interactive=False)
        service = build("sheets", "v4", credentials=creds)

        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=TICKETS_RANGE_NAME
        ).execute()

        rows = result.get("values", [])
        if not rows:
            return "No support tickets found in system."

        query = identifier.strip().lower()
        matches = []

        for row in rows[1:]:  # Skip header row
            if len(row) > 0:
                t_id = row[0].strip().lower() if len(row) > 0 else ""
                t_email = row[2].strip().lower() if len(row) > 2 else ""

                if query == t_id or query == t_email or query in t_id:
                    t_name = row[1] if len(row) > 1 else "N/A"
                    t_date = row[4] if len(row) > 4 else "N/A"
                    t_loc = row[5] if len(row) > 5 else "N/A"
                    t_desc = row[6] if len(row) > 6 else "N/A"
                    t_prio = row[7] if len(row) > 7 else "N/A"
                    t_res = row[8] if len(row) > 8 else "N/A"
                    t_cat = row[9] if len(row) > 9 else "N/A"
                    t_esc = row[10] if len(row) > 10 else "N/A"

                    matches.append(
                        f"• Ticket ID: {row[0]}\n"
                        f"  Date: {t_date}\n"
                        f"  Category: {t_cat}\n"
                        f"  Priority: {t_prio}/10\n"
                        f"  Resolved: {t_res}\n"
                        f"  Escalated to Human: {t_esc}\n"
                        f"  Issue: {t_desc}"
                    )

        if not matches:
            return f"No support ticket found matching '{identifier}'."

        return "Found matching support ticket(s):\n\n" + "\n\n".join(matches)

    except Exception as e:
        return f"Error retrieving ticket status: {e}"


def escalate_ticket_to_human(ticket_id: str, reason: str = "Client requested senior manager assistance") -> str:
    """Escalates an existing ticket to a human manager."""
    sheet_ok, sheet_msg = check_google_sheets_access()
    if not sheet_ok:
        return f"Error: Cannot access Google Sheet. Details: {sheet_msg}"

    try:
        creds = get_credentials(allow_interactive=False)
        service = build("sheets", "v4", credentials=creds)

        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=TICKETS_RANGE_NAME
        ).execute()

        rows = result.get("values", [])
        ticket_target = ticket_id.strip().upper()

        for idx, row in enumerate(rows):
            if len(row) > 0 and row[0].strip().upper() == ticket_target:
                row_num = idx + 1  # 1-indexed line in sheets
                # Update Escalated column (column K / index 11)
                update_range = f"Tickets!K{row_num}"
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=update_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": [["Yes"]]}
                ).execute()

                c_name = row[1] if len(row) > 1 else "Client"
                c_email = row[2] if len(row) > 2 else "Unknown"
                c_desc = row[6] if len(row) > 6 else ""
                c_prio = row[7] if len(row) > 7 else "8"
                c_cat = row[9] if len(row) > 9 else "Support"

                _send_escalation_alert_email(c_name, c_email, ticket_target, c_prio, c_cat, c_desc, reason)
                return f"Ticket {ticket_target} has been successfully escalated to a senior manager."

        return f"Ticket ID '{ticket_id}' not found."

    except Exception as e:
        return f"Error escalating ticket: {e}"


EMERGENCY_EMAIL = os.getenv("EMERGENCY_EMAIL", "hamasnaveed123@gmail.com")


def _send_ticket_email(client_email: str, client_name: str, ticket_id: str, priority: int, category: str, issue_description: str, is_escalated: str):
    """Sends confirmation email to user upon support ticket creation."""
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("gmail", "v1", credentials=creds)

        message = EmailMessage()
        message["Subject"] = f"Support Ticket Confirmation [{ticket_id}] - Apex Remodeling"
        message["To"] = client_email
        message["From"] = "me"

        body_text = f"""Dear {client_name},

Thank you for reaching out to Apex Remodeling & Design.

Your Ticket ID is {ticket_id} and our agent will get in contact with you for resolving the issue.

Ticket Details:
- Category: {category}
- Priority Level: {priority}/10
- Issue Description: {issue_description}
- Status: Opened (Escalated: {is_escalated})

You can check your ticket status at any time by asking our AI assistant or replying to this email.

Best regards,
Support Team
Apex Remodeling & Design
"""
        message.set_content(body_text)
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": encoded_message}).execute()
    except Exception as e:
        print(f"Error sending ticket confirmation email: {e}")


def _send_escalation_alert_email(client_name: str, client_email: str, ticket_id: str, priority: int, category: str, issue_description: str, chat_summary: str):
    """Sends urgent alert email to support manager for escalated high-priority tickets."""
    try:
        creds = get_credentials(allow_interactive=False)
        service = build("gmail", "v1", credentials=creds)

        message = EmailMessage()
        message["Subject"] = f"HIGH PRIORITY / ESCALATED SUPPORT TICKET ALERT - {ticket_id}"
        message["To"] = EMERGENCY_EMAIL
        message["From"] = "me"

        body_text = f"""HIGH PRIORITY / ESCALATED SUPPORT TICKET ALERT

Ticket ID: {ticket_id}
Client Name: {client_name}
Client Email: {client_email}
Category: {category}
Priority Level: {priority}/10

Issue Summary:
{issue_description}

Chat Context / Summary:
{chat_summary}

Please contact the client immediately to resolve this escalated issue.
"""
        message.set_content(body_text)
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": encoded_message}).execute()
    except Exception as e:
        print(f"Error sending escalation email: {e}")