#!/usr/bin/env python3
"""Email monitor — checks Gmail for new important emails, sends digest via email.

Runs on a schedule (e.g. every 5 minutes). Tracks seen message IDs to avoid
duplicate notifications. Aggressively filters spam, marketing, and noise.
Batches important emails into a single clean HTML digest email + a short
Telegram ping.

Usage:
    python3 email-monitor.py          # Normal check
    python3 email-monitor.py --dry    # Dry run — show what would notify, don't send
"""

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# --------------- Config ---------------

STATE_FILE = Path.home() / ".openclaw" / "email-monitor-state.json"
CREDENTIALS_FILE = Path.home() / ".openclaw" / "google-credentials.json"
TOKEN_FILE = Path.home() / ".openclaw" / "google-token.json"
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "dinisusmc@gmail.com")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Max emails to fetch per check
CHECK_LIMIT = 25

# Max seen IDs to retain (rolling window)
MAX_SEEN_IDS = 500

# --------------- Spam / Noise Filters ---------------

# Sender patterns to always ignore (case-insensitive regex)
SPAM_SENDER_PATTERNS = [
    r"noreply@",
    r"no-reply@",
    r"no\.reply@",
    r"donotreply@",
    r"do-not-reply@",
    r"notifications?@",
    r"notify@",
    r"alert@",
    r"mailer-daemon@",
    r"postmaster@",
    r"bounce@",
    r"newsletter@",
    r"marketing@",
    r"promo(tions?)?@",
    r"digest@",
    r"updates?@",
    r"news@",
    r"info@.*\.com",
    r"support@.*shopify",
    r"@linkedin\.com",
    r"@facebookmail\.com",
    r"@facebook\.com",
    r"@pinterest\.com",
    r"@quora\.com",
    r"@reddit\.com",
    r"@medium\.com",
    r"@substack\.com",
    r"@mailchimp\.com",
    r"@sendgrid\.",
    r"@amazonses\.com",
    r"@mandrillapp\.com",
    r"@em\d+\.\w+\.com",
    r"@e\..*\.com",
    r"@mail\.\w+\.com",
    r"@mg\.\w+\.com",  # Mailgun
    r"@bounce\.\w+\.com",
    r"@cmail\d+\.com",
    r"@t\.co",
    r"@discord\.com",
    r"@twitchmail\.com",
    r"@steampowered\.com",
    r"@playstation\.com",
    r"@xbox\.com",
    r"@ea\.com",
    r"@epicgames\.com",
    r"@accounts\.google\.com",
    r"@google\.com",
    r"@youtube\.com",
    r"@spotifymail\.com",
    r"@spotify\.com",
    r"@uber\.com",
    r"@lyft\.com",
    r"@dominos\.com",
    r"@doordash\.com",
    r"@grubhub\.com",
    r"@ubereats\.com",
    r"@yelp\.com",
    r"@groupon\.com",
    r"@wish\.com",
    r"@aliexpress\.com",
    r"@temu\.com",
    r"@shein\.com",
    r"@banggood\.com",
    r"@amazon\.com",
    r"@ebay\.com",
    r"@etsy\.com",
    r"@target\.com",
    r"@walmart\.com",
    r"@bestbuy\.com",
    r"@apple\.com",
    r"@microsoft\.com",
    r"@github\.com",
]

