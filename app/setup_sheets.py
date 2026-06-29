"""
One-time setup: initialise the Google Sheet with all required tabs and headers.
Run: python setup_sheets.py
"""
import json
import sys
import os

def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        print("ERROR: config.json not found. Run the app first and complete setup via the browser.")
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    from sheets.client import SheetsClient, SHEET_HEADERS
    import gspread

    print("Connecting to Google Sheets...")
    client = SheetsClient(cfg["credentials_path"], cfg["spreadsheet_id"])

    print("Checking / creating worksheet tabs...")
    for name, headers in SHEET_HEADERS.items():
        try:
            ws = client.ss.worksheet(name)
            print(f"  ✓ '{name}' already exists")
        except gspread.WorksheetNotFound:
            ws = client.ss.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
            print(f"  + Created '{name}'")

    print("\nSetup complete! All sheets are ready.")
    print(f"Open your spreadsheet: https://docs.google.com/spreadsheets/d/{cfg['spreadsheet_id']}/edit")


if __name__ == "__main__":
    main()
