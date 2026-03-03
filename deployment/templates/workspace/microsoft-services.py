#!/usr/bin/env python3
"""Microsoft 365 Email and Calendar integration via Microsoft Graph API.

Provides functions for:
- Reading emails (inbox, unread, search)
- Sending emails (plain text and HTML)
- Listing calendar events
- Creating calendar events
- Deleting calendar events

Requires an Azure App Registration with delegated permissions:
  - Mail.Read, Mail.Send, Mail.ReadWrite
  - Calendars.Read, Calendars.ReadWrite

Run with --auth flag to perform initial interactive authentication.
Run with --test flag to verify connectivity.
"""

import base64
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".openclaw"
CREDENTIALS_FILE = CONFIG_DIR / "microsoft-credentials.json"
TOKEN_CACHE_FILE = CONFIG_DIR / "microsoft-token-cache.json"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Scopes for delegated (user) access
SCOPES = [
    "Mail.Read",
    "Mail.Send",
    "Mail.ReadWrite",
    "Calendars.Read",
    "Calendars.ReadWrite",
    "User.Read",
]

DEFAULT_TIMEZONE = "America/New_York"


# ── Authentication ──────────────────────────────────────────────────────────

USER_EMAIL = "nick@sempersolved.com"


def _load_credentials():
    """Load Azure App Registration credentials from config file."""
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"Microsoft credentials not found at {CREDENTIALS_FILE}.\n"
            "Create an Azure App Registration and save credentials:\n"
            f'  {{"client_id": "YOUR_CLIENT_ID", "tenant_id": "YOUR_TENANT_ID", "client_secret": "YOUR_SECRET"}}'
        )
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)
    required = ["client_id", "tenant_id", "client_secret"]
    for key in required:
        if key not in creds:
            raise ValueError(f"Missing '{key}' in {CREDENTIALS_FILE}")
    return creds


def _load_token_data():
    """Load cached token data from disk."""
    if TOKEN_CACHE_FILE.exists():
        try:
            data = json.loads(TOKEN_CACHE_FILE.read_text())
            if data.get("access_token") and data.get("_expires_at", 0) > __import__("time").time() + 300:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_token_data(data):
    """Save token data to disk cache."""
    import time as _time
    # Calculate absolute expiry time
    if "expires_in" in data and "_expires_at" not in data:
        data["_expires_at"] = _time.time() + data["expires_in"]
    TOKEN_CACHE_FILE.write_text(json.dumps(data, indent=2))
    TOKEN_CACHE_FILE.chmod(0o600)


