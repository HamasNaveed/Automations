import os
import sys
import re
from Google import Create_Service
from googleapiclient.errors import HttpError

def validate_name(name):
    """Validates that name is not empty and does not contain special characters (only letters, numbers, and spaces)."""
    if not name:
        return False, "Name cannot be empty."
    if not re.match(r'^[a-zA-Z0-9\s]+$', name):
        return False, "Name must not contain special characters (only letters, numbers, and spaces are allowed)."
    return True, ""

def validate_email(email):
    """Validates that email is in a proper format."""
    if not email:
        return False, "Email cannot be empty."
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Email format is invalid (e.g., example@domain.com)."
    return True, ""

# Configuration
SPREADSHEET_ID = '1Jr9FM-2E2RUQSF4Rg95jDVZJAqpJhxOCl3RdHFRIzCY'
SHEET_NAME = 'Test Sheet 1'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def init_service():
    """Initializes the Google Sheets API service using credentials."""
    folder_path = os.path.dirname(os.path.abspath(__file__))
    client_secret_file = os.path.join(folder_path, 'Client_Secret.json')
    if not os.path.exists(client_secret_file):
        print(f"Error: Client_Secret.json not found in {folder_path}.")
        sys.exit(1)
    
    try:
        service = Create_Service(client_secret_file, 'sheets', 'v4', SCOPES)
        return service
    except Exception as e:
        print(f"Failed to initialize Google Sheets service: {e}")
        sys.exit(1)

def get_all_rows(service):
    """Retrieves all rows (columns A to D) from the sheet."""
    try:
        range_name = f"'{SHEET_NAME}'!A:D"
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name
        ).execute()
        return result.get('values', [])
    except HttpError as error:
        print(f"Google API Error retrieving values: {error}")
        return []

def update_row(service, row_number, updated_row):
    """Updates a specific row (columns A to D) in the sheet."""
    try:
        range_name = f"'{SHEET_NAME}'!A{row_number}:D{row_number}"
        body = {"values": [updated_row]}
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        print(f"\n[Success] Row {row_number} updated successfully!")
    except HttpError as error:
        print(f"Google API Error updating row: {error}")

def append_row(service, new_row):
    """Appends a new row (columns A to D) to the sheet."""
    try:
        range_name = f"'{SHEET_NAME}'!A:D"
        body = {"values": [new_row]}
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        print(f"\n[Success] New row with ID {new_row[0]} added successfully!")
    except HttpError as error:
        print(f"Google API Error appending row: {error}")

def initialize_sheet_if_empty(service):
    """Initializes the sheet with headers if it is empty."""
    rows = get_all_rows(service)
    if not rows:
        print("Sheet is empty. Initializing with header row (Id, Name, Adress, Email)...")
        initial_data = [
            ["Id", "Name", "Adress", "Email"]
        ]
        try:
            range_name = f"'{SHEET_NAME}'!A1:D1"
            body = {"values": initial_data}
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            print("Header row successfully initialized!")
        except HttpError as error:
            print(f"Google API Error initializing sheet: {error}")

def print_table(rows):
    """Prints retrieved rows as a formatted table."""
    if not rows:
        print("No data available.")
        return
    
    # Calculate column widths for pretty formatting
    col_widths = [2, 4, 6, 5] # Minimum widths
    for row in rows:
        for idx, val in enumerate(row):
            if idx < len(col_widths):
                col_widths[idx] = max(col_widths[idx], len(str(val)))
            else:
                col_widths.append(len(str(val)))
                
    # Create border line
    border = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    
    print(border)
    # Header
    header_row = rows[0]
    padded_header = " | ".join(str(val).ljust(col_widths[idx]) for idx, val in enumerate(header_row))
    print(f"| {padded_header} |")
    print(border)
    
    # Data Rows
    for row in rows[1:]:
        # Ensure row has enough elements
        padded_row = []
        for idx in range(len(col_widths)):
            val = row[idx] if idx < len(row) else ""
            padded_row.append(str(val).ljust(col_widths[idx]))
        print(f"| { ' | '.join(padded_row) } |")
    print(border)

def handle_read_values(service):
    while True:
        print("\n--- Read Values Menu ---")
        print("1. Get all sheet data")
        print("2. Get specific data by Email")
        print("3. Back to Main Menu")
        choice = input("Enter choice (1-3): ").strip()
        
        if choice == '1':
            rows = get_all_rows(service)
            if not rows or len(rows) <= 1:
                print("No data found in the sheet (except headers if initialized).")
            else:
                print("\nRetrieving all sheet data...")
                print_table(rows)
        elif choice == '2':
            email_query = input("Enter Email to search: ").strip()
            if not email_query:
                print("Email cannot be empty.")
                continue
                
            rows = get_all_rows(service)
            if not rows:
                print("No data found in the sheet.")
                continue
                
            found = False
            results = [rows[0]] # Include the header for printing
            for row in rows[1:]:
                # Email is in column index 3 (Column D)
                if len(row) > 3 and row[3].strip().lower() == email_query.lower():
                    results.append(row)
                    found = True
            
            if found:
                print(f"\nFound matches for email '{email_query}':")
                print_table(results)
            else:
                print(f"\n[Notice] Email '{email_query}' does not exist.")
        elif choice == '3':
            break
        else:
            print("Invalid choice. Please choose 1, 2, or 3.")

