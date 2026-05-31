import base64
import os
import re
import time
from base64 import urlsafe_b64encode
from email.mime.text import MIMEText
from html.parser import HTMLParser
from typing import Optional

import requests as http_requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import database as db
import gmail_client as gc
import session as sess

app = FastAPI(title="Email Unsubscriber")

# Per-session OAuth flows: session_id -> Flow object.
# Flows are short-lived (seconds between /login and /callback), so this
# in-memory dict is sufficient; it clears on every server restart.
_auth_flows: dict = {}


# ── Session middleware ────────────────────────────────────────────────────────

@app.middleware("http")
async def session_middleware(request: Request, call_next):
    sid = request.cookies.get(sess.SESSION_COOKIE, "")
    if not sess.is_valid_sid(sid):
        sid = sess.new_session_id()
    request.state.sid = sid
    # Only touch (disk write) on API calls; static assets don't reset inactivity
    if request.url.path.startswith("/api/"):
        sess.touch_session(gc.DATA_DIR, sid)
    response = await call_next(request)
    sess.set_session_cookie(response, sid)
    return response


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    db.init_db()
    sess.cleanup_expired_sessions(gc.DATA_DIR)


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def app_status():
    return {"credentials_configured": gc.has_credentials()}


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/api/auth/status")
async def auth_status(request: Request):
    sid = request.state.sid
    creds = gc.load_credentials(sid)
    return {
        "authenticated": creds is not None,
        "has_credentials": gc.has_credentials(),
        "current_account": gc.get_current_account(sid),
        "accounts": gc.list_accounts(sid),
    }


@app.get("/api/auth/login")
async def login(request: Request):
    if not gc.has_credentials():
        raise HTTPException(
            status_code=400,
            detail="credentials.json not found. See README.",
        )
    sid = request.state.sid
    flow = gc.get_flow()
    _auth_flows[sid] = flow
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(auth_url)


@app.get("/api/auth/add-account")
async def add_account_oauth(request: Request):
    if not gc.has_credentials():
        raise HTTPException(
            status_code=400,
            detail="credentials.json not found.",
        )
    sid = request.state.sid
    flow = gc.get_flow()
    _auth_flows[sid] = flow
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="select_account consent"
    )
    return RedirectResponse(auth_url)


@app.get("/api/auth/callback")
async def auth_callback(request: Request, code: str, state: Optional[str] = None):
    sid = request.state.sid
    flow = _auth_flows.pop(sid, None)
    if flow is None:
        raise HTTPException(
            status_code=400,
            detail="Auth session expired. Please click Connect again.",
        )

    flow.fetch_token(code=code)
    creds = flow.credentials

    client_config = gc.credentials_config()
    config = client_config.get("web", client_config.get("installed", {}))

    email = gc.get_account_email(creds)
    if not email:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve account email from Google.",
        )

    gc.save_credentials(sid, creds, config["client_id"], config["client_secret"], email)
    return RedirectResponse("/?connected=1")


@app.post("/api/auth/switch/{email}")
async def switch_account(request: Request, email: str):
    sid = request.state.sid
    if email not in gc.list_accounts(sid):
        raise HTTPException(status_code=404, detail="Account not found.")
    gc.set_current_account(sid, email)
    return {"status": "ok", "current_account": email}


@app.delete("/api/auth/logout")
async def logout(request: Request):
    sid = request.state.sid
    current = gc.get_current_account(sid)
    if current:
        tf = gc.token_file_path(sid, current)
        if os.path.exists(tf):
            os.remove(tf)
    remaining = gc.list_accounts(sid)
    if remaining:
        gc.set_current_account(sid, remaining[0])
        return {"status": "ok", "next_account": remaining[0]}
    caf = os.path.join(gc.session_dir(sid), "current_account.json")
    if os.path.exists(caf):
        os.remove(caf)
    return {"status": "ok", "next_account": None}


# ── Scan ──────────────────────────────────────────────────────────────────────

