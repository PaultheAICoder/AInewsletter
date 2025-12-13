#!/bin/bash
# Weekly Newsletter Generation Script
# Runs every Friday morning to generate and send the newsletter
#
# Cron entry (Friday at 6 AM):
# 0 6 * * 5 /home/pbrown/AInewsletter/scripts/cron_newsletter.sh >> /home/pbrown/AInewsletter/logs/cron_newsletter.log 2>&1

set -e

PROJECT_DIR="/home/pbrown/AInewsletter"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_FILE="$PROJECT_DIR/logs/cron_newsletter_$(date +%Y%m%d).log"

# Ensure log directory exists
mkdir -p "$PROJECT_DIR/logs"

echo "========================================" >> "$LOG_FILE"
echo "Newsletter Generation - $(date)" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Change to project directory
cd "$PROJECT_DIR"

# Step 1: Generate newsletter
echo "[$(date)] Generating newsletter..." >> "$LOG_FILE"
ISSUE_ID=$(python scripts/generate_newsletter.py 2>&1 | tee -a "$LOG_FILE" | grep -oP 'issue #\K\d+' | tail -1)

if [ -z "$ISSUE_ID" ]; then
    echo "[$(date)] ERROR: Failed to generate newsletter or extract issue ID" >> "$LOG_FILE"
    exit 1
fi

echo "[$(date)] Generated issue #$ISSUE_ID" >> "$LOG_FILE"

# Step 2: Send newsletter (uncomment when SMTP is configured)
# echo "[$(date)] Sending newsletter issue #$ISSUE_ID..." >> "$LOG_FILE"
# python scripts/send_newsletter.py --issue-id "$ISSUE_ID" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] Newsletter generation complete" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