def _get_token():
    """Acquire an access token using client credentials flow (app-only)."""
    # Try cached token first
    cached = _load_token_data()
    if cached and "access_token" in cached:
        return cached["access_token"]

    # Get fresh token via client credentials
    creds = _load_credentials()
    resp = requests.post(
        f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token",
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token acquisition failed: {resp.text}")
    
    token_data = resp.json()
    token_data["_auth_mode"] = "client_credentials"
    token_data["_user_email"] = USER_EMAIL
    _save_token_data(token_data)
    return token_data["access_token"]


def _headers():
    """Get authorization headers for Graph API calls."""
    token = _get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _user_path():
    """Return the Graph API user path for app-only access."""
    return f"/users/{USER_EMAIL}"


def _graph_get(endpoint, params=None):
    """Make a GET request to Microsoft Graph."""
    url = f"{GRAPH_BASE}{endpoint}"
    resp = requests.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    return resp.json()


def _graph_post(endpoint, data):
    """Make a POST request to Microsoft Graph."""
    url = f"{GRAPH_BASE}{endpoint}"
    resp = requests.post(url, headers=_headers(), json=data)
    resp.raise_for_status()
    return resp


def _graph_delete(endpoint):
    """Make a DELETE request to Microsoft Graph."""
    url = f"{GRAPH_BASE}{endpoint}"
    resp = requests.delete(url, headers=_headers())
    resp.raise_for_status()
    return resp


def _graph_patch(endpoint, data):
    """Make a PATCH request to Microsoft Graph."""
    url = f"{GRAPH_BASE}{endpoint}"
    resp = requests.patch(url, headers=_headers(), json=data)
    resp.raise_for_status()
    return resp


# ── Email Functions ─────────────────────────────────────────────────────────

def list_emails(folder="inbox", count=10, filter_unread=False):
    """List emails from a folder.
    
    Args:
        folder: Mail folder (inbox, sentitems, drafts, deleteditems, junkemail)
        count: Number of messages to return
        filter_unread: If True, only return unread messages
    
    Returns:
        List of email dicts with id, subject, from, receivedDateTime, isRead, preview
    """
    params = {
        "$top": count,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview,hasAttachments",
    }
    if filter_unread:
        params["$filter"] = "isRead eq false"

    data = _graph_get(f"{_user_path()}/mailFolders/{folder}/messages", params=params)
    messages = []
    for msg in data.get("value", []):
        messages.append({
            "id": msg["id"],
            "subject": msg.get("subject", "(no subject)"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"),
            "from_name": msg.get("from", {}).get("emailAddress", {}).get("name", ""),
            "date": msg.get("receivedDateTime", ""),
            "is_read": msg.get("isRead", False),
            "preview": msg.get("bodyPreview", "")[:200],
            "has_attachments": msg.get("hasAttachments", False),
        })
    return messages


def read_email(message_id):
    """Read a specific email by ID.
    
    Returns:
        Dict with full email content including body
    """
    params = {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,isRead",
    }
    msg = _graph_get(f"{_user_path()}/messages/{message_id}", params=params)
    
    # Mark as read
    try:
        _graph_patch(f"{_user_path()}/messages/{message_id}", {"isRead": True})
    except Exception:
        pass  # Non-critical
    
    return {
        "id": msg["id"],
        "subject": msg.get("subject", "(no subject)"),
        "from": msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"),
        "from_name": msg.get("from", {}).get("emailAddress", {}).get("name", ""),
        "to": [r["emailAddress"]["address"] for r in msg.get("toRecipients", [])],
        "cc": [r["emailAddress"]["address"] for r in msg.get("ccRecipients", [])],
        "date": msg.get("receivedDateTime", ""),
        "body": msg.get("body", {}).get("content", ""),
        "body_type": msg.get("body", {}).get("contentType", "text"),
        "has_attachments": msg.get("hasAttachments", False),
    }


def search_emails(query, count=10):
    """Search emails using Microsoft Graph $search.
    
    Args:
        query: Search query string (searches subject, body, from, etc.)
        count: Max results
    
    Returns:
        List of email dicts
    """
    params = {
        "$search": f'"{query}"',
        "$top": count,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
    }
    data = _graph_get(f"{_user_path()}/messages", params=params)
    messages = []
    for msg in data.get("value", []):
        messages.append({
            "id": msg["id"],
            "subject": msg.get("subject", "(no subject)"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"),
            "date": msg.get("receivedDateTime", ""),
            "preview": msg.get("bodyPreview", "")[:200],
        })
    return messages


def count_unread():
    """Count unread emails in inbox.
    
    Returns:
        Integer count of unread messages
    """
    data = _graph_get(f"{_user_path()}/mailFolders/inbox", params={"$select": "unreadItemCount"})
    return data.get("unreadItemCount", 0)


def send_email(to, subject, body, cc=None, bcc=None):
    """Send a plain text email.
    
    Args:
        to: Recipient email address (string or list)
        subject: Email subject
        body: Plain text body
        cc: CC recipients (string or list, optional)
        bcc: BCC recipients (string or list, optional)
    
    Returns:
        True on success
    """
    return _send_email(to, subject, body, "text", cc=cc, bcc=bcc)


def send_html_email(to, subject, html_body, cc=None, bcc=None):
    """Send an HTML email.
    
    Args:
        to: Recipient email address (string or list)
        subject: Email subject
        html_body: HTML body content
        cc: CC recipients (string or list, optional)
        bcc: BCC recipients (string or list, optional)
    
    Returns:
        True on success
    """
    return _send_email(to, subject, html_body, "html", cc=cc, bcc=bcc)


def _format_recipients(addresses):
    """Convert email addresses to Graph API recipients format."""
    if isinstance(addresses, str):
        addresses = [addresses]
    return [{"emailAddress": {"address": addr.strip()}} for addr in addresses if addr.strip()]


def _send_email(to, subject, body, content_type="text", cc=None, bcc=None):
    """Internal: Send an email via Graph API."""
    message = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML" if content_type == "html" else "Text",
                "content": body,
            },
            "toRecipients": _format_recipients(to),
        },
        "saveToSentItems": True,
    }
    if cc:
        message["message"]["ccRecipients"] = _format_recipients(cc)
    if bcc:
        message["message"]["bccRecipients"] = _format_recipients(bcc)

    _graph_post(f"{_user_path()}/sendMail", message)
    return True


