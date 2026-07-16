# Twilio WhatsApp Sandbox Chatbot

A lightweight help-desk menu bot for WhatsApp Sandbox using Flask and Twilio's TwiML.

## Features

- **Multi-Level Menus**: Navigates between Support Options, Sales Options, FAQs, and Resolution confirmations.
- **Session State Machine**: Uses a session manager to track each user's state by their WhatsApp phone number.
- **Clean Structure**: Separation of copy template strings from routing logic for easy wording updates.

## Setup & Run

1. **Install Requirements**:
   ```bash
   pip install flask twilio
   ```
2. **Run Bot**:
   ```bash
   python app.py
   ```
3. **Expose Hook**:
   Use `ngrok http 5000` to expose your local port, and paste your ngrok URL (`/whatsapp` endpoint) into the Twilio Sandbox message webhook configuration.
