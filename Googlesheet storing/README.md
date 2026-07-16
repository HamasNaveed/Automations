# Google Sheets CRUD Utility

A CLI-based command-line tool that performs raw Create, Read, Update, and Delete (CRUD) operations on Google Sheets.

## Features

- **CLI Menu Interface**: Interactive terminal menus for CRUD actions.
- **Input Validation**: Custom regex checks for valid names and emails before writing to Sheets.
- **Soft and Hard Deletion**: Option to mark records as deleted or clear them completely from the sheet.
- **Dynamic Headers**: Automatically initializes headers (`Id`, `Name`, `Adress`, `Email`) if the sheet is blank.

## Setup & Run

1. **Dependencies**:
   Ensure `google-api-python-client` and `google-auth` packages are installed.
2. **Credentials**:
   Place your OAuth client configuration named `Client_Secret.json` in this folder.
3. **Run**:
   ```bash
   python "Retrive and update google sheet data.py"
   ```
