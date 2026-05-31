import os
import re
import secrets
import shutil
import time

SESSION_COOKIE = "sid"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

# Use secure (HTTPS-only) cookies when running on Railway
SECURE_COOKIES = os.environ.get("RAILWAY_ENVIRONMENT") is not None

_SID_RE = re.compile(r"^[0-9a-f]{64}$")


def is_valid_sid(sid: str) -> bool:
    return bool(sid) and bool(_SID_RE.match(sid))


def new_session_id() -> str:
    return secrets.token_hex(32)


def set_session_cookie(response, sid: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sid,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


def touch_session(data_dir: str, session_id: str) -> None:
    """Update last_active so the session's 30-day inactivity clock resets."""
    d = os.path.join(data_dir, "sessions", session_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "last_active"), "w") as f:
        f.write(str(time.time()))


def cleanup_expired_sessions(data_dir: str) -> None:
    """Delete session directories that have been inactive for over 30 days."""
    root = os.path.join(data_dir, "sessions")
    if not os.path.isdir(root):
        return
    cutoff = time.time() - SESSION_MAX_AGE
    for sid in os.listdir(root):
        sdir = os.path.join(root, sid)
        if not os.path.isdir(sdir):
            continue
        la_path = os.path.join(sdir, "last_active")
        try:
            if os.path.isfile(la_path):
                with open(la_path) as f:
                    ts = float(f.read().strip())
            else:
                ts = os.path.getmtime(sdir)
            if ts < cutoff:
                shutil.rmtree(sdir, ignore_errors=True)
        except Exception:
            pass