@app.post("/api/scan")
async def scan(request: Request):
    sid = request.state.sid
    service = gc.build_service(sid)
    if not service:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account_email = gc.get_current_account(sid) or ""

    # Catch bulk/marketing mail by the List-Id header (list:*) plus Gmail's
    # promotions category. This finds emails the plain "unsubscribe" keyword
    # search misses — e.g. bank/newsletter blasts where the word only appears
    # inside images or footer fine print.
    query = "list:* OR category:promotions"

    messages = []
    page_token = None
    try:
        while True:
            params = {"userId": "me", "q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            results = service.users().messages().list(**params).execute()
            messages.extend(results.get("messages", []))
            page_token = results.get("nextPageToken")
            if not page_token or len(messages) >= 2000:
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not messages:
        bounces = _reconcile_bounces(service, sid, account_email)
        return {
            "found": 0,
            "total": db.count_subscriptions(sid, account_email),
            "body_link_found": 0,
            "bounces_reconciled": bounces,
        }

    # Batch-fetch headers + labels (100 per batch — Gmail API limit)
    email_metadata: dict = {}

    def on_metadata_response(request_id, response, exception):
        if exception or not response:
            return
        headers = {
            h["name"]: h["value"]
            for h in response.get("payload", {}).get("headers", [])
        }
        email_metadata[request_id] = {
            "headers": headers,
            "labels": response.get("labelIds", []),
        }

    for i in range(0, len(messages), 100):
        batch = service.new_batch_http_request(callback=on_metadata_response)
        for msg in messages[i:i + 100]:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "List-Unsubscribe"],
                ),
                request_id=msg["id"],
            )
        batch.execute()

    # Pass 1: header-based (List-Unsubscribe present)
    found_new = 0
    tracked_senders: set = set()
    for _msg_id, meta in email_metadata.items():
        headers = meta["headers"]
        unsubscribe_header = headers.get("List-Unsubscribe", "")
        if not unsubscribe_header:
            continue

        sender_name, sender_email = _parse_from(headers.get("From", ""))
        if not sender_email:
            continue
        tracked_senders.add(sender_email)

        method, target = _parse_unsubscribe_header(unsubscribe_header)
        if not method:
            continue

        subject = headers.get("Subject", "(no subject)")
        if db.add_subscription(
            sid, account_email, sender_email, sender_name, subject, method, target
        ):
            found_new += 1

    # Pass 2: body-link for CATEGORY_PROMOTIONS emails missing List-Unsubscribe.
    # Strictly limited to Gmail's tightest "marketing" classification to avoid
    # transactional mail (statements, receipts, password resets, etc).
    candidate_ids: list = []
    pass2_seen: set = set()
    for msg_id, meta in email_metadata.items():
        if meta["headers"].get("List-Unsubscribe"):
            continue
        if "CATEGORY_PROMOTIONS" not in meta["labels"]:
            continue
        _, sender_email = _parse_from(meta["headers"].get("From", ""))
        if not sender_email:
            continue
        if sender_email in tracked_senders or sender_email in pass2_seen:
            continue
        pass2_seen.add(sender_email)
        candidate_ids.append(msg_id)
        if len(candidate_ids) >= 50:  # bound API cost per scan
            break

    full_messages: dict = {}

    def on_full_response(request_id, response, exception):
        if exception or not response:
            return
        full_messages[request_id] = response

    for i in range(0, len(candidate_ids), 100):
        batch = service.new_batch_http_request(callback=on_full_response)
        for msg_id in candidate_ids[i:i + 100]:
            batch.add(
                service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ),
                request_id=msg_id,
            )
        batch.execute()

    body_link_found = 0
    for msg_id in candidate_ids:
        response = full_messages.get(msg_id)
        if not response:
            continue
        headers = email_metadata[msg_id]["headers"]
        sender_name, sender_email = _parse_from(headers.get("From", ""))
        subject = headers.get("Subject", "(no subject)")

        html = _find_html_body(response.get("payload", {}))
        if not html:
            continue
        url = _extract_unsub_link(html)
        if not url:
            continue

        if db.add_subscription(
            sid, account_email, sender_email, sender_name, subject,
            "http_body", url,
        ):
            body_link_found += 1

    bounces = _reconcile_bounces(service, sid, account_email)

    return {
        "found": found_new + body_link_found,
        "total": db.count_subscriptions(sid, account_email),
        "body_link_found": body_link_found,
        "bounces_reconciled": bounces,
    }


# ── Emails / Stats ────────────────────────────────────────────────────────────

@app.get("/api/emails")
async def get_emails(request: Request):
    sid = request.state.sid
    account_email = gc.get_current_account(sid) or ""
    return {"emails": db.get_subscriptions(sid, account_email)}


@app.get("/api/stats")
async def get_stats(request: Request):
    sid = request.state.sid
    account_email = gc.get_current_account(sid) or ""
    return db.get_stats(sid, account_email)


# ── Unsubscribe ───────────────────────────────────────────────────────────────

@app.post("/api/unsubscribe/{sub_id}")
async def unsubscribe_one(request: Request, sub_id: int):
    sid = request.state.sid
    service = gc.build_service(sid)
    if not service:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = db.get_subscription(sid, sub_id)
    if not row:
        raise HTTPException(status_code=404, detail="Subscription not found")

    try:
        if row["unsubscribe_method"] == "mailto":
            _send_unsubscribe_email(service, row["unsubscribe_target"])
        else:
            _http_unsubscribe(row["unsubscribe_target"])
        db.update_status(sub_id, "success")
        return {"status": "success"}
    except Exception as e:
        error = str(e)
        db.update_status(sub_id, "failed", error)
        return {"status": "failed", "error": error}


