#!/bin/bash
#
# OpenClaw Service Health Check
# Monitors all OpenClaw infrastructure and sends Discord alerts when services are down
#

set -uo pipefail  # Removed -e to allow checks to fail without stopping script

# State file to track previous status (avoid spam alerts)
STATE_FILE="$HOME/.openclaw/workspace/.health-check-state.json"
DISCORD_NOTIFY="$HOME/.openclaw/workspace/discord-notify.sh"
TELEGRAM_NOTIFY="$HOME/.openclaw/workspace/telegram-notify.sh"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Service tracking
declare -A CURRENT_STATUS
declare -A PREVIOUS_STATUS
SERVICES_DOWN=()
SERVICES_RECOVERED=()

# Initialize state file if it doesn't exist
if [[ ! -f "$STATE_FILE" ]]; then
    echo '{}' > "$STATE_FILE"
fi

# Load previous state
load_previous_state() {
    if [[ -f "$STATE_FILE" ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ \"([^\"]+)\":\"([^\"]+)\" ]]; then
                PREVIOUS_STATUS["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
            fi
        done < "$STATE_FILE"
    fi
}

# Save current state
save_current_state() {
    local json="{"
    local first=true
    for service in "${!CURRENT_STATUS[@]}"; do
        if [[ "$first" == true ]]; then
            first=false
        else
            json+=","
        fi
        json+="\"$service\":\"${CURRENT_STATUS[$service]}\""
    done
    json+="}"
    echo "$json" > "$STATE_FILE"
}

# Check if Docker container is healthy
check_docker_container() {
    local name="$1"
    local label="$2"
    
    if docker ps --filter "name=$name" --filter "health=healthy" --format '{{.Names}}' 2>/dev/null | grep -q "$name"; then
        CURRENT_STATUS["$label"]="up"
        echo -e "${GREEN}✓${NC} $label: Running (healthy)"
        return 0
    elif docker ps --filter "name=$name" --format '{{.Names}}' 2>/dev/null | grep -q "$name"; then
        CURRENT_STATUS["$label"]="unhealthy"
        echo -e "${YELLOW}⚠${NC} $label: Running (unhealthy)"
        return 1
    else
        CURRENT_STATUS["$label"]="down"
        echo -e "${RED}✗${NC} $label: Not running"
        return 1
    fi
}

# Check systemd user service
check_systemd_service() {
    local service="$1"
    local label="$2"
    
    if systemctl --user is-active "$service" >/dev/null 2>&1; then
        CURRENT_STATUS["$label"]="up"
        echo -e "${GREEN}✓${NC} $label: Active"
        return 0
    else
        CURRENT_STATUS["$label"]="down"
        echo -e "${RED}✗${NC} $label: Inactive"
        return 1
    fi
}

# Check if process is running
check_process() {
    local pattern="$1"
    local label="$2"
    
    if pgrep -f "$pattern" >/dev/null 2>&1; then
        CURRENT_STATUS["$label"]="up"
        echo -e "${GREEN}✓${NC} $label: Running"
        return 0
    else
        CURRENT_STATUS["$label"]="down"
        echo -e "${RED}✗${NC} $label: Not running"
        return 1
    fi
}

# Check if cron job exists
check_cron_job() {
    local pattern="$1"
    local label="$2"
    
    if crontab -l 2>/dev/null | grep -q "$pattern"; then
        CURRENT_STATUS["$label"]="up"
        echo -e "${GREEN}✓${NC} $label: Configured"
        return 0
    else
        CURRENT_STATUS["$label"]="down"
        echo -e "${RED}✗${NC} $label: Not configured"
        return 1
    fi
}

# Send Discord notification
send_alert() {
    local message="$1"
    if [[ -x "$TELEGRAM_NOTIFY" ]]; then
        "$TELEGRAM_NOTIFY" "blocker" "health" "OpenClaw Health Check" "$message"
    elif [[ -x "$DISCORD_NOTIFY" ]]; then
        "$DISCORD_NOTIFY" "$message"
    else
        echo "Warning: telegram-notify.sh not found or not executable"
    fi
}

# Compare states and determine what to alert
check_state_changes() {
    for service in "${!CURRENT_STATUS[@]}"; do
        local current="${CURRENT_STATUS[$service]}"
        local previous="${PREVIOUS_STATUS[$service]:-unknown}"
        
        if [[ "$current" != "up" ]] && [[ "$previous" == "up" ]]; then
            # Service went down
            SERVICES_DOWN+=("$service")
        elif [[ "$current" == "up" ]] && [[ "$previous" != "up" ]] && [[ "$previous" != "unknown" ]]; then
            # Service recovered
            SERVICES_RECOVERED+=("$service")
        fi
    done
}

# Main health check
main() {
    echo "================================================"
    echo "OpenClaw Health Check - $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================"
    echo ""
    
    load_previous_state
    
    # Check Docker containers
    echo "Docker Containers:"
    check_docker_container "openclaw-postgres" "PostgreSQL Database"
    echo ""
    
    # Check systemd services
    echo "Systemd Services:"
    check_systemd_service "openclaw-task-manager-db.timer" "Autonomous Task Manager (DB)"
    check_systemd_service "openclaw-telegram-commands.timer" "Telegram Task Commands"
    check_systemd_service "openclaw-chat-router.service" "Chat Router"
    check_systemd_service "openclaw-gateway.service" "Gateway"
    echo ""
    
    # Check cron jobs
    echo "Cron Jobs:"
    check_cron_job "daily-report-discord.py" "Daily Report"
    echo ""
    
    # Check state changes
    check_state_changes
    
    # Send alerts for services that went down
    if [[ ${#SERVICES_DOWN[@]} -gt 0 ]]; then
        echo -e "${RED}🚨 ALERT: Services went down!${NC}"
        local alert_msg="🚨 **OpenClaw Alert**\n\n**Services DOWN:**\n"
        for service in "${SERVICES_DOWN[@]}"; do
            alert_msg+="- ❌ $service\n"
            echo -e "${RED}  - $service${NC}"
        done
        alert_msg+="\n_Check: \`systemctl --user status\` and \`docker ps\`_"
        send_alert "$alert_msg"
        echo ""
    fi
    
        # Recovery notices are suppressed to keep Telegram noise low
    
    # Summary
    local total_services=${#CURRENT_STATUS[@]}
    local services_up=0
    for status in "${CURRENT_STATUS[@]}"; do
        if [[ "$status" == "up" ]]; then
            services_up=$((services_up + 1))
        fi
    done
    
    echo "================================================"
    if [[ $services_up -eq $total_services ]]; then
        echo -e "${GREEN}All systems operational ($services_up/$total_services)${NC}"
    else
        echo -e "${YELLOW}Issues detected: $((total_services - services_up))/$total_services services down${NC}"
    fi
    echo "================================================"
    
    # Save state for next run
    save_current_state
    
    # Exit with appropriate code
    if [[ ${#SERVICES_DOWN[@]} -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
