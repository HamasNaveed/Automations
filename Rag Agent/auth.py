from google_services import get_credentials
import os

if __name__ == "__main__":
    print("Starting authentication flow for Google Services...")
    try:
        creds = get_credentials(allow_interactive=True)
        if creds and creds.valid:
            print("\nSuccess! Google Services are authenticated.")
            import google_services
            print(f"Token saved to: {google_services.TOKEN_PATH}")
        else:
            print("\nAuthentication failed or was incomplete.")
    except Exception as e:
        print(f"\nAn error occurred during authentication: {e}")
