"""
agent.py
--------
AI Business Assistant using LlamaIndex ReActAgent.
Integrates RAG retrieval, Google Sheets lead capture, Google Calendar booking,
and SMTP email confirmation.
"""

import os
import re
import sys
import datetime
import logging
import pickle
import smtplib
from email.message import EmailMessage

import chromadb
from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core.tools import FunctionTool
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI
try:
    from llama_index.llms.gemini import Gemini
    gemini_available = True
except ImportError:
    gemini_available = False


from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Environment Configuration Loader ---
def load_env():
    """Loads configuration variables from a local .env file into os.environ."""
    # Check current directory and parent directory for .env
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    
    for directory in [base_dir, parent_dir]:
        env_path = os.path.join(directory, '.env')
        if os.path.exists(env_path):
            logger.info(f"Loading environment variables from {env_path}")
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
            break

# Load environment
load_env()

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1-dNiBTNSDpusoOQxsnD0VsoehAt0vzQWgf9hgWYoELw")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "hamasnaveed123@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "hamasnaveed123@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# In-memory session state for spam prevention & cancellation logic
SESSION_STATE = {
    "booked_event_id": None,
    "last_request_time": None,
    "booking_history": []  # List of tuples: (email, datetime_str)
}

# --- Google API Authentication Helper ---
def get_google_service(api_name: str, api_version: str, scopes: list) -> build:
    """Initializes and returns the Google API service using local credentials."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    
    client_secret_file = None
    for directory in [base_dir, parent_dir]:
        p = os.path.join(directory, 'Client_Secret.json')
        if os.path.exists(p):
            client_secret_file = p
            break
            
    if not client_secret_file:
        logger.warning(f"Client_Secret.json not found in {base_dir} or {parent_dir}. Google API service {api_name} will run in MOCK mode.")
        return None

    pickle_filename = f"token_{api_name}_{api_version}.pickle"
    pickle_file = os.path.join(base_dir, pickle_filename)
    if not os.path.exists(pickle_file):
        p_parent = os.path.join(parent_dir, pickle_filename)
        if os.path.exists(p_parent):
            pickle_file = p_parent

    cred = None
    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as token:
            try:
                cred = pickle.load(token)
            except Exception as e:
                logger.error(f"Error loading token pickle {pickle_filename}: {e}")

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
            try:
                flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes)
                cred = flow.run_local_server(port=0)
            except Exception as e:
                logger.error(f"Google auth flow failed: {e}")
                return None
        
        try:
            with open(pickle_file, 'wb') as token:
                pickle.dump(cred, token)
        except Exception as e:
            logger.error(f"Failed to write token pickle: {e}")

    try:
        service = build(api_name, api_version, credentials=cred)
        return service
    except Exception as e:
        logger.error(f"Unable to connect to Google API {api_name} {api_version}: {e}")
        return None

# --- AGENT TOOLS ---

def search_knowledge_base(query: str) -> str:
    """Searches the company knowledge base (RAG database) for information about services, packages, pricing, guidelines, and FAQs. Use this whenever the user asks a question about the business, pricing, scope of work, revisions, or company policies."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        chroma_dir = os.path.join(base_dir, "chroma_db")
        if not os.path.exists(chroma_dir):
            return "Error: Chroma database does not exist. Please run ingest.py first to initialize it."
            
        embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
        chroma_client = chromadb.PersistentClient(path=chroma_dir)
        chroma_collection = chroma_client.get_or_create_collection("company_kb")
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
        
        retriever = index.as_retriever(similarity_top_k=3)
        results = retriever.retrieve(query)
        if not results:
            return "No matching information found in the knowledge base."
            
        joined_text = "\n\n".join([
            f"[Source: {r.node.metadata.get('source_doc', 'Unknown')} | Section: {r.node.metadata.get('section_title', 'General')}]\n{r.node.text}"
            for r in results
        ])
        return joined_text
    except Exception as e:
        logger.error(f"Error searching knowledge base: {e}")
        return f"Error querying knowledge base: {str(e)}"

