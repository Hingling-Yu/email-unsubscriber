import json
import os
import shutil
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
CURRENT_ACCOUNT_FILE = os.path.join(DATA_DIR, "current_account.json")
REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback"
)


def credentials_config() -> Optional[dict]:
    """Return OAuth client config from GOOGLE_CREDENTIALS_JSON env var or local file."""
    env_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if env_json:
        try:
            return json.loads(env_json)
        except (json.JSONDecodeError, ValueError):
            pass
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def has_credentials() -> bool:
    return credentials_config() is not None


def token_file_path(email: str) -> str:
    return os.path.join(DATA_DIR, f"token_{email}.json")


def get_current_account() -> Optional[str]:
    if not os.path.exists(CURRENT_ACCOUNT_FILE):
        return None
    with open(CURRENT_ACCOUNT_FILE) as f:
        return json.load(f).get("email")


def set_current_account(email: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CURRENT_ACCOUNT_FILE, "w") as f:
        json.dump({"email": email}, f)


def list_accounts() -> List[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(
        fname[6:-5]
        for fname in os.listdir(DATA_DIR)
        if fname.startswith("token_") and fname.endswith(".json")
    )


def get_flow() -> Flow:
    config = credentials_config()
    if config is None:
        raise RuntimeError("No OAuth credentials configured.")
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=REDIRECT_URI)


def load_credentials(email: Optional[str] = None) -> Optional[Credentials]:
    if email is None:
        email = get_current_account()
    if not email:
        return None
    tf = token_file_path(email)
    if not os.path.exists(tf):
        return None
    with open(tf) as f:
        data = json.load(f)
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(
                creds, data["client_id"], data["client_secret"], email
            )
        except Exception:
            return None
    return creds


def save_credentials(
    creds: Credentials, client_id: str, client_secret: str, email: str
):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(token_file_path(email), "w") as f:
        json.dump(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            f,
        )
    set_current_account(email)


def get_account_email(creds: Credentials) -> Optional[str]:
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile["emailAddress"]
    except Exception:
        return None


def build_service(email: Optional[str] = None):
    creds = load_credentials(email)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def migrate_legacy_token():
    """Migrate old token.json to per-account token_{email}.json format."""
    legacy = os.path.join(BASE_DIR, "token.json")
    if not os.path.exists(legacy) or os.path.exists(CURRENT_ACCOUNT_FILE):
        return
    try:
        with open(legacy) as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=SCOPES,
        )
        email = get_account_email(creds)
        if email:
            shutil.copy(legacy, token_file_path(email))
            set_current_account(email)
            os.remove(legacy)
    except Exception:
        pass
