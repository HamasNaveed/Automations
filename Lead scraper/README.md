# Local Business Lead Scraper

A web-based scraping dashboard built with Flask, Celery, Apify, and BeautifulSoup to find local business leads.

## Features

- **Google Places Integration**: Calls Apify's Google Places crawler to get matching businesses in a specific area.
- **Sub-page Crawler**: Crawls the websites of the discovered businesses to scrape public contact info (email, phone, Facebook, Instagram, Twitter).
- **Google Sheets Output**: Automatically logs all crawled contacts to a Google Sheet.
- **Real-Time Log Dashboard**: Uses Server-Sent Events (SSE) to stream crawler status logs directly to the dashboard interface.
- **Celery Worker Support**: Backgrounds long-running scraping tasks so they do not block the web server.

## Setup & Run

1. **Configure Environment**:
   Configure `.env` with your `APIFY_API_TOKEN` and Google Sheets `SPREADSHEET_ID`.
2. **Start Celery Worker**:
   ```bash
   celery -A celery_tasks.celery worker --loglevel=info
   ```
3. **Start Flask Dashboard**:
   ```bash
   python app.py
   ```
   Open `http://localhost:5000` to run scrapers.
