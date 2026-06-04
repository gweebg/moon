#!/usr/bin/env bash

# v1.1.0
# dnf-automatic-discord-notify.sh
# sends dnf-automatic results as a Discord embed with attached report

LOG_FILE="/var/log/dnf-discord-notify.log"

log() {
    message="$(date '+%Y-%m-%d %H:%M:%S') - $1"
    echo "$message" >> "$LOG_FILE"
}

create_temp_report() {
    body_file=$(mktemp /tmp/dnf-report.XX)
    cat > "$body_file"
    echo "$body_file"
}

check_reboot_scheduled() {
    local scheduled_file="/run/systemd/shutdown/scheduled"
    
    if [[ ! -f "$scheduled_file" ]]; then
        return 1
    fi
    
    # Example file:
    #   USEC=1779069600000000
    #   WARN_WALL=1
    #   MODE=reboot
    #   UID=0
    #   TTY=pts/0
    # We only need the USEC pair.
    local usec
    usec=$(grep -m1 '^USEC=' "$scheduled_file" | cut -d'=' -f2)
    
    if [[ -z "$usec" ]]; then
        return 1
    fi
    
    # Convert microseconds to seconds (Unix timestamp)
    echo $(( usec / 1000000 ))
    return 0
}

message_color_indicator() {
    # blue if no reboot; purple if reboot scheduled
    [[ "$1" == "No" ]] && echo "3447003" || echo "10181046"
}

get_os_thumbnail() {
    case "$NAME" in
        "Debian GNU/Linux") echo "https://www.debian.org/logos/openlogo-nd-100.png" ;;
        "Rocky Linux")      echo "https://raw.githubusercontent.com/rocky-linux/branding/refs/heads/main/logo/out/icon-bg_transparent-primary-256x.png" ;;
    esac
}

build_json_payload() {
    
    local host os \
    checked_at color \
    thumbnail_url reboot="$1"
    
    os="$NAME $VERSION_ID"
    host="$(hostname -f)"
    color=$(message_color_indicator "$reboot")
    checked_at="$(date '+%A %W %Y %X')"
    thumbnail_url=$(get_os_thumbnail)
    
    # Format reboot value for display
    local reboot_display="$reboot"
    [[ "$reboot" =~ ^[0-9]+$ ]] && reboot_display="<t:$reboot:R>"
    
    jq -n \
    --arg host "\`$host\`" \
    --arg reboot_display "$reboot_display" \
    --arg footer "Updated at: $checked_at" \
    --arg title "Update Report for \`$host\`" \
    --arg os "$os" \
    --arg color "$color" \
    --arg thumbnail_url "$thumbnail_url" \
    '{
            "embeds": [{
                "title": $title,
                "color": ($color | tonumber),
                "thumbnail": (if $thumbnail_url != "" then {"url": $thumbnail_url} else null end),
                "fields": [
                    {"name": "Host:", "value": $host},
                    {"name": "Operating System:", "value": $os},
                    {"name": "Reboot status:", "value": $reboot_display}
                ],
                "footer": {"text": $footer}
            }]
    }'
}

send_discord() {
    local payload="$1" \
    file="$2" \
    webhook="$3" \
    response
    
    response=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -F "payload_json=$payload" \
        -F "file=@$file;filename=dnf-report-$(date -I).txt" \
    "$webhook")
    
    if [[ "$response" -ge 200 && "$response" -lt 300 ]]; then
        log "Discord notification sent successfully."
    else
        log "Error sending Discord notification, HTTP response: $response"
    fi
}

main() {
    # for os related information
    source /etc/os-release
    
    local reboot body_file payload
    
    if ts=$(check_reboot_scheduled); then
        reboot="$ts"
    else
        reboot="No"
    fi
    
    body_file=$(create_temp_report)
    
    payload=$(build_json_payload "$reboot")
    send_discord "$payload" "$body_file" "$DISCORD_WEBHOOK"
    
    rm -f "$body_file"
}

main