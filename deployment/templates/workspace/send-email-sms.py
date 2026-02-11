#!/usr/bin/env python3
"""
Send Daily Report via Email to SMS Gateway (Verizon: @vtext.com)
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import subprocess
import sys
import os

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
    # Get credentials from environment
    email_from = os.environ.get("GMAIL_USER", "dinisusmc@gmail.com")
    email_token = os.environ.get("GMAIL_TOKEN", "")
    
    if not email_token:
        print("ERROR: GMAIL_TOKEN not set")
        return False
    
    # Create message
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    
    # Add body
    msg.attach(MIMEText(body, "plain"))
    
    try:
        # Connect to Gmail SMTP
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        
        # Login (using app password or token)
        server.login(email_from, email_token)
        
        # Send email
        server.sendmail(email_from, to_email, msg.as_string())
        server.quit()
        
        print(f"✅ Email sent to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False

def main():
    """Main function"""
    report = get_report()
    
    # Verizon SMS gateway
    sms_email = "7323970270@vtext.com"
    subject = "Daily Report • February 8, 2026"
    
    # Send the email
    success = send_email_sms(subject, report, sms_email)
    
    if success:
        print("\nReport delivered via email/SMS!")
    else:
        print("\nFailed to send email/SMS. Report output:")
        print(report)

if __name__ == "__main__":
    main()
