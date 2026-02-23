#!/usr/bin/env python3
"""Google Gmail and Calendar integration for Ashley bot.

Provides functions for:
- Reading emails (inbox, unread, search)
- Sending emails
- Listing calendar events
- Creating calendar events

Requires OAuth2 credentials from Google Cloud Console.
Run with --auth flag to perform initial authentication.
"""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".openclaw" / "google-credentials.json"
TOKEN_FILE = Path.home() / ".openclaw" / "google-token.json"
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "dinisusmc@gmail.com")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


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
    """Build Gmail API service."""
    from googleapiclient.discovery import build
    creds = _get_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def _get_calendar_service():
    """Build Calendar API service."""
    from googleapiclient.discovery import build
    creds = _get_credentials()
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


# --------------- Gmail Functions ---------------

def list_emails(query: str = "", max_results: int = 10) -> list[dict]:
    """List emails matching a query. Default: recent inbox messages."""
    service = _get_gmail_service()
    if not service:
        return []
    try:
        q = query if query else "in:inbox"
        results = service.users().messages().list(
            userId="me", q=q, maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            snippet = detail.get("snippet", "")
            labels = detail.get("labelIds", [])
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": snippet[:200],
                "unread": "UNREAD" in labels,
            })
        return emails
    except Exception as e:
        print(f"Gmail list error: {e}", file=sys.stderr)
        return []


def read_email(message_id: str) -> dict:
    """Read a specific email by ID. Returns full body text."""
    service = _get_gmail_service()
    if not service:
        return {"error": "Not authenticated"}
    try:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body
        body = _extract_body(msg.get("payload", {}))

        # Mark as read
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()

        return {
            "id": message_id,
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "body": body[:3000],
        }
    except Exception as e:
        return {"error": str(e)}


def _extract_body(payload: dict) -> str:
    """Extract plain text body from email payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Fallback: try HTML parts
    for part in parts:
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                # Strip HTML tags crudely
                import re
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                return text

    # Recurse into nested parts
    for part in parts:
        nested = _extract_body(part)
        if nested:
            return nested

    return "(no readable body)"


def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email."""
    service = _get_gmail_service()
    if not service:
        return {"error": "Not authenticated"}
    try:
        message = MIMEText(body)
        message["to"] = to
        message["from"] = GMAIL_ADDRESS
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {"ok": True, "id": sent.get("id", ""), "threadId": sent.get("threadId", "")}
    except Exception as e:
        return {"error": str(e)}


def count_unread() -> int:
    """Count unread emails in inbox."""
    service = _get_gmail_service()
    if not service:
        return -1
    try:
        results = service.users().messages().list(
            userId="me", q="in:inbox is:unread", maxResults=1
        ).execute()
        return results.get("resultSizeEstimate", 0)
    except Exception:
        return -1


# --------------- Calendar Functions ---------------

def list_events(days: int = 7, max_results: int = 20) -> list[dict]:
    """List upcoming calendar events for the next N days."""
    service = _get_calendar_service()
    if not service:
        return []
    try:
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days)
        results = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = []
        for event in results.get("items", []):
            start = event.get("start", {})
            end = event.get("end", {})
            events.append({
                "id": event.get("id", ""),
                "summary": event.get("summary", "(no title)"),
                "start": start.get("dateTime", start.get("date", "")),
                "end": end.get("dateTime", end.get("date", "")),
                "location": event.get("location", ""),
                "description": (event.get("description", "") or "")[:200],
                "status": event.get("status", ""),
            })
        return events
    except Exception as e:
        print(f"Calendar list error: {e}", file=sys.stderr)
        return []


