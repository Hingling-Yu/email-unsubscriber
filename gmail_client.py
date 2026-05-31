import json
import os
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


# ── Session-scoped helpers ────────────────────────────────────────────────────

def session_dir(session_id: str) -> str:
    return os.path.join(DATA_DIR, "sessions", session_id)


def token_file_path(session_id: str, email: str) -> str:
    return os.path.join(session_dir(session_id), f"token_{email}.json")


def _current_account_file(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "current_account.json")


def get_current_account(session_id: str) -> Optional[str]:
    path = _current_account_file(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("email")
    except (json.JSONDecodeError, OSError):
        return None


def set_current_account(session_id: str, email: str) -> None:
    d = session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    with open(_current_account_file(session_id), "w") as f:
        json.dump({"email": email}, f)


def list_accounts(session_id: str) -> List[str]:
    d = session_dir(session_id)
    if not os.path.isdir(d):
        return []
    return sorted(
        fname[6:-5]
        for fname in os.listdir(d)
        if fname.startswith("token_") and fname.endswith(".json")
    )


# ── OAuth flow ────────────────────────────────────────────────────────────────

def get_flow() -> Flow:
    config = credentials_config()
    if config is None:
        raise RuntimeError("No OAuth credentials configured.")
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=REDIRECT_URI)


# ── Credential I/O ────────────────────────────────────────────────────────────

def load_credentials(session_id: str, email: Optional[str] = None) -> Optional[Credentials]:
    if email is None:
        email = get_current_account(session_id)
    if not email:
        return None
    tf = token_file_path(session_id, email)
    if not os.path.exists(tf):
        return None
    try:
        with open(tf) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
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
                session_id, creds, data["client_id"], data["client_secret"], email
            )
        except Exception:
            return None
    return creds


def save_credentials(
    session_id: str,
    creds: Credentials,
    client_id: str,
    client_secret: str,
    email: str,
) -> None:
    d = session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    with open(token_file_path(session_id, email), "w") as f:
        json.dump(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            f,
        )
    set_current_account(session_id, email)


def get_account_email(creds: Credentials) -> Optional[str]:
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile["emailAddress"]
    except Exception:
        return None


def build_service(session_id: str, email: Optional[str] = None):
    creds = load_credentials(session_id, email)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)
