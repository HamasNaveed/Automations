import os
from Google import Create_Service

FOLDER_PATH = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_FILE = os.path.join(FOLDER_PATH, 'Client_Secret.json')
API_SERVICE_NAME = 'sheets'
API_VERSION = 'v4'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

service = Create_Service(CLIENT_SECRET_FILE, API_SERVICE_NAME, API_VERSION, SCOPES)


"""
To specify Google Sheets file basic settings and as well as configure default worksheets
"""
sheet_body = {
    'properties': {
        'title': 'Demo Test Sheet',
        'locale': 'en_US', # optional
        'timeZone': 'Karachi'
        }
    ,
    'sheets': [
        {
            'properties': {
                'title': 'Test Sheet 1'
            }
        },
        {
            'properties': {
                'title': 'Test Sheet 2'
            }
        }
    ]
}

sheets_file2 = service.spreadsheets().create(body=sheet_body).execute()
print(sheets_file2['spreadsheetUrl'])
print(sheets_file2['spreadsheetId'])
print(sheets_file2['sheets'])
print(sheets_file2['properties']) 