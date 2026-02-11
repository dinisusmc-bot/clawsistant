#!/usr/bin/env python3
"""
Daily Report Generator with Email/SMS Delivery
Runs the weather/sports report and sends via email to Verizon SMS
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import subprocess
import sys
import os
from datetime import datetime

def get_report():
    """Generate the daily report using the Python script"""
    try:
        result = subprocess.run(
            [sys.executable, "/home/bot/.openclaw/workspace/daily-report-generator.py"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout if result.returncode == 0 else "Error generating report"
    except Exception as e:
        return f"Error: {str(e)}"

def send_email_sms(subject, body, to_email):
    """Send email via Gmail SMTP"""
    email_from = os.environ.get("GMAIL_USER", "dinisusmc@gmail.com")
    email_token = os.environ.get("GMAIL_TOKEN", "")
    
    if not email_token:
        print("ERROR: GMAIL_TOKEN not set")
        return False
    
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(email_from, email_token)
        server.sendmail(email_from, to_email, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False

def main():
    report = get_report()
    sms_email = "7323970270@vtext.com"
    subject = f"Daily Report • {datetime.now().strftime('%B %-d, %Y')}"
    
    success = send_email_sms(subject, report, sms_email)
    
    if success:
        print("\nReport delivered via email/SMS!")
        # Also output to Discord
        print(report)
    else:
        print("\nFailed to send email/SMS. Outputting to Discord:")
        print(report)

if __name__ == "__main__":
    main()
