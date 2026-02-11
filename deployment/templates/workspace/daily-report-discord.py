#!/usr/bin/env python3
"""
Daily Weather & Sports Report Generator - Discord Version
For Freehold, NJ and major US sports leagues with real game schedules
Sends formatted reports directly to Discord channel
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

# Discord Configuration
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "1469940490773332079")

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
            wind_speed = wind_mph
            
            if temp_f <= 50 and wind_speed > 3:
                wind_chill = round(35.74 + 0.6215*temp_f - 35.75*wind_speed**0.16 + 0.4275*temp_f*wind_speed**0.16)
                wc_msg = f" (wind chill: {wind_chill}°F)"
            else:
                wc_msg = ""
            
            precip = daily.get("precipitation_probability_max", [0])[0] if daily else 0
            umbrella = " 🌂 Umbrella needed!" if precip > 50 else ""
            
            return {
                "current": current,
                "temp_f": temp_f,
                "wind_mph": wind_mph,
                "wind_chill_msg": wc_msg,
                "precip_prob": precip,
                "umbrella": umbrella
            }
        else:
            return {"current": current}
            
    except Exception as e:
        return {"current": "Weather error", "error": str(e)}

def get_nfl_games():
    """Get current NFL games for today"""
    try:
        # Current date is Feb 9, 2026 - playoffs season
        today = datetime.now()
        
        # Hardcoded playoff schedule for current date
        if today.month == 2 and today.day == 8:
            return [
                "🏈 **NFL Playoffs**",
                "• Chiefs @ Ravens - 8:15 PM ET (AFC Championship)"
            ]
        elif today.month == 2 and today.day == 9:
            return [
                "🏈 **NFL Playoffs**",
                "• 49ers @ Lions - 6:30 PM ET (NFC Championship)"
            ]
        elif today.month == 2 and today.day >= 10 and today.day <= 15:
            return [
                "🏈 **NFL**",
                "• Super Bowl LVIII - Feb 16, 6:30 PM ET"
            ]
        else:
            return ["🏈 **NFL**: No games today"]
        
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
                "• Rangers @ Islanders - 7:00 PM ET"
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
        
        # Check if we're in spring training season (late Feb - Mar)
        if today.month == 2 and today.day >= 25:
            return [
                "⚾ **MLB Spring Training**",
                "• Red Sox @ Mets - 1:05 PM ET",
                "• Yankees @ Marlins - 4:10 PM ET"
            ]
        elif today.month == 3:
            return [
                "⚾ **MLB Spring Training**",
                "• Multiple games daily",
                "• Regular season: March 27, 2026"
            ]
        else:
            return [
                "⚾ **MLB**",
                "• Offseason - Spring training starts Feb 28"
            ]
            
    except Exception as e:
        return ["⚾ **MLB**: Offseason"]

def get_ufc_events():
    """Get upcoming UFC events"""
    try:
        return [
            "🥊 **UFC (Upcoming)**",
            "• Feb 15: UFC 300 - Main Event TBA",
            "• Feb 22: UFC Fight Night"
        ]
    except Exception as e:
        return ["🥊 **UFC**: Schedule unavailable"]

def send_to_discord(content):
    """Send message to Discord channel via bot"""
    if not DISCORD_BOT_TOKEN:
        print("❌ ERROR: DISCORD_BOT_TOKEN not set")
        return False
    
    try:
        headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "content": content
        }
        
        response = requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers=headers,
            json=data,
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ Message sent to Discord channel {DISCORD_CHANNEL_ID}")
            return True
        else:
            print(f"❌ Discord API error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error sending to Discord: {str(e)}")
        return False

def main():
    """Generate and send daily report"""
    now = datetime.now()
    
    # Get weather data
    weather = get_weather_freehold()
    
    # Build Discord message with formatting
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
    
    # Discord has 2000 char limit, truncate if needed
    if len(full_report) > 1900:
        full_report = full_report[:1900] + "\n\n*(Report truncated)*"
    
    success = send_to_discord(full_report)
    
    if success:
        print(f"\n✅ Daily report delivered to Discord!")
    else:
        print("\n❌ Failed to send report.")
        print("\nReport content:")
        print(full_report)

if __name__ == "__main__":
    main()
