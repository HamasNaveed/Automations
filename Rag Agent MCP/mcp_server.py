"""
MCP Server for Google Services (Calendar, Sheets, Gmail).

Exposes Google Services functionality as standardized Model Context Protocol tools.
"""

from mcp.server.fastmcp import FastMCP
import google_services

mcp = FastMCP("Google Services Manager")

@mcp.tool()
def get_current_datetime() -> str:
    """Returns the real current date and time (local) and weekday name.
    
    The LLM has no reliable sense of 'today' on its own, so it must call this
    before interpreting relative dates (like 'tomorrow' or 'next week') or before booking.
    """
    return google_services.get_current_datetime()

@mcp.tool()
def update_lead_sheet(lead_id: str, name: str, email: str, calendar_id: str, meeting_date: str, address: str = "") -> str:
    """Use ONLY to save contact info if a lead shares details but does NOT book a meeting.
    
    NEVER call this tool for a booked meeting (book_meeting handles Sheets logging automatically).
    """
    return google_services.update_lead_sheet(
        lead_id=lead_id,
        name=name,
        email=email,
        calendar_id=calendar_id,
        meeting_date=meeting_date,
        address=address
    )

@mcp.tool()
def check_calendar_availability(date_time_iso: str, duration_minutes: int = 30) -> bool:
    """Checks if the user's primary calendar is free at the given ISO datetime string.
    
    Optional duration_minutes defaults to 30.
    """
    return google_services.check_calendar_availability(
        date_time_iso=date_time_iso,
        duration_minutes=duration_minutes
    )

@mcp.tool()
def book_meeting(client_name: str, client_email: str, date_time_iso: str, client_address: str) -> str:
    """Use to book a consultation meeting.
    
    It automatically checks availability, creates the event on Google Calendar,
    sends a confirmation email to the user, and logs the lead in the Google Sheet.
    """
    return google_services.book_meeting(
        client_name=client_name,
        client_email=client_email,
        date_time_iso=date_time_iso,
        client_address=client_address
    )

@mcp.tool()
def cancel_meeting(client_email: str) -> str:
    """Cancels an existing meeting for the given email."""
    return google_services.cancel_meeting(client_email=client_email)

@mcp.tool()
def create_support_ticket(
    name: str,
    email: str,
    location: str,
    issue_description: str,
    priority: int = 5,
    category: str = "General Support",
    calendar_id: str = "",
    meeting_date: str = "",
    chat_summary: str = "",
    order_number: str = ""
) -> str:
    """Creates a support ticket in Google Sheets (Tickets tab), sends email confirmation, and auto-escalates if priority >= 8."""
    return google_services.create_support_ticket(
        name=name,
        email=email,
        location=location,
        issue_description=issue_description,
        priority=priority,
        category=category,
        calendar_id=calendar_id,
        meeting_date=meeting_date,
        chat_summary=chat_summary,
        order_number=order_number
    )


@mcp.tool()
def get_ticket_status(identifier: str) -> str:
    """Retrieves support ticket details and resolution status by Ticket ID (e.g. TICK-123456) or client Email."""
    return google_services.get_ticket_status(identifier=identifier)

@mcp.tool()
def escalate_ticket_to_human(ticket_id: str, reason: str = "Client requested human agent") -> str:
    """Escalates an existing support ticket to a human senior manager and dispatches an urgent alert email."""
    return google_services.escalate_ticket_to_human(ticket_id=ticket_id, reason=reason)

if __name__ == "__main__":
    mcp.run()

