#!/usr/bin/env python3
"""Email monitor — checks Gmail for new important emails and notifies via Telegram.

Runs on a schedule (e.g. every 5 minutes). Tracks seen message IDs to avoid
duplicate notifications. Aggressively filters spam, marketing, and noise.

Usage:
    python3 email-monitor.py          # Normal check
    python3 email-monitor.py --dry    # Dry run — show what would notify, don't send
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------- Config ---------------

STATE_FILE = Path.home() / ".openclaw" / "email-monitor-state.json"
CREDENTIALS_FILE = Path.home() / ".openclaw" / "google-credentials.json"
TOKEN_FILE = Path.home() / ".openclaw" / "google-token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
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
    """Send a message via Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[email-monitor] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"[email-monitor] Telegram send error: {e}", file=sys.stderr)
        return False


def format_notification(email: dict, importance: int) -> str:
    """Format an email into a Telegram notification."""
    # Importance indicator
    if importance >= 5:
        icon = "🔴"
    elif importance >= 4:
        icon = "🟠"
    else:
        icon = "📩"

    sender = email.get("from", "Unknown")
    # Clean up sender display
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"').strip("'")
        addr = sender.split("<")[1].rstrip(">")
        if name:
            sender = f"{name}"
        else:
            sender = addr
    if len(sender) > 40:
        sender = sender[:37] + "..."

    subject = email.get("subject", "(no subject)")
    if len(subject) > 80:
        subject = subject[:77] + "..."

    snippet = email.get("snippet", "")
    if len(snippet) > 150:
        snippet = snippet[:147] + "..."

    # Escape HTML
    for char, esc in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")]:
        sender = sender.replace(char, esc)
        subject = subject.replace(char, esc)
        snippet = snippet.replace(char, esc)

    lines = [
        f"{icon} <b>New Email</b>",
        f"<b>From:</b> {sender}",
        f"<b>Subject:</b> {subject}",
    ]
    if snippet:
        lines.append(f"<i>{snippet}</i>")
    lines.append(f"\n<code>/email {email['id']}</code>")

    return "\n".join(lines)


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

    for email in emails:
        # Skip already-seen
        if email["id"] in seen_ids:
            continue

        stats["new"] += 1

        # Spam filter
        is_spam, reason = is_spam_or_noise(email)
        if is_spam:
            stats["filtered_spam"] += 1
            if dry_run:
                print(f"  [FILTERED] {email['subject'][:60]} — {reason}")
            continue

        # Score importance
        importance = get_importance_score(email)

        if dry_run:
            print(f"  [NOTIFY importance={importance}] {email['from'][:30]} — {email['subject'][:60]}")
        else:
            # Send notification
            msg = format_notification(email, importance)
            if send_telegram(msg):
                stats["notified"] += 1
            else:
                stats["errors"] += 1

        # Mark as seen regardless
        seen_ids.add(email["id"])

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