# Subject patterns to always ignore (case-insensitive regex)
SPAM_SUBJECT_PATTERNS = [
    r"unsubscribe",
    r"^(re:\s*)?your\s+(order|shipment|delivery|package|receipt)",
    r"^(re:\s*)?confirm(ation)?\s+(of|your)",
    r"verify your (email|account)",
    r"password reset",
    r"reset your password",
    r"welcome to",
    r"thanks? for (signing|joining|subscribing|registering|your (order|purchase))",
    r"thank you for your (order|purchase|payment)",
    r"your (weekly|daily|monthly) (digest|summary|report|update|newsletter|recap)",
    r"(weekly|daily|monthly) (digest|summary|report|update|newsletter|recap)",
    r"here.s what you missed",
    r"new sign[- ]in",
    r"security alert",
    r"two[- ]factor",
    r"2fa|mfa",
    r"verify (your|this) (device|login)",
    r"account (activity|update|notification|security)",
    r"(flash|mega|big|huge|exclusive|limited)\s+sale",
    r"\d+%\s+off",
    r"free shipping",
    r"act now",
    r"limited time",
    r"special offer",
    r"black friday|cyber monday|prime day",
    r"deal of the day",
    r"don.t miss",
    r"last chance",
    r"price drop",
    r"save \$",
    r"your (cart|wishlist)",
    r"items? in your cart",
    r"complete your (purchase|order)",
    r"we miss you",
    r"come back",
    r"you.re invited",
    r"invitation to",
    r"survey|feedback|rate (us|your|this)",
    r"review your (experience|purchase|order)",
    r"how (did we|was your)",
    r"(social|community) update",
    r"someone (liked|commented|shared|followed|mentioned|replied)",
    r"new (follower|like|comment|connection|endorsement)",
    r"trending (on|in|near)",
    r"what.s happening",
    r"people you may know",
    r"job (alert|recommendation|match)",
    r"new jobs? for you",
    r"recommended for you",
    r"(your )?subscription (is |will )?(renew|expir|cancel)",
    r"billing (statement|summary|update|notification)",
    r"invoice #",
    r"payment (received|confirmed|processed|failed|declined)",
    r"receipt for",
    r"transaction (alert|notification)",
    r"statement (is )?ready",
    r"your (plan|membership|trial)",
    r"successfully (created|updated|deleted|added|removed)",
    r"has been (created|updated|deleted|added|removed|shipped|delivered)",
]

# Gmail labels that indicate spam/noise
NOISE_LABELS = {
    "SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
    "CATEGORY_UPDATES", "CATEGORY_FORUMS",
}

# Labels that indicate importance
IMPORTANT_LABELS = {
    "IMPORTANT", "STARRED", "CATEGORY_PERSONAL",
}


# --------------- State Management ---------------

def load_state() -> dict:
    """Load seen message IDs and last check time."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen_ids": [], "last_check": None}


def save_state(state: dict) -> None:
    """Save state, trimming seen_ids to MAX_SEEN_IDS."""
    state["seen_ids"] = state["seen_ids"][-MAX_SEEN_IDS:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# --------------- Gmail ---------------

def _get_credentials():
    """Load or refresh OAuth2 credentials."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            return None
    return creds


def _get_gmail_service():
    from googleapiclient.discovery import build
    creds = _get_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def fetch_recent_unread(service, limit: int = CHECK_LIMIT) -> list[dict]:
    """Fetch recent unread inbox emails with metadata."""
    try:
        results = service.users().messages().list(
            userId="me", q="in:inbox is:unread", maxResults=limit
        ).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date", "List-Unsubscribe"]
            ).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            labels = set(detail.get("labelIds", []))
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", "")[:200],
                "labels": labels,
                "has_unsubscribe": bool(headers.get("List-Unsubscribe", "")),
            })
        return emails
    except Exception as e:
        print(f"[email-monitor] Gmail fetch error: {e}", file=sys.stderr)
        return []


# --------------- Filtering ---------------

def is_spam_or_noise(email: dict) -> tuple[bool, str]:
    """Check if an email is spam/noise. Returns (is_spam, reason)."""
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()
    labels = email.get("labels", set())

    # Check Gmail labels first
    if labels & NOISE_LABELS:
        matched = labels & NOISE_LABELS
        return True, f"noise label: {', '.join(matched)}"

    # Has List-Unsubscribe header = mass mail
    if email.get("has_unsubscribe"):
        # Still allow if it's marked IMPORTANT by Gmail
        if "IMPORTANT" not in labels:
            return True, "bulk mail (List-Unsubscribe header)"

    # Check sender patterns
    for pattern in SPAM_SENDER_PATTERNS:
        if re.search(pattern, sender, re.IGNORECASE):
            return True, f"sender match: {pattern}"

    # Check subject patterns
    for pattern in SPAM_SUBJECT_PATTERNS:
        if re.search(pattern, subject, re.IGNORECASE):
            return True, f"subject match: {pattern}"

    return False, ""