def reply_to_email(message_id, body, reply_all=False):
    """Reply to an email.
    
    Args:
        message_id: ID of the message to reply to
        body: Reply body (HTML)
        reply_all: If True, reply to all recipients
    """
    endpoint = f"{_user_path()}/messages/{message_id}/replyAll" if reply_all else f"{_user_path()}/messages/{message_id}/reply"
    _graph_post(endpoint, {
        "comment": body,
    })
    return True


# ── Calendar Functions ──────────────────────────────────────────────────────

def list_events(days=7, calendar_id=None):
    """List upcoming calendar events.
    
    Args:
        days: Number of days ahead to look
        calendar_id: Specific calendar ID (default: primary)
    
    Returns:
        List of event dicts
    """
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    
    cal_path = f"{_user_path()}/calendars/{calendar_id}" if calendar_id else f"{_user_path()}/calendar"
    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end.isoformat(),
        "$orderby": "start/dateTime",
        "$top": 50,
        "$select": "id,subject,start,end,location,organizer,isAllDay,bodyPreview",
    }
    
    data = _graph_get(f"{cal_path}/calendarView", params=params)
    events = []
    for ev in data.get("value", []):
        events.append({
            "id": ev["id"],
            "subject": ev.get("subject", "(no title)"),
            "start": ev.get("start", {}).get("dateTime", ""),
            "start_tz": ev.get("start", {}).get("timeZone", "UTC"),
            "end": ev.get("end", {}).get("dateTime", ""),
            "end_tz": ev.get("end", {}).get("timeZone", "UTC"),
            "location": ev.get("location", {}).get("displayName", ""),
            "is_all_day": ev.get("isAllDay", False),
            "organizer": ev.get("organizer", {}).get("emailAddress", {}).get("address", ""),
            "preview": ev.get("bodyPreview", "")[:200],
        })
    return events


def today_schedule():
    """Get today's calendar events.
    
    Returns:
        List of event dicts for today
    """
    return list_events(days=1)


def create_event(subject, start, end, location=None, body=None, attendees=None, 
                 is_all_day=False, timezone_str=None):
    """Create a calendar event.
    
    Args:
        subject: Event title
        start: Start time as ISO string (e.g., "2026-03-03T14:00:00")
        end: End time as ISO string
        location: Location string (optional)
        body: Event description (optional)
        attendees: List of email addresses (optional)
        is_all_day: Whether this is an all-day event
        timezone_str: Timezone (default: America/New_York)
    
    Returns:
        Created event dict with id
    """
    tz = timezone_str or DEFAULT_TIMEZONE
    
    event = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
        "isAllDay": is_all_day,
    }
    if location:
        event["location"] = {"displayName": location}
    if body:
        event["body"] = {"contentType": "HTML", "content": body}
    if attendees:
        if isinstance(attendees, str):
            attendees = [attendees]
        event["attendees"] = [
            {
                "emailAddress": {"address": addr.strip()},
                "type": "required",
            }
            for addr in attendees
        ]
    
    resp = _graph_post(f"{_user_path()}/events", event)
    result = resp.json()
    return {
        "id": result.get("id"),
        "subject": result.get("subject"),
        "start": result.get("start", {}).get("dateTime"),
        "end": result.get("end", {}).get("dateTime"),
        "link": result.get("webLink", ""),
    }


def delete_event(event_id):
    """Delete a calendar event.
    
    Args:
        event_id: Event ID from list_events or create_event
    
    Returns:
        True on success
    """
    _graph_delete(f"{_user_path()}/events/{event_id}")
    return True