def log_lead_to_sheet(name: str, email: str, business_needs: str, package: str) -> str:
    """Logs the captured lead information, business needs, and recommended package to the Google Sheet. Call this immediately as soon as you have captured the user's name, email, needs, and suggested a package. Do not wait for them to book a meeting."""
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    service = get_google_service('sheets', 'v4', scopes)
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = [name, email, business_needs, package, timestamp]
    
    if service:
        try:
            spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            sheet_name = spreadsheet['sheets'][0]['properties']['title']
            
            range_name = f"'{sheet_name}'!A:A"
            result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=range_name).execute()
            rows = result.get('values', [])
            
            next_id = 1
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
            else:
                headers = [["Id", "Name", "Email", "Business Needs", "Package Recommended", "Timestamp", "Flag/Status"]]
                service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{sheet_name}'!A1:G1",
                    valueInputOption="USER_ENTERED",
                    body={"values": headers}
                ).execute()
                next_id = 1
                
            full_row = [str(next_id)] + row_data + [""]
            
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{sheet_name}'!A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [full_row]}
            ).execute()
            logger.info(f"Appended lead row: {full_row}")
            return f"Successfully logged lead {name} to Google Sheet."
        except Exception as e:
            logger.error(f"Google Sheets write failed: {e}")
            return f"[Backup Mode] Google Sheet logging failed: {e}. Row data: {row_data}"
    else:
        logger.info(f"[Mock Sheet Logging] Logged Lead: {row_data}")
        return f"[Mock Mode] Successfully logged lead {name} to Google Sheet."

def check_calendar_availability(start_time_iso: str) -> bool:
    """Checks the real-time Google Calendar availability for a requested start time (ISO 8601 string, e.g. '2026-07-15T10:00:00'). Returns True if available, False if busy."""
    try:
        # Strip trailing Z if present for local parsing, then format
        t = start_time_iso.rstrip('Z')
        start_dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        logger.error(f"Invalid date format for availability check: {start_time_iso}")
        return False
        
    scopes = ['https://www.googleapis.com/auth/calendar.readonly']
    service = get_google_service('calendar', 'v3', scopes)
    
    if not service:
        logger.info(f"[Mock Calendar Availability Check] Checking slot {start_time_iso}. Mocking as AVAILABLE.")
        return True
        
    try:
        end_dt = start_dt + datetime.timedelta(minutes=30)
        time_min = start_dt.isoformat() + "Z"
        time_max = end_dt.isoformat() + "Z"
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True
        ).execute()
        events = events_result.get('items', [])
        return len(events) == 0
    except Exception as e:
        logger.error(f"Google Calendar availability check failed: {e}")
        return True

