from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_drive_credentials():
    """
    Returns authenticated Google Drive credentials.

    Expects credentials.json to live outside the repo.
    Example location:
    C:/Users/<you>/.ember/credentials.json
    """

    ember_dir = Path.home() / ".ember"
    ember_dir.mkdir(exist_ok=True)

    credentials_path = ember_dir / "credentials.json"
    token_path = ember_dir / "token.json"

    creds = None

    # Load cached token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or authenticate
    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path,
                SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save token
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return creds