def create_event(
    summary: str,
    start_time: str,
    end_time: str | None = None,
    description: str = "",
    location: str = "",
    all_day: bool = False,
) -> dict:
    """Create a calendar event.

    Args:
        summary: Event title
        start_time: ISO format datetime or date (YYYY-MM-DD for all-day)
        end_time: ISO format datetime or date. If None, defaults to 1 hour after start.
        description: Optional event description
        location: Optional location
        all_day: If True, treat start/end as dates (YYYY-MM-DD)
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Not authenticated"}
    try:
        if all_day:
            start_body = {"date": start_time[:10]}
            if end_time:
                end_body = {"date": end_time[:10]}
            else:
                end_body = {"date": start_time[:10]}
        else:
            # Ensure timezone info
            if "T" not in start_time:
                start_time = f"{start_time}T09:00:00"
            if not start_time.endswith("Z") and "+" not in start_time and "-" not in start_time[10:]:
                start_time += "-05:00"  # EST default

            if end_time:
                if "T" not in end_time:
                    end_time = f"{end_time}T10:00:00"
                if not end_time.endswith("Z") and "+" not in end_time and "-" not in end_time[10:]:
                    end_time += "-05:00"
            else:
                # Default 1 hour
                from datetime import datetime as dt
                try:
                    st = dt.fromisoformat(start_time)
                    et = st + timedelta(hours=1)
                    end_time = et.isoformat()
                except Exception:
                    end_time = start_time

            start_body = {"dateTime": start_time, "timeZone": "America/New_York"}
            end_body = {"dateTime": end_time, "timeZone": "America/New_York"}

        event_body = {
            "summary": summary,
            "start": start_body,
            "end": end_body,
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        event = service.events().insert(
            calendarId="primary", body=event_body
        ).execute()
        return {
            "ok": True,
            "id": event.get("id", ""),
            "summary": event.get("summary", ""),
            "start": event.get("start", {}),
            "htmlLink": event.get("htmlLink", ""),
        }
    except Exception as e:
        return {"error": str(e)}


def delete_event(event_id: str) -> dict:
    """Delete a calendar event by ID."""
    service = _get_calendar_service()
    if not service:
        return {"error": "Not authenticated"}
    try:
        service.events().delete(
            calendarId="primary", eventId=event_id
        ).execute()
        return {"ok": True, "deleted": event_id}
    except Exception as e:
        return {"error": str(e)}


def today_schedule() -> str:
    """Get a formatted summary of today's events."""
    events = list_events(days=1, max_results=20)
    if not events:
        return "No events scheduled for today."
    lines = ["Today's schedule:"]
    for ev in events:
        start = ev["start"]
        if "T" in start:
            # Extract time portion
            try:
                dt = datetime.fromisoformat(start)
                time_str = dt.strftime("%-I:%M %p")
            except Exception:
                time_str = start
        else:
            time_str = "All day"
        loc = f" @ {ev['location']}" if ev.get("location") else ""
        lines.append(f"  {time_str} — {ev['summary']}{loc}")
    return "\n".join(lines)


# --------------- Auth Flow ---------------

def run_auth_flow():
    """Run the initial OAuth2 authentication flow using OOB/console method for headless servers."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS_FILE.exists():
        print(f"ERROR: Credentials file not found at {CREDENTIALS_FILE}")
        print("Download the OAuth client JSON from Google Cloud Console and save it there.")
        return False

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)

    # Use redirect to localhost with a specific port, but print the URL for manual use
    flow.redirect_uri = "http://localhost:18820"

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    print("\n" + "=" * 60)
    print("Open this URL in your browser to authorize:")
    print()
    print(auth_url)
    print()
    print("After authorizing, you'll be redirected to a localhost URL.")
    print("It will fail to load — that's OK!")
    print("Copy the FULL URL from your browser's address bar and paste it here.")
    print("It will look like: http://localhost:18820/?state=...&code=...&scope=...")
    print("=" * 60)

    redirect_response = input("\nPaste the full redirect URL here: ").strip()

    flow.fetch_token(authorization_response=redirect_response)
    creds = flow.credentials
    TOKEN_FILE.write_text(creds.to_json())
    print(f"\n✅ Authentication successful! Token saved to {TOKEN_FILE}")
    return True


# --------------- CLI ---------------

def _format_email_list(emails: list[dict]) -> str:
    if not emails:
        return "No emails found."
    lines = []
    for e in emails:
        unread = "●" if e.get("unread") else " "
        subj = e.get("subject", "(no subject)")[:60]
        sender = e.get("from", "")
        # Shorten sender
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')
        if len(sender) > 25:
            sender = sender[:22] + "..."
        lines.append(f"  {unread} {sender} — {subj}")
        lines.append(f"    ID: {e['id']}")
    return "\n".join(lines)


def _format_event_list(events: list[dict]) -> str:
    if not events:
        return "No upcoming events."
    lines = []
    current_date = ""
    for ev in events:
        start = ev["start"]
        if "T" in start:
            try:
                dt = datetime.fromisoformat(start)
                date_str = dt.strftime("%a %b %-d")
                time_str = dt.strftime("%-I:%M %p")
            except Exception:
                date_str = start[:10]
                time_str = start
        else:
            date_str = start
            time_str = "All day"

        if date_str != current_date:
            lines.append(f"\n📅 {date_str}")
            current_date = date_str

        loc = f" @ {ev['location']}" if ev.get("location") else ""
        lines.append(f"  {time_str} — {ev['summary']}{loc}")
        if ev.get("id"):
            lines.append(f"    ID: {ev['id']}")
    return "\n".join(lines).strip()


if __name__ == "__main__":
    if "--auth" in sys.argv:
        run_auth_flow()
    elif "--test" in sys.argv:
        creds = _get_credentials()
        if creds:
            print("✅ Google authentication is working")
            unread = count_unread()
            print(f"📧 Unread emails: {unread}")
            events = list_events(days=1)
            print(f"📅 Today's events: {len(events)}")
        else:
            print("❌ Not authenticated. Run with --auth first.")
    else:
        print("Usage: python3 google-services.py --auth | --test")
