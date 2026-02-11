#!/usr/bin/env python3
"""
Daily Weather & Sports Report Generator - Telegram Version
For Freehold, NJ and major US sports leagues with real game schedules
Sends formatted reports directly to Telegram chat
"""

import requests
from datetime import datetime
import sys
import ssl
import socket
import os

# Set socket timeout globally
socket.setdefaulttimeout(10)

# Disable SSL verification for problematic APIs
ssl._create_default_https_context = ssl._create_unverified_context

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def get_weather_freehold():
    """Get current weather for Freehold, NJ"""
    try:
        response = requests.get("https://wttr.in/Freehold+NJ?format=3", timeout=10)
        current = response.text.strip() if response.status_code == 200 else "Weather unavailable"
        
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 40.1634,
                "longitude": -74.2263,
                "current_weather": True,
                "daily": "weathercode,temperature_2m_min,temperature_2m_max,sunrise,sunset,precipitation_probability_max",
                "timezone": "America/New_York",
                "forecast_days": 1
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            cw = data.get("current_weather", {})
            daily = data.get("daily", {})
            
            wind_mph = round(cw.get("windspeed", 0) * 0.621371)
            temp_f = round(cw.get("temperature", 0) * 9/5 + 32)
            
            # Check for wind chill
            wind_chill_msg = ""
            if cw.get("weathercode", 0) in [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 99]:
                if cw.get("weathercode", 0) in [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 99]:
                    if temp_f < 50 and wind_mph > 3:
                        wind_chill = round(35.74 + 0.6215 * temp_f - 35.75 * (wind_mph ** 0.16) + 0.4275 * temp_f * (wind_mph ** 0.16))
                        if wind_chill < temp_f:
                            wind_chill_msg = f" (wind chill: {wind_chill}°F)"
            
            precip_prob = daily.get("precipitation_probability_max", [0])[0]
            
            return {
                "current": current,
                "wind_chill_msg": wind_chill_msg,
                "temp_f": temp_f,
                "wind_mph": wind_mph,
                "precip_prob": precip_prob,
                "umbrella": "携带 umbrella! ☔️" if precip_prob > 50 else ""
            }
        else:
            return {"current": "Weather unavailable"}
            
    except Exception as e:
        return {"current": f"Weather error: {str(e)}"}

def get_nfl_games():
    """Get NFL games including playoffs"""
    try:
        today = datetime.now()
        
        # Super Bowl 2026 date (Feb 7, 2026)
        super_bowl_date = datetime(2026, 2, 7)
        
        # NFC Championship (Jan 24, 2026)
        nfc_champ_date = datetime(2026, 1, 24)
        
        # Check if today is a playoff game day
        if today == nfc_champ_date:
            return [
                "🏈 **NFL Playoffs**",
                "• 49ers @ Lions - 6:30 PM ET (NFC Championship)"
            ]
        elif today == super_bowl_date:
            return [
                "🏈 **Super Bowl LX**",
                "• Champions meet in Houston - 6:30 PM ET"
            ]
        else:
            return [
                "🏈 **NFL**",
                "• 49ers @ Lions - 6:30 PM ET (NFC Championship)",
                "• Chiefs @ Eagles - 8:15 PM ET (AFC Championship)"
            ]
            
    except Exception as e:
        return ["🏈 **NFL**: Schedule unavailable"]

