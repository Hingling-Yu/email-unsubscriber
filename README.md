# Email Unsubscriber

Automatically finds marketing emails in your Gmail and unsubscribes from them via the standard `List-Unsubscribe` header. Tracks everything locally in SQLite.

## Prerequisites

- Python 3.9+
- A Google account

## Step 1 — Google Cloud Setup

You need OAuth2 credentials so the app can read your Gmail.

1. Go to https://console.cloud.google.com/ and create a new project (or select an existing one).

2. In the left menu, go to **APIs & Services > Library**. Search for "Gmail API" and click **Enable**.

3. Go to **APIs & Services > OAuth consent screen**.
   - Choose **External** and click Create.
   - Fill in App name (e.g. "Email Unsubscriber") and your email for support.
   - Skip Scopes for now and click Save.
   - Under **Test users**, add your Gmail address. Click Save.

4. Go to **APIs & Services > Credentials**.
   - Click **Create Credentials > OAuth client ID**.
   - Application type: **Web application**.
   - Name: anything (e.g. "Email Unsubscriber").
   - Under **Authorized redirect URIs**, add: `http://localhost:8000/api/auth/callback`
   - Click Create.

5. Click the download icon next to your new credential. Save the file as `credentials.json` in this folder (next to `main.py`).

## Step 2 — Install Dependencies

```bash
cd email-unsubscriber
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 3 — Run

```bash
uvicorn main:app --reload
```

Open http://localhost:8000 in your browser.

## Usage

1. Click **Connect with Google** and sign in with your Gmail account.
2. Click **Scan Emails** — the app searches your inbox for emails with a `List-Unsubscribe` header and adds senders to the list.
3. Click **Unsubscribe** next to individual senders, or **Unsubscribe All Pending** to process everything at once.
4. The dashboard shows counts: found / unsubscribed / pending / failed.
5. Run **Scan Emails** again any time to pick up new marketing emails.

## How it works

- **Scan**: Searches Gmail for emails containing "unsubscribe", then batch-fetches headers. Only emails with a `List-Unsubscribe` header are tracked, deduped by sender address.
- **mailto method**: Sends a blank unsubscribe email via your Gmail account.
- **http method**: POSTs to the one-click unsubscribe URL (RFC 8058), falling back to GET.
- **Storage**: SQLite database (`subscriptions.db`) — all data stays on your machine.

## Files

```
main.py             Backend API (FastAPI)
gmail_client.py     Gmail OAuth2 + API wrapper
database.py         SQLite storage
requirements.txt    Python dependencies
static/             Web dashboard (HTML/CSS/JS)
credentials.json    Your Google OAuth credentials (you provide this)
token.json          Stored access token (auto-generated)
subscriptions.db    Tracking database (auto-generated)
```