def get_me():
    """Get the authenticated user's profile info.
    
    Returns:
        Dict with displayName, mail, userPrincipalName
    """
    return _graph_get(f"{_user_path()}", params={"$select": "displayName,mail,userPrincipalName"})


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_json(data):
    print(json.dumps(data, indent=2, default=str))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Microsoft 365 Email & Calendar CLI")
    parser.add_argument("--auth", action="store_true", help="Authenticate with Microsoft")
    parser.add_argument("--test", action="store_true", help="Test connectivity")
    parser.add_argument("--setup", action="store_true", help="Interactive setup wizard")
    
    sub = parser.add_subparsers(dest="command")
    
    # Email commands
    p_list = sub.add_parser("list", help="List emails")
    p_list.add_argument("--folder", default="inbox")
    p_list.add_argument("--count", type=int, default=10)
    p_list.add_argument("--unread", action="store_true")
    
    p_read = sub.add_parser("read", help="Read an email")
    p_read.add_argument("message_id")
    
    p_search = sub.add_parser("search", help="Search emails")
    p_search.add_argument("query")
    p_search.add_argument("--count", type=int, default=10)
    
    p_unread = sub.add_parser("unread", help="Count unread emails")
    
    p_send = sub.add_parser("send", help="Send an email")
    p_send.add_argument("to")
    p_send.add_argument("subject")
    p_send.add_argument("body")
    p_send.add_argument("--html", action="store_true")
    
    # Calendar commands
    p_events = sub.add_parser("events", help="List calendar events")
    p_events.add_argument("--days", type=int, default=7)
    
    p_today = sub.add_parser("today", help="Today's schedule")
    
    p_create = sub.add_parser("create-event", help="Create calendar event")
    p_create.add_argument("subject")
    p_create.add_argument("start", help="ISO datetime")
    p_create.add_argument("end", help="ISO datetime")
    p_create.add_argument("--location", default=None)
    p_create.add_argument("--body", default=None)
    
    p_delete = sub.add_parser("delete-event", help="Delete calendar event")
    p_delete.add_argument("event_id")
    
    args = parser.parse_args()
    
    if args.setup:
        _interactive_setup()
        return
    
    if args.auth or args.test:
        print("Authenticating with Microsoft 365...")
        try:
            me = get_me()
            print(f"✓ Signed in as: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})")
            if args.test:
                unread = count_unread()
                print(f"✓ Unread emails: {unread}")
                events = today_schedule()
                print(f"✓ Today's events: {len(events)}")
        except Exception as e:
            print(f"✗ Error: {e}")
            sys.exit(1)
        return
    
    if args.command == "list":
        _print_json(list_emails(args.folder, args.count, args.unread))
    elif args.command == "read":
        _print_json(read_email(args.message_id))
    elif args.command == "search":
        _print_json(search_emails(args.query, args.count))
    elif args.command == "unread":
        print(count_unread())
    elif args.command == "send":
        fn = send_html_email if args.html else send_email
        fn(args.to, args.subject, args.body)
        print("✓ Email sent")
    elif args.command == "events":
        _print_json(list_events(args.days))
    elif args.command == "today":
        _print_json(today_schedule())
    elif args.command == "create-event":
        result = create_event(args.subject, args.start, args.end, 
                             location=args.location, body=args.body)
        _print_json(result)
    elif args.command == "delete-event":
        delete_event(args.event_id)
        print("✓ Event deleted")
    else:
        parser.print_help()


def _interactive_setup():
    """Interactive setup wizard for Microsoft 365 credentials."""
    print("\n" + "="*60)
    print("  Microsoft 365 Setup for OpenClaw")
    print("="*60)
    print("""
You need an Azure App Registration. Here's how:

1. Go to https://portal.azure.com → Azure Active Directory → App registrations
2. Click "New registration"
   - Name: "OpenClaw Bot"
   - Supported account types: "Accounts in this organizational directory only"
   - Redirect URI: select "Public client/native (mobile & desktop)"
     set to: https://login.microsoftonline.com/common/oauth2/nativeclient
3. After creating, note the:
   - Application (client) ID
   - Directory (tenant) ID
4. Go to "Certificates & secrets" → "New client secret"
   - Description: "openclaw"
   - Note the secret value (shown only once!)
5. Go to "API permissions" → "Add a permission" → "Microsoft Graph"
   - Add Delegated permissions:
     • Mail.Read, Mail.Send, Mail.ReadWrite
     • Calendars.Read, Calendars.ReadWrite
     • User.Read
     • offline_access
   - Click "Grant admin consent" (if you're an admin)
    """)
    
    client_id = input("Enter Application (client) ID: ").strip()
    tenant_id = input("Enter Directory (tenant) ID: ").strip()
    client_secret = input("Enter Client Secret value (press Enter to skip): ").strip()
    
    if not client_id or not tenant_id:
        print("✗ Client ID and Tenant ID are required.")
        sys.exit(1)
    
    creds = {
        "client_id": client_id,
        "tenant_id": tenant_id,
    }
    if client_secret:
        creds["client_secret"] = client_secret
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    CREDENTIALS_FILE.chmod(0o600)
    print(f"\n✓ Credentials saved to {CREDENTIALS_FILE}")
    
    print("\nNow authenticating...")
    try:
        me = get_me()
        print(f"✓ Signed in as: {me.get('displayName')} ({me.get('mail') or me.get('userPrincipalName')})")
    except Exception as e:
        print(f"\n⚠ Authentication will happen on first use (device code flow).")
        print(f"  Run: python3 {__file__} --auth")


if __name__ == "__main__":
    main()