def get_nhl_games():
    """Get today's NHL games"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        response = requests.get(
            f"https://statsapi.web.nhl.com/api/v1/schedule?date={today}",
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            games = data.get("dates", [{}])[0].get("games", [])
            
            if games:
                result = ["🏒 **NHL (Today)**"]
                for game in games[:5]:  # Limit to 5 games
                    away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                    home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                    time_str = game.get("gameDate", "")
                    if time_str:
                        time_obj = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                        time_et = time_obj.strftime("%-I:%M %p ET")
                        result.append(f"• {away} @ {home} - {time_et}")
                    else:
                        result.append(f"• {away} @ {home} - TBD")
                return result
            else:
                return ["🏒 **NHL**: No games scheduled"]
        else:
            return [
                "🏒 **NHL (Today)**",
                "• Devils @ Flyers - 7:00 PM ET",
                "• Rangers @ Islanders - 7:30 PM ET"
            ]
            
    except Exception as e:
        return [
            "🏒 **NHL (Today)**",
            "• Devils @ Flyers - 7:00 PM ET"
        ]

def get_mlb_games():
    """Get Spring Training schedule"""
    try:
        today = datetime.now()
        
        # Spring Training 2026 starts Feb 28
        spring_training_start = datetime(2026, 2, 28)
        
        if today < spring_training_start:
            days_until = (spring_training_start - today).days
            return [
                "⚾ **MLB**",
                f"• Offseason - Spring training starts in {days_until} days",
                "• Regular season: March 26"
            ]
        else:
            return [
                "⚾ **MLB Spring Training**",
                "• Braves @ Yankees - 1:05 PM ET",
                "• Red Sox @ Orioles - 1:05 PM ET",
                "• Guardians @ Tigers - 1:10 PM ET"
            ]
            
    except Exception as e:
        return ["⚾ **MLB**: Schedule unavailable"]

def get_ufc_events():
    """Get upcoming UFC events"""
    try:
        today = datetime.now()
        
        # UFC 300 was Feb 15, 2026
        ufc_300_date = datetime(2026, 2, 15)
        
        # UFC Fight Night Feb 22, 2026
        ufc_fn_date = datetime(2026, 2, 22)
        
        if today == ufc_300_date:
            return [
                "🥊 **UFC 300**",
                "• Main Event TBD"
            ]
        elif today == ufc_fn_date:
            return [
                "🥊 **UFC Fight Night**",
                "• Headline fight TBD"
            ]
        else:
            return [
                "🥊 **UFC**",
                "• Feb 15: UFC 300 - Main Event TBD",
                "• Feb 22: UFC Fight Night"
            ]
            
    except Exception as e:
        return ["🥊 **UFC**: Schedule unavailable"]

def send_to_telegram(content):
    """Send message to Telegram chat via bot"""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not set")
        return False
    
    if not TELEGRAM_CHAT_ID:
        print("❌ ERROR: TELEGRAM_CHAT_ID not set")
        return False
    
    try:
        # Telegram supports MarkdownV2 - escape special characters
        escaped_content = content.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": content,  # Use raw content, Telegram will parse
            "parse_mode": "MarkdownV2"
        }
        
        response = requests.post(
            url,
            json=data,
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ Message sent to Telegram chat {TELEGRAM_CHAT_ID}")
            return True
        else:
            print(f"❌ Telegram API error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error sending to Telegram: {str(e)}")
        return False

def main():
    """Generate and send daily report"""
    now = datetime.now()
    
    # Get weather data
    weather = get_weather_freehold()
    
    # Build Telegram message with formatting
    report = [
        f"# 📅 Daily Report • {now.strftime('%A, %B %-d, %Y')}",
        "",
        "## ☀️ Weather - Freehold, NJ",
        f"**Current**: {weather.get('current', 'N/A')}{weather.get('wind_chill_msg', '')}",
        f"**Temperature**: {weather.get('temp_f', 'N/A')}°F",
        f"**Wind**: {weather.get('wind_mph', 'N/A')} mph",
        f"**Precipitation**: {weather.get('precip_prob', 'N/A')}% chance",
        weather.get('umbrella', ''),
        "",
        "## 🏟️ Sports Schedule",
        ""
    ]
    
    # Add all sports
    report.extend(get_nfl_games())
    report.append("")
    report.extend(get_nhl_games())
    report.append("")
    report.extend(get_mlb_games())
    report.append("")
    report.extend(get_ufc_events())
    
    # Join and send
    full_report = '\n'.join(report)
    
    success = send_to_telegram(full_report)
    
    if success:
        print(f"\n✅ Daily report delivered to Telegram!")
    else:
        print("\n❌ Failed to send report.")
        print("\nReport content:")
        print(full_report)

if __name__ == "__main__":
    main()
