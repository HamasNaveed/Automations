# Automations Suite

This repository contains a collection of automation tools, scrapers, lead capture systems, and conversational agents.

## Projects Directory

1. **[Rag Agent](file:///e:/New%20folder/Rag%20Agent)**: A retrieval-augmented conversational assistant powered by Google Gemini (Flash-Lite) and LlamaIndex. It integrates with Google Calendar to book appointments, Google Sheets to capture leads, and Gmail to send confirmations. Includes a responsive glassmorphic Web UI.
2. **[Lead Scraper](file:///e:/New%20folder/Lead%20scraper)**: A Flask dashboard that automates local business lead scraping. It utilizes Apify's Google Places crawler, crawls matching business web domains for emails/social media links, logs results to Google Sheets, and uses Celery for background tasks.
3. **[Lead Capture](file:///e:/New%20folder/Lead%20capture)**: A FastAPI webhook backend that validates lead data (budget, timeline, location) submitted from web forms, appends it to a Google Sheet, and optionally sends notification emails to administrators.
4. **[Googlesheet Storing](file:///e:/New%20folder/Googlesheet%20storing)**: A python CRUD utility script demonstrating low-level Google Sheets API operations. It provides a CLI menu to retrieve, append, update, and soft-delete rows.
5. **[Twilio WhatsApp Bot](file:///e:/New%20folder/Twillio/whatsapp)**: A simple WhatsApp Sandbox chatbot using Flask. It implements a multi-level support/sales menu flow using Twilio TwiML.

---

## Setup & Credentials

Most of these tools share common Google API dependencies. To run them:
- Create a project on the [Google Cloud Console](https://console.cloud.google.com/).
- Enable **Google Sheets API**, **Google Calendar API**, and **Gmail API**.
- Download your OAuth Client Credentials as `credentials.json` (or `Client_Secret.json` for older scripts).
- Run the respective setup or authorization scripts within the subfolders to generate a local `token.json` or pickle file.