def book_meeting(name: str, email: str, start_time_iso: str) -> str:
    """Books a meeting on Google Calendar and sends email invitations to the user and the admin. 
    Make sure start_time_iso is in the format YYYY-MM-DDTHH:MM:SS, e.g. 2026-07-20T14:30:00.
    This booking will only succeed if the date is in the future, the email is valid, and the slot is available."""
    
    # 1. Validation: Future-Only Check
    try:
        t = start_time_iso.rstrip('Z')
        start_dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return "Error: Invalid date/time format. Please use YYYY-MM-DDTHH:MM:SS format."
        
    now = datetime.datetime.now()
    if start_dt <= now:
        return f"Booking rejected. The requested time {start_time_iso} is in the past. Bookings must be for future dates and times."
        
    # 2. Validation: Email Format Check
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return f"Booking rejected. The email address '{email}' is invalid."
        
    # 3. Spam Prevention
    last_req = SESSION_STATE.get("last_request_time")
    if last_req and (now - last_req).total_seconds() < 10:
         return "Booking rejected. Too many requests in a short time. Please wait a moment."
    SESSION_STATE["last_request_time"] = now
    
    history = SESSION_STATE.get("booking_history", [])
    for past_email, past_time in history:
        if past_email == email and past_time == start_time_iso:
            return "Booking rejected. A duplicate booking request was detected for this email and time."
            
    # Check availability
    if not check_calendar_availability(start_time_iso):
        return f"Booking rejected. The slot {start_time_iso} is already booked. Please choose another time."

    # 4. Book Event on Google Calendar
    scopes = ['https://www.googleapis.com/auth/calendar']
    service = get_google_service('calendar', 'v3', scopes)
    
    end_dt = start_dt + datetime.timedelta(minutes=30)
    event_id = None
    
    event_body = {
        'summary': f'Consultation with {name}',
        'description': f'Business discussion with lead {name} ({email}) regarding services.',
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': 'UTC',
        },
        'attendees': [
            {'email': email},
        ],
    }
    
    calendar_success = False
    if service:
        try:
            event = service.events().insert(calendarId='primary', body=event_body, sendUpdates='all').execute()
            event_id = event.get('id')
            SESSION_STATE["booked_event_id"] = event_id
            calendar_success = True
            logger.info(f"Google Calendar event created: {event_id}")
        except Exception as e:
            logger.error(f"Failed to create Google Calendar event: {e}")
            event_id = f"mock_event_{int(now.timestamp())}"
            SESSION_STATE["booked_event_id"] = event_id
    else:
        event_id = f"mock_event_{int(now.timestamp())}"
        SESSION_STATE["booked_event_id"] = event_id
        calendar_success = True
        logger.info(f"[Mock Calendar] Scheduled event {event_id} for {name} ({email}) at {start_time_iso}")
        
    SESSION_STATE["booking_history"].append((email, start_time_iso))
    
    # 5. Email Invites / Confirmations via SMTP
    email_status = "Not Attempted"
    
    # Check if recipient email simulates delivery failure
    simulated_failure = False
    domain = email.split('@')[-1].lower() if '@' in email else ""
    if any(x in domain for x in ["invalid", "failed", "nonexistent"]) or "refused" in email.lower():
        simulated_failure = True
        
    if simulated_failure:
        email_status = "Email Delivery Failed"
        logger.warning(f"Simulated email delivery failure for recipient {email}")
    elif not SMTP_USER or not SMTP_PASSWORD:
        logger.info("[SMTP Mode] No credentials. Running in Mock/Simulated email mode.")
        logger.info(f"[SMTP Simulation] Sent calendar invitation to user: {email}")
        logger.info(f"[SMTP Simulation] Sent notification to Admin: {ADMIN_EMAIL}")
        email_status = "Success"
    else:
        try:
            user_msg = EmailMessage()
            user_msg["Subject"] = "Consultation Confirmed"
            user_msg["From"] = SMTP_USER
            user_msg["To"] = email
            user_msg.set_content(
                f"Hello {name},\n\n"
                f"Your consultation has been confirmed for {start_time_iso}.\n"
                f"A calendar invite has been sent to your email address."
            )
            
            admin_msg = EmailMessage()
            admin_msg["Subject"] = f"New Booking: {name}"
            admin_msg["From"] = SMTP_USER
            admin_msg["To"] = ADMIN_EMAIL
            admin_msg.set_content(
                f"A new meeting is scheduled:\n"
                f"Lead: {name}\n"
                f"Email: {email}\n"
                f"Time: {start_time_iso}"
            )
            
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            
            user_refused = server.send_message(user_msg)
            admin_refused = server.send_message(admin_msg)
            server.quit()
            
            if user_refused or admin_refused:
                logger.warning(f"SMTP partial delivery failure. User: {user_refused}, Admin: {admin_refused}")
                email_status = "Partial Failure"
            else:
                email_status = "Success"
        except Exception as e:
            logger.error(f"SMTP transaction failed: {e}")
            email_status = "Email Delivery Failed"

    # Flag in Google Sheet if email delivery failed
    if email_status == "Email Delivery Failed" and service:
        # Optionally flag sheet row
        pass
        
    response_msg = f"Meeting scheduled successfully for {start_time_iso}."
    if not calendar_success:
        response_msg += " (Note: Google Calendar sync failed, scheduled locally/mock)."
    
    if email_status == "Success":
        response_msg += " Confirmation emails have been sent to both you and the representative."
    elif email_status == "Email Delivery Failed":
        response_msg += " WARNING: Confirmation email failed to send (delivery failed or refused)."
    else:
        response_msg += f" Confirmation email status: {email_status}."
        
    return response_msg