def handle_add_new_row(service, rows):
    """Calculates new ID (+1 of last row ID) and appends a new row."""
    new_id = 1
    if rows and len(rows) > 1:
        last_row = rows[-1]
        if last_row and len(last_row) > 0:
            try:
                new_id = int(last_row[0].strip()) + 1
            except ValueError:
                # Fallback: search for max ID
                max_id = 0
                for row in rows[1:]:
                    if row and len(row) > 0:
                        try:
                            max_id = max(max_id, int(row[0].strip()))
                        except ValueError:
                            pass
                new_id = max_id + 1
                
    print(f"\nAdding a new row. Auto-calculated ID will be: {new_id}")
    
    while True:
        new_name = input("Enter Name: ").strip()
        is_valid, err_msg = validate_name(new_name)
        if is_valid:
            break
        print(f"[Error] {err_msg}")
        
    new_address = input("Enter Adress: ").strip()
    
    while True:
        new_email = input("Enter Email: ").strip()
        is_valid, err_msg = validate_email(new_email)
        if is_valid:
            break
        print(f"[Error] {err_msg}")
    
    new_row = [str(new_id), new_name, new_address, new_email]
    
    print("\nAppending new row...")
    append_row(service, new_row)

def handle_update_values(service):
    while True:
        print("\n--- Update / Add Values Menu ---")
        print("1. Update an existing row by ID")
        print("2. Add a new row")
        print("3. Back to Main Menu")
        choice = input("Enter choice (1-3): ").strip()
        
        if choice == '1':
            id_query = input("Enter the Id you want to update: ").strip()
            if not id_query:
                print("Id cannot be empty.")
                continue
                
            rows = get_all_rows(service)
            if not rows:
                print("No data found in the sheet.")
                continue
                
            row_idx_found = -1
            matched_row = []
            
            for idx, row in enumerate(rows):
                if idx == 0:
                    continue  # Skip headers
                if len(row) > 0 and row[0].strip() == id_query:
                    row_idx_found = idx
                    matched_row = row
                    break
                    
            if row_idx_found == -1:
                print(f"\n[Notice] Id '{id_query}' does not exist.")
                add_new = input("Would you like to add a new row instead? (y/n): ").strip().lower()
                if add_new == 'y':
                    handle_add_new_row(service, rows)
                continue
                
            current_name = matched_row[1] if len(matched_row) > 1 else ""
            current_address = matched_row[2] if len(matched_row) > 2 else ""
            current_email = matched_row[3] if len(matched_row) > 3 else ""
            
            print(f"\nId {id_query} found! Current Details:")
            print(f"Name: {current_name}")
            print(f"Adress: {current_address}")
            print(f"Email: {current_email}")
            print("\nEnter new values (press Enter to keep current value):")
            
            while True:
                new_name = input(f"New Name [{current_name}]: ").strip()
                if not new_name:
                    new_name = current_name
                    break
                is_valid, err_msg = validate_name(new_name)
                if is_valid:
                    break
                print(f"[Error] {err_msg}")
                
            new_address = input(f"New Adress [{current_address}]: ").strip()
            if not new_address:
                new_address = current_address
                
            while True:
                new_email = input(f"New Email [{current_email}]: ").strip()
                if not new_email:
                    new_email = current_email
                    break
                is_valid, err_msg = validate_email(new_email)
                if is_valid:
                    break
                print(f"[Error] {err_msg}")
                
            updated_row = [id_query, new_name, new_address, new_email]
            sheet_row_num = row_idx_found + 1
            
            print("\nUpdating row...")
            update_row(service, sheet_row_num, updated_row)
            break
            
        elif choice == '2':
            rows = get_all_rows(service)
            handle_add_new_row(service, rows)
            break
            
        elif choice == '3':
            break
        else:
            print("Invalid choice. Please choose 1, 2, or 3.")

def main():
    print("Connecting to Google Sheets API...")
    service = init_service()
    
    # Initialize sheet header if empty (dummy data auto-insert removed)
    initialize_sheet_if_empty(service)
    
    while True:
        print("\n===== Google Sheets Terminal CLI =====")
        print("1. Read values")
        print("2. Update values")
        print("3. Exit")
        choice = input("Enter choice (1-3): ").strip()
        
        if choice == '1':
            handle_read_values(service)
        elif choice == '2':
            handle_update_values(service)
        elif choice == '3':
            print("Exiting application. Goodbye!")
            break
        else:
            print("Invalid choice. Please choose 1, 2, or 3.")

if __name__ == "__main__":
    main()