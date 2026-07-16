# Lead Capture API

A FastAPI backend for validating and recording leads generated from customer intake forms.

## Features

- **FastAPI Webhook**: Exposes endpoints for capturing user-submitted web form details.
- **Data Validation**: Strict Pydantic validators verifying name lengths, format, budget limits, timeline, and valid emails.
- **Google Sheets Integration**: Automatically appends verified leads to a tracking Google Sheet.
- **Email Notifications**: Option to send automated SMTP email alerts to admins when new leads are registered.

## Setup & Run

1. Configure `.env` with your `SPREADSHEET_ID` and SMTP server settings (copy from `.env.example`).
2. Install requirements and start the server:
   ```bash
   pip install fastapi uvicorn google-api-python-client
   uvicorn main:app --reload --port 8000
   ```
3. Open `index.html` in your browser to submit test lead data.