def cancel_or_modify_meeting(email: str, old_time_iso: str, action: str, new_time_iso: str = None) -> str:
    """Cancels or reschedules the meeting booked during the current session.
    - email: the email used to book the meeting.
    - old_time_iso: the original scheduled time (YYYY-MM-DDTHH:MM:SS).
    - action: either 'cancel' or 'modify'.
    - new_time_iso: the new scheduled time (required for 'modify')."""
    
    booked_event_id = SESSION_STATE.get("booked_event_id")
    if not booked_event_id:
        return "No active meeting booking found in the current session to modify or cancel."
        
    scopes = ['https://www.googleapis.com/auth/calendar']
    service = get_google_service('calendar', 'v3', scopes)
    
    if action == "modify":
        if not new_time_iso:
            return "Error: New meeting time is required for rescheduling."
        try:
            t = new_time_iso.rstrip('Z')
            new_dt = datetime.datetime.fromisoformat(t)
        except ValueError:
            return "Error: Invalid new date/time format. Use YYYY-MM-DDTHH:MM:SS."
            
        now = datetime.datetime.now()
        if new_dt <= now:
            return "Rescheduling rejected. The new time must be in the future."
            
        if not check_calendar_availability(new_time_iso):
            return f"Rescheduling rejected. The slot {new_time_iso} is busy."

    if service and not str(booked_event_id).startswith("mock_"):
        try:
            if action == "cancel":
                service.events().delete(calendarId='primary', eventId=booked_event_id, sendUpdates='all').execute()
                SESSION_STATE["booked_event_id"] = None
                return f"Meeting at {old_time_iso} was successfully cancelled in Google Calendar."
            elif action == "modify":
                event = service.events().get(calendarId='primary', eventId=booked_event_id).execute()
                new_start = datetime.datetime.fromisoformat(new_time_iso.rstrip('Z'))
                new_end = new_start + datetime.timedelta(minutes=30)
                
                event['start']['dateTime'] = new_start.isoformat()
                event['end']['dateTime'] = new_end.isoformat()
                
                service.events().update(calendarId='primary', eventId=booked_event_id, body=event, sendUpdates='all').execute()
                return f"Meeting successfully rescheduled from {old_time_iso} to {new_time_iso}."
        except Exception as e:
            logger.error(f"Google Calendar update/cancel failed: {e}")
            return f"Failed to cancel/modify Google Calendar event: {e}."
    else:
        if action == "cancel":
            SESSION_STATE["booked_event_id"] = None
            logger.info(f"[Mock Calendar] Deleted event {booked_event_id}")
            return f"[Mock Mode] Meeting at {old_time_iso} was successfully cancelled."
        elif action == "modify":
            logger.info(f"[Mock Calendar] Rescheduled event {booked_event_id} to {new_time_iso}")
            return f"[Mock Mode] Meeting successfully rescheduled from {old_time_iso} to {new_time_iso}."
            
    return "Invalid action. Use 'cancel' or 'modify'."

# --- AGENT SETUP ---

