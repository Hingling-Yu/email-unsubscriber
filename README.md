# Email Unsubscriber

A web app that scans your Gmail for marketing emails and unsubscribes from them in one click — no installs, no command line.

**Live app:** [https://email-unsubscriber.up.railway.app](https://email-unsubscriber.up.railway.app)

---

## Getting Started

1. Open [https://email-unsubscriber.up.railway.app](https://email-unsubscriber.up.railway.app) in any browser
2. Click **Connect with Google** and sign in with your Gmail account
3. Click **Scan Emails** to find marketing senders in your inbox
4. Click **Unsubscribe** next to any sender, or **Unsubscribe All Pending** to process everything at once

That's it — no downloads, no setup required.

---

## How It Works

### Scanning

The scanner searches Gmail using two passes:

1. **Header-based (primary):** Queries for emails with a `List-Unsubscribe` header (the RFC 2369 standard used by bulk mailers), plus Gmail's `CATEGORY_PROMOTIONS` label. Headers are batch-fetched 100 at a time using the Gmail batch API.

2. **Body-link parsing (fallback):** For promotional emails that lack the header, the scanner downloads the full message body and extracts unsubscribe links using anchor text matching — limited to 50 candidates per scan to control API cost.

Senders are deduplicated by email address, so repeated scans only surface new senders.

### Unsubscribing

Two methods are used depending on what the sender provides:

- **HTTP (preferred):** POSTs to the one-click unsubscribe URL per [RFC 8058](https://www.rfc-editor.org/rfc/rfc8058), falling back to a GET request. A successful HTTP status code is an immediate confirmation.
- **mailto:** Sends a blank unsubscribe email via your Gmail account. HTTP is preferred because `mailto` is fire-and-forget — Gmail accepting the send does not mean the recipient's server accepted it.

### Bounce Reconciliation

On each scan, the app queries your inbox for recent MAILER-DAEMON bounce notices and flips any matching `mailto` unsubscribes from `success` to `failed`, preventing false positives in the dashboard.

### Storage

Per-account subscription records are stored in SQLite. On Railway, the database lives on a persistent volume at `/data`. No email content is stored — only sender metadata (address, name, last subject line, unsubscribe method, status).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · uvicorn |
| Auth | Google OAuth 2.0 (via `google-auth-oauthlib`) |
| Gmail API | `google-api-python-client` batch requests |
| Storage | SQLite (`sqlite3`) |
| Frontend | Vanilla HTML / CSS / JS — no framework |
| Hosting | Railway (nixpacks build, persistent volume) |
| PWA | Web App Manifest + Service Worker (offline shell cache) |

---

## Self-Hosting

### Prerequisites

- Python 3.12+
- A Google Cloud project with the Gmail API enabled and an OAuth 2.0 Web Client credential

### Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. Enable the **Gmail API** under **APIs & Services > Library**.
3. Configure the **OAuth consent screen** (External), add your Gmail as a test user.
4. Create an **OAuth client ID** (type: Web application). Add your redirect URI:
   - Local: `http://localhost:8000/api/auth/callback`
   - Railway: `https://your-app.up.railway.app/api/auth/callback`
5. Download the credentials JSON.

### Running Locally

```bash
git clone https://github.com/Hingling-Yu/email-unsubscriber.git
cd email-unsubscriber
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Place your downloaded credentials file as `credentials.json` in the project root, then:

```bash
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

### Deploying to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

1. Push this repo to GitHub and connect it to a new Railway project.
2. Add a **Volume** mounted at `/data` for SQLite persistence.
3. Set the following environment variables in Railway:

| Variable | Value |
|---|---|
| `DATA_DIR` | `/data` |
| `OAUTH_REDIRECT_URI` | `https://your-app.up.railway.app/api/auth/callback` |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of your `credentials.json` file (paste as a single JSON string) |

4. Add `https://your-app.up.railway.app/api/auth/callback` to the **Authorized redirect URIs** in Google Cloud Console.
5. Railway will build and deploy automatically using the `railway.toml` config included in this repo.

### Project Structure

```
main.py             FastAPI routes and scan/unsubscribe logic
gmail_client.py     Gmail OAuth2 + API wrapper (supports env var credentials)
database.py         SQLite schema and queries
requirements.txt    Python dependencies
railway.toml        Railway build and deploy config
static/
  index.html        Single-page dashboard
  style.css         Responsive styles (mobile-first, 375px+)
  app.js            Frontend logic (no framework)
  manifest.json     PWA manifest
  sw.js             Service worker (offline shell cache only)
```