def get_importance_score(email: dict) -> int:
    """Score email importance 1-5. Higher = more important."""
    score = 3  # Default: normal
    labels = email.get("labels", set())
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()

    # Gmail thinks it's important
    if "IMPORTANT" in labels:
        score += 1
    if "STARRED" in labels:
        score += 1
    if "CATEGORY_PERSONAL" in labels:
        score += 1

    # Direct/personal indicators
    if re.search(r"(urgent|asap|critical|emergency|important|action required|immediate)", subject, re.IGNORECASE):
        score += 1

    # Looks like a real person (has a name before the email)
    if re.search(r'^[A-Z][a-z]+ [A-Z][a-z]+\s*<', email.get("from", "")):
        score += 1

    return min(score, 5)


# --------------- Telegram ---------------

def load_env():
    """Load env from ~/.env."""
    env_file = Path.home() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key and not os.environ.get(key):
                    os.environ[key] = val


def send_telegram(text: str) -> bool:
    """Send a short text ping via Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"[email-monitor] Telegram send error: {e}", file=sys.stderr)
        return False


def send_digest_email(important_emails: list[dict]) -> bool:
    """Send a clean HTML digest email with all important new emails."""
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            print("[email-monitor] Gmail not authenticated for sending", file=sys.stderr)
            return False

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    now = datetime.now()
    count = len(important_emails)
    subject = f"📬 {count} new email{'s' if count != 1 else ''} — {now.strftime('%b %-d, %-I:%M %p')}"

    html = _build_digest_html(important_emails, now)

    msg = MIMEMultipart("alternative")
    msg["To"] = GMAIL_ADDRESS
    msg["From"] = GMAIL_ADDRESS
    msg["Subject"] = subject
    msg.attach(MIMEText(_build_digest_plain(important_emails), "plain"))
    msg.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception as e:
        print(f"[email-monitor] Send digest error: {e}", file=sys.stderr)
        return False


def _build_digest_html(emails: list[dict], now: datetime) -> str:
    """Build clean HTML digest email."""
    rows = ""
    for em in emails:
        importance = em.get("_importance", 3)
        if importance >= 5:
            badge = '<span style="color:#dc3545;font-weight:bold;">● URGENT</span>'
            row_bg = "#fff5f5"
        elif importance >= 4:
            badge = '<span style="color:#fd7e14;font-weight:bold;">● Important</span>'
            row_bg = "#fff8f0"
        else:
            badge = ""
            row_bg = "#ffffff"

        sender = _clean_sender(em.get("from", "Unknown"))
        subject = em.get("subject", "(no subject)")
        snippet = em.get("snippet", "")
        date = em.get("date", "")

        # Escape HTML
        for char, esc in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")]:
            sender = sender.replace(char, esc)
            subject = subject.replace(char, esc)
            snippet = snippet.replace(char, esc)
            date = date.replace(char, esc)

        rows += f"""
        <tr style="background:{row_bg};">
            <td style="padding:16px 20px;border-bottom:1px solid #eee;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <strong style="color:#1a1a2e;font-size:15px;">{sender}</strong>
                    {f'<span style="margin-left:8px;">{badge}</span>' if badge else ''}
                </div>
                <div style="color:#2d3748;font-size:14px;font-weight:600;margin-bottom:6px;">{subject}</div>
                <div style="color:#718096;font-size:13px;line-height:1.4;">{snippet}</div>
                <div style="color:#a0aec0;font-size:11px;margin-top:6px;">{date}</div>
            </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f7f8fc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <div style="max-width:600px;margin:20px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:24px 20px;text-align:center;">
            <h1 style="color:#fff;margin:0;font-size:20px;font-weight:600;">📬 New Emails</h1>
            <p style="color:rgba(255,255,255,0.85);margin:6px 0 0;font-size:13px;">
                {len(emails)} message{'s' if len(emails) != 1 else ''} · {now.strftime('%A, %b %-d at %-I:%M %p')}
            </p>
        </div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
            {rows}
        </table>
        <div style="padding:16px 20px;text-align:center;color:#a0aec0;font-size:11px;border-top:1px solid #eee;">
            Ashley Email Monitor · Reply /emails in Telegram to manage
        </div>
    </div>