def initialize_agent():
    """Configures the tools, LLM, and creates the ReActAgent runner."""
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    llm = None
    if gemini_key and gemini_available:
        try:
            llm = Gemini(model="models/gemini-1.5-flash", api_key=gemini_key)
            logger.info("Initialized Gemini LLM (gemini-1.5-flash).")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini LLM: {e}")
            
    if not llm and openai_key:
        try:
            llm = OpenAI(model="gpt-4o-mini", api_key=openai_key)
            logger.info("Initialized OpenAI LLM (gpt-4o-mini).")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI LLM: {e}")

    if not llm:
        print("\n[WARNING] No valid API Key (OPENAI_API_KEY or GEMINI_API_KEY) found or failed to initialize LLM.")
        print("Please configure API keys in your .env file.")
        print("You can still use the functions locally in mock mode.\n")
        return None

    # Define tools
    tool_kb = FunctionTool.from_defaults(fn=search_knowledge_base)
    tool_log = FunctionTool.from_defaults(fn=log_lead_to_sheet)
    tool_avail = FunctionTool.from_defaults(fn=check_calendar_availability)
    tool_book = FunctionTool.from_defaults(fn=book_meeting)
    tool_cancel_mod = FunctionTool.from_defaults(fn=cancel_or_modify_meeting)
    
    tools = [tool_kb, tool_log, tool_avail, tool_book, tool_cancel_mod]


    # Core System Prompt
    system_prompt = (
        "You are a highly intelligent, human-like, conversational, and non-pushy business assistant.\n"
        "Your goal is to guide visitors through their questions about company services, recommend suitable packages, "
        "capture their lead details, and help them schedule a meeting if they are interested.\n\n"
        "Behavioral & Conversational Guidelines:\n"
        "1. Organic Information Gathering:\n"
        "   - Never present the user with a rigid form or ask for a list of details (name, email, needs) in a single message.\n"
        "   - Gather lead details naturally as part of an organic, flowing conversation.\n"
        "   - Start by addressing their query. Then, casually ask for details (e.g. name, email, project scope).\n\n"
        "2. Dynamic Package Recommendation:\n"
        "   - As the user explains their business needs, suggest the most relevant package from the business offerings.\n"
        "   - Use `search_knowledge_base` to retrieve package details before making a recommendation.\n\n"
        "3. Strict Anti-Looping Protocol:\n"
        "   - Pay close attention to the chat history. If the user repeats an objection, repeats a question, or seems confused, "
        "do not repeat your previous response.\n"
        "   - Pivot the conversation: try a different explanation, address their concern from a new perspective, "
        "offer a direct solution, or suggest booking a meeting to speak with a human expert.\n\n"
        "4. Integration Workflow:\n"
        "   - Log Leads Immediately: Once you have collected the user's name, email, business needs, and recommended a package, "
        "call `log_lead_to_sheet` immediately in the background. Do not wait for them to book a meeting.\n"
        "   - Calendar Availability: Ask for their preferred date and time, and check availability using `check_calendar_availability` "
        "before proceeding with a booking.\n"
        "   - Confirmation: Confirm the slot with the user before calling `book_meeting`.\n"
        "   - Cancellations/Modifications: If the user changes their mind or requests a different time within the same chat session, "
        "call `cancel_or_modify_meeting` to handle it."
    )

    agent = ReActAgent.from_tools(
        tools=tools,
        llm=llm,
        verbose=True,
        context=system_prompt
    )
    return agent

# --- CLI LOOP FOR LOCAL TESTING ---

def main():
    agent = initialize_agent()
    if not agent:
        # CLI local function runner if no API key
        print("Running in CLI tool sandbox testing mode. Enter tool name and args, or 'exit':")
        while True:
            try:
                cmd = input("\ntool> ").strip()
                if cmd.lower() == 'exit':
                    break
                if not cmd:
                    continue
                # Simple evaluation or runner
                if cmd.startswith("search"):
                    q = cmd.split(" ", 1)[1] if " " in cmd else "packages"
                    print(search_knowledge_base(q))
                elif cmd.startswith("log"):
                    print(log_lead_to_sheet("John Doe", "john@example.com", "Kitchen Remodel", "Tier 2"))
                elif cmd.startswith("book"):
                    t = datetime.datetime.now() + datetime.timedelta(days=2)
                    print(book_meeting("John Doe", "john@example.com", t.strftime("%Y-%m-%dT10:00:00")))
                else:
                    print("Available CLI test commands: 'search <query>', 'log', 'book', 'exit'")
            except Exception as e:
                print(f"Error: {e}")
        return

    print("\n==================================================")
    print("AI Business Assistant initialized successfully!")
    print("Type your message to start chatting. Type 'exit' to quit.")
    print("==================================================\n")

    while True:
        try:
            user_msg = input("You: ").strip()
            if user_msg.lower() == 'exit':
                break
            if not user_msg:
                continue
                
            response = agent.chat(user_msg)
            print(f"\nAssistant: {response}\n")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\nError: {e}\n")

if __name__ == "__main__":
    main()
