"""
app.py
------
A simple WhatsApp help-bot built with Flask + Twilio's WhatsApp Sandbox.

Flow:
    MAIN_MENU  -> user picks 1 (Support), 2 (Sales), or 3 (FAQs)
    SUPPORT_MENU -> user picks 11 (Technical) or 12 (Billing)
    SALES_MENU   -> user picks 21 (Quote) or 22 (Agent)
    -> resolution message is sent, then session resets to MAIN_MENU

Run locally:
    python app.py
Then expose it with ngrok and point the Twilio Sandbox webhook at it
(full instructions in the README).
"""

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

from session_manager import get_state, set_state, clear_state

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Static reply text — kept separate from the routing logic so it's easy
# for a non-developer to update copy without touching the state machine.
# ---------------------------------------------------------------------------

MAIN_MENU_TEXT = (
    "How can I help you? Reply with a number:\n"
    "1) Support\n"
    "2) Sales\n"
    "3) FAQs"
)

SUPPORT_MENU_TEXT = (
    "Support Options:\n"
    "11) Technical Issue\n"
    "12) Billing Issue\n\n"
    "Reply 'menu' anytime to go back to the main menu."
)

SALES_MENU_TEXT = (
    "Sales Options:\n"
    "21) Request a Quote\n"
    "22) Speak to an Agent\n\n"
    "Reply 'menu' anytime to go back to the main menu."
)

FAQ_TEXT = (
    "FAQ: Our support hours are Mon-Fri, 9am-6pm.\n"
    "You can reach us anytime through this WhatsApp number.\n\n"
    "Reply 'menu' to go back to the main menu."
)

RESOLUTION_TEXT = {
    "11": (
        "Technical Issue: Please try restarting the app and ensure you're "
        "on the latest version. If the problem persists, please email "
        "support (at) ourcompany (dot) com with a screenshot and we'll get "
        "back to you within 24 hours."
    ),
    "12": (
        "Billing Issue: You can view and manage your invoices at "
        "our portal (billing.ourcompany.com). If you see an unexpected "
        "charge, email billing (at) ourcompany (dot) com with your account ID."
    ),
    "21": (
        "Request a Quote: Please share your company name, the product "
        "you're interested in, and your expected volume, and our sales "
        "team will send a custom quote within 1 business day."
    ),
    "22": (
        "Speak to an Agent: One of our sales agents will call you shortly. "
        "You can also reach them directly at +1-555-0100."
    ),
}

INVALID_OPTION_TEXT = "Sorry, I didn't understand that. {}"


def build_response(message: str) -> str:
    """Wrap a plain string in Twilio's TwiML <Message> format."""
    twiml = MessagingResponse()
    twiml.message(message)
    return str(twiml)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """
    Single webhook endpoint Twilio calls for every inbound WhatsApp message.
    Twilio sends form-encoded data; the two fields we care about are:
        From - the sender's WhatsApp number, e.g. "whatsapp:+15551234567"
        Body - the text they sent
    """
    incoming_msg = request.form.get("Body", "").strip()
    sender = request.form.get("From", "")  # used as our session key
    print(f"Received message: '{incoming_msg}' from: '{sender}'")

    reply = route_message(sender, incoming_msg)
    print(f"Replying: '{reply}'")
    return Response(build_response(reply), mimetype="text/xml")


def route_message(user_id: str, incoming_msg: str) -> str:
    """
    Core state machine. Decides what to reply based on:
      - the user's current session state
      - the text they just sent

    Kept separate from the Flask route so it's easily unit-testable
    without spinning up HTTP requests.
    """
    text = incoming_msg.strip().lower()
    current_state = get_state(user_id)
    if current_state is None:
        current_state = "MAIN_MENU"

    # Global shortcut: typing "menu" from anywhere resets to the main menu.
    if text == "menu":
        set_state(user_id, "MAIN_MENU")
        return MAIN_MENU_TEXT

    # --- State: MAIN_MENU ---------------------------------------------
    if current_state == "MAIN_MENU":
        if text == "1":
            set_state(user_id, "SUPPORT_MENU")
            return SUPPORT_MENU_TEXT
        elif text == "2":
            set_state(user_id, "SALES_MENU")
            return SALES_MENU_TEXT
        elif text == "3":
            # FAQ is a leaf node with no further sub-options,
            # so we just stay in MAIN_MENU state.
            return FAQ_TEXT
        else:
            # Any first-touch / unrecognized message shows the main menu.
            # This also covers "Greeting" - first message from a new user.
            set_state(user_id, "MAIN_MENU")
            return MAIN_MENU_TEXT

    # --- State: SUPPORT_MENU --------------------------------------------
    elif current_state == "SUPPORT_MENU":
        if text in ("11", "12"):
            clear_state(user_id)  # resolved -> reset for next conversation
            return RESOLUTION_TEXT[text]
        else:
            return INVALID_OPTION_TEXT.format(SUPPORT_MENU_TEXT)

    # --- State: SALES_MENU -------------------------------------------------
    elif current_state == "SALES_MENU":
        if text in ("21", "22"):
            clear_state(user_id)
            return RESOLUTION_TEXT[text]
        else:
            return INVALID_OPTION_TEXT.format(SALES_MENU_TEXT)

    # --- Fallback: unknown state, reset safely ---------------------------
    set_state(user_id, "MAIN_MENU")
    return MAIN_MENU_TEXT


if __name__ == "__main__":
    # debug=True auto-reloads on code changes - handy for local dev.
    # Do NOT use debug=True in production.
    app.run(port=5000, debug=True)