</body>
</html>"""


def _build_digest_plain(emails: list[dict]) -> str:
    """Build plain text fallback for digest."""
    lines = [f"📬 {len(emails)} new email(s)\n"]
    for em in emails:
        sender = _clean_sender(em.get("from", "Unknown"))
        subject = em.get("subject", "(no subject)")
        snippet = em.get("snippet", "")
        lines.append(f"From: {sender}")
        lines.append(f"Subject: {subject}")
        if snippet:
            lines.append(f"  {snippet[:120]}")
        lines.append("")
    return "\n".join(lines)


def _clean_sender(sender: str) -> str:
    """Extract clean display name from email sender."""
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"').strip("'")
        addr = sender.split("<")[1].rstrip(">")
        return name if name else addr
    return sender


# --------------- Main ---------------

def run_check(dry_run: bool = False) -> dict:
    """Run one email check cycle. Returns stats."""
    load_env()
    state = load_state()
    seen_ids = set(state.get("seen_ids", []))

    service = _get_gmail_service()
    if not service:
        print("[email-monitor] Gmail not authenticated", file=sys.stderr)
        return {"error": "not authenticated"}

    emails = fetch_recent_unread(service)
    stats = {
        "total_unread": len(emails),
        "new": 0,
        "filtered_spam": 0,
        "notified": 0,
        "errors": 0,
    }

    important_emails = []

    for email in emails:
        # Skip already-seen
        if email["id"] in seen_ids:
            continue

        stats["new"] += 1

        # ALWAYS mark as seen — this is the dedup fix
        seen_ids.add(email["id"])

        # Spam filter
        is_spam, reason = is_spam_or_noise(email)
        if is_spam:
            stats["filtered_spam"] += 1
            if dry_run:
                print(f"  [FILTERED] {email['subject'][:60]} — {reason}")
            continue

        # Score importance
        importance = get_importance_score(email)
        email["_importance"] = importance

        if dry_run:
            print(f"  [NOTIFY importance={importance}] {email['from'][:30]} — {email['subject'][:60]}")

        important_emails.append(email)

    # Send batched digest if there are important emails
    if important_emails and not dry_run:
        # Send clean HTML digest email
        if send_digest_email(important_emails):
            stats["notified"] = len(important_emails)
        else:
            stats["errors"] = len(important_emails)

        # Send short Telegram ping (just a count, not the full content)
        count = len(important_emails)
        subjects = [e.get("subject", "")[:40] for e in important_emails[:3]]
        preview = " · ".join(subjects)
        if count > 3:
            preview += f" (+{count - 3} more)"
        send_telegram(f"📬 {count} new email{'s' if count != 1 else ''}: {preview}\nDigest sent to your inbox.")

    # Save state
    state["seen_ids"] = list(seen_ids)
    if not dry_run:
        save_state(state)

    return stats


if __name__ == "__main__":
    dry_run = "--dry" in sys.argv

    if dry_run:
        print("[email-monitor] DRY RUN — no notifications will be sent\n")

    stats = run_check(dry_run=dry_run)

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[email-monitor {ts}] unread={stats.get('total_unread', 0)} "
          f"new={stats.get('new', 0)} "
          f"filtered={stats.get('filtered_spam', 0)} "
          f"notified={stats.get('notified', 0)} "
          f"errors={stats.get('errors', 0)}")
