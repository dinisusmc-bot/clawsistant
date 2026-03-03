#!/usr/bin/env python3
"""Microsoft 365 Email Monitor with Telegram notifications.

Periodically checks the MS365 inbox for new unread emails,
filters out spam/noise, and sends Telegram notifications for
important messages. Does NOT use the OpenClaw Telegram plugin —
sends notifications directly via Telegram Bot API.

Usage:
    python3 ms365-email-monitor.py           # Normal run
    python3 ms365-email-monitor.py --dry      # Dry run (no notifications)
    python3 ms365-email-monitor.py --daemon   # Run continuously (every 5 min)
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Import microsoft_services ──────────────────────────────────────────────

WORKSPACE_DIR = Path.home() / ".openclaw" / "workspace"
sys.path.insert(0, str(WORKSPACE_DIR))

import microsoft_services as ms  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────

STATE_FILE = Path.home() / ".openclaw" / "email-monitor-state.json"
MAX_SEEN_IDS = 500
CHECK_LIMIT = 25                  # Messages per check
POLL_INTERVAL_SEC = 5 * 60        # 5 minutes when running as daemon

# Telegram (direct — NOT through OpenClaw plugin)
TELEGRAM_BOT_TOKEN = "7202673884:AAGjqUoBRfNNN8sda4Fn9twdJmdUbdee9fU"
TELEGRAM_CHAT_ID = "8302484666"


# ── Spam / Noise Filters ──────────────────────────────────────────────────

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
    r"@mg\.\w+\.com",
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

# MS365 folder names that are noise (junk mail folder)
NOISE_FOLDERS = {"junkemail", "deleteditems", "clutter"}


# ── State Management ───────────────────────────────────────────────────────

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


# ── Filtering ──────────────────────────────────────────────────────────────

def is_spam_or_noise(email: dict) -> tuple:
    """Check if an email is spam/noise. Returns (is_spam, reason)."""
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()

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
    """Score email importance 1–5. Higher = more important."""
    score = 3  # Default: normal
    sender = email.get("from", "").lower()
    subject = email.get("subject", "").lower()
    from_name = email.get("from_name", "")

    # MS Graph importance flag
    if email.get("importance") == "high":
        score += 2
    elif email.get("importance") == "low":
        score -= 1

    # Has attachments = slightly more likely to be real
    if email.get("has_attachments"):
        score += 1

    # Urgent language in subject
    if re.search(r"(urgent|asap|critical|emergency|important|action required|immediate)", subject, re.IGNORECASE):
        score += 1

    # Looks like a real person (has a proper name)
    if from_name and re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+', from_name):
        score += 1

    return min(max(score, 1), 5)


# ── Telegram Notifications ─────────────────────────────────────────────────

def _escape_md(text: str) -> str:
    """Escape special chars for Telegram Markdown."""
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram(text: str, parse_mode: str = "") -> bool:
    """Send a message via Telegram Bot API directly."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        # Retry without parse_mode if Markdown failed
        if parse_mode:
            return send_telegram(text.replace("\\", ""), parse_mode="")
        print(f"[email-monitor] Telegram send error: {e}", file=sys.stderr)
        return False


def _clean_sender(sender: str, from_name: str = "") -> str:
    """Extract a clean display name."""
    if from_name:
        return from_name
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"').strip("'")
        addr = sender.split("<")[1].rstrip(">")
        return name if name else addr
    return sender


def format_email_notification(emails: list) -> str:
    """Format email list into a Telegram-friendly message (plain text)."""
    count = len(emails)
    lines = [f"📬 {count} new email{'s' if count != 1 else ''}\n"]

    for em in emails:
        importance = em.get("_importance", 3)
        if importance >= 5:
            badge = "🔴 URGENT"
        elif importance >= 4:
            badge = "🟠 Important"
        else:
            badge = ""

        sender = _clean_sender(em.get("from", "unknown"), em.get("from_name", ""))
        subject = em.get("subject", "(no subject)")
        preview = em.get("preview", "")[:120]

        line = f"{'[' + badge + '] ' if badge else ''}From: {sender}\n  {subject}"
        if preview:
            line += f"\n  {preview}"
        lines.append(line)
        lines.append("")

    return "\n".join(lines).strip()


# ── Fetch from MS365 ──────────────────────────────────────────────────────

def fetch_recent_unread(limit: int = CHECK_LIMIT) -> list:
    """Fetch recent unread inbox emails via microsoft_services."""
    try:
        emails = ms.list_emails(folder="inbox", count=limit, filter_unread=True)
        return emails
    except Exception as e:
        print(f"[email-monitor] MS365 fetch error: {e}", file=sys.stderr)
        return []


# ── Main Check ─────────────────────────────────────────────────────────────

def run_check(dry_run: bool = False) -> dict:
    """Run one email check cycle. Returns stats."""
    state = load_state()
    seen_ids = set(state.get("seen_ids", []))

    emails = fetch_recent_unread()
    stats = {
        "total_unread": len(emails),
        "new": 0,
        "filtered_spam": 0,
        "notified": 0,
        "errors": 0,
    }

    important_emails = []

    for email in emails:
        msg_id = email.get("id", "")
        if not msg_id:
            continue

        # Skip already-seen
        if msg_id in seen_ids:
            continue

        stats["new"] += 1

        # Always mark as seen (dedup)
        seen_ids.add(msg_id)

        # Spam filter
        is_spam, reason = is_spam_or_noise(email)
        if is_spam:
            stats["filtered_spam"] += 1
            if dry_run:
                print(f"  [FILTERED] {email.get('subject', '')[:60]} — {reason}")
            continue

        # Score importance
        importance = get_importance_score(email)
        email["_importance"] = importance

        if dry_run:
            sender = _clean_sender(email.get("from", ""), email.get("from_name", ""))
            print(f"  [NOTIFY importance={importance}] {sender[:30]} — {email.get('subject', '')[:60]}")

        important_emails.append(email)

    # Send notifications
    if important_emails and not dry_run:
        msg = format_email_notification(important_emails)
        if send_telegram(msg):
            stats["notified"] = len(important_emails)
            print(f"[email-monitor] Sent Telegram notification for {len(important_emails)} email(s)")
        else:
            stats["errors"] = len(important_emails)
            print("[email-monitor] Failed to send Telegram notification", file=sys.stderr)

    # Save state
    state["seen_ids"] = list(seen_ids)
    if not dry_run:
        save_state(state)

    return stats


def run_daemon():
    """Run continuously, checking every POLL_INTERVAL_SEC."""
    print(f"[email-monitor] Starting daemon (poll every {POLL_INTERVAL_SEC}s)")
    while True:
        try:
            stats = run_check()
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[email-monitor {ts}] unread={stats.get('total_unread', 0)} "
                  f"new={stats.get('new', 0)} "
                  f"filtered={stats.get('filtered_spam', 0)} "
                  f"notified={stats.get('notified', 0)} "
                  f"errors={stats.get('errors', 0)}")
        except Exception as e:
            print(f"[email-monitor] Error in check cycle: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    dry_run = "--dry" in sys.argv
    daemon = "--daemon" in sys.argv

    if dry_run:
        print("[email-monitor] DRY RUN — no notifications will be sent\n")
        stats = run_check(dry_run=True)
    elif daemon:
        run_daemon()
    else:
        stats = run_check()

    if not daemon:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[email-monitor {ts}] unread={stats.get('total_unread', 0)} "
              f"new={stats.get('new', 0)} "
              f"filtered={stats.get('filtered_spam', 0)} "
              f"notified={stats.get('notified', 0)} "
              f"errors={stats.get('errors', 0)}")