@app.post("/api/unsubscribe-all")
async def unsubscribe_all(request: Request):
    sid = request.state.sid
    service = gc.build_service(sid)
    if not service:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account_email = gc.get_current_account(sid) or ""
    pending = db.get_subscriptions(sid, account_email, status="pending")
    success_count, failed_count = 0, 0

    for row in pending:
        try:
            if row["unsubscribe_method"] == "mailto":
                _send_unsubscribe_email(service, row["unsubscribe_target"])
            else:
                _http_unsubscribe(row["unsubscribe_target"])
            db.update_status(row["id"], "success")
            success_count += 1
        except Exception as e:
            db.update_status(row["id"], "failed", str(e))
            failed_count += 1
        time.sleep(0.3)

    return {"success": success_count, "failed": failed_count}


# ── Private helpers ───────────────────────────────────────────────────────────

def _parse_from(from_header: str):
    match = re.match(r'"?([^"<]+?)"?\s*<([^>]+)>', from_header.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip().lower()
    return "", from_header.strip().lower()


def _parse_unsubscribe_header(header: str):
    # Prefer HTTP over mailto: HTTP gives an immediate status-code signal,
    # while mailto is fire-and-forget — Gmail accepting the send doesn't
    # mean the receiving server accepted it (bounces come back async and
    # would leave us marking failed unsubscribes as "success").
    http = re.search(r"<(https?://[^>]+)>", header, re.IGNORECASE)
    if http:
        return "http", http.group(1)
    mailto = re.search(r"<mailto:([^>]+)>", header, re.IGNORECASE)
    if mailto:
        return "mailto", mailto.group(1)
    return None, None


def _send_unsubscribe_email(service, to_field: str):
    addr, subject = to_field, "Unsubscribe"
    if "?" in to_field:
        addr, params = to_field.split("?", 1)
        m = re.search(r"subject=([^&]+)", params, re.IGNORECASE)
        if m:
            subject = m.group(1).replace("%20", " ").replace("+", " ")
    msg = MIMEText("")
    msg["to"] = addr
    msg["subject"] = subject
    raw = urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _http_unsubscribe(url: str):
    ua = {"User-Agent": "Mozilla/5.0 (compatible; EmailUnsubscriber/1.0)"}
    try:
        r = http_requests.post(
            url,
            data={"List-Unsubscribe": "One-Click"},
            headers=ua,
            timeout=15,
            allow_redirects=True,
        )
        if r.status_code < 400:
            return
    except Exception:
        pass
    http_requests.get(url, headers=ua, timeout=15, allow_redirects=True)


# ── Body-link parsing (Pass 2) ────────────────────────────────────────────────

_UNSUB_KEYWORDS = ("unsubscribe", "opt-out", "opt out", "退订", "取消订阅")


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: list = []
        self._href = None
        self._text: list = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text).strip()))
            self._href = None
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def _find_html_body(payload: dict) -> Optional[str]:
    """Walk MIME parts, return decoded text/html body if present."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return None
    for part in payload.get("parts", []) or []:
        result = _find_html_body(part)
        if result:
            return result
    return None


def _extract_unsub_link(html: str) -> Optional[str]:
    """Find an unsubscribe link with strict criteria.

    Requires: anchor text < 50 chars and contains an exact unsubscribe
    keyword. We deliberately skip ambiguous wording like "manage
    preferences" — those frequently appear in transactional emails too.
    """
    parser = _AnchorCollector()
    try:
        parser.feed(html)
    except Exception:
        return None
    for href, text in parser.anchors:
        if not href.lower().startswith(("http://", "https://")):
            continue
        t = text.lower()
        if len(t) > 50 or not t:
            continue
        if any(kw in t for kw in _UNSUB_KEYWORDS):
            return href
    return None


# ── Bounce reconciliation ─────────────────────────────────────────────────────

def _reconcile_bounces(service, session_id: str, account_email: str) -> int:
    """Find recent mailer-daemon bounces and flip matching mailto
    subscriptions from 'success' to 'failed'. Never adds new senders."""
    try:
        results = service.users().messages().list(
            userId="me",
            q="from:mailer-daemon newer_than:30d",
            maxResults=200,
        ).execute()
    except Exception:
        return 0

    bounce_msgs = results.get("messages", [])
    if not bounce_msgs:
        return 0

    failed_addrs: set = set()

    def on_response(_id, response, exception):
        if exception or not response:
            return
        for h in response.get("payload", {}).get("headers", []):
            if h["name"].lower() == "x-failed-recipients":
                for addr in h["value"].split(","):
                    addr = addr.strip().lower()
                    if addr:
                        failed_addrs.add(addr)

    for i in range(0, len(bounce_msgs), 100):
        batch = service.new_batch_http_request(callback=on_response)
        for msg in bounce_msgs[i:i + 100]:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["X-Failed-Recipients"],
                ),
                request_id=msg["id"],
            )
        batch.execute()

    reconciled = 0
    for addr in failed_addrs:
        for row in db.find_mailto_by_address(session_id, account_email, addr):
            if row["status"] == "success":
                db.update_status(
                    row["id"], "failed",
                    "Bounced: unsubscribe email rejected by remote server",
                )
                reconciled += 1
    return reconciled


# Serve frontend (must be last — catches all unmatched paths)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
