#!/bin/bash
set -e

BACKUP_DIR="$HOME/bot-deploy-setup"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="backup_${TIMESTAMP}.tar.gz"

echo "=== Bot Backup ==="
echo "Creating backup: $BACKUP_FILE"
echo ""

cd "$BACKUP_DIR"

# Create backup (excluding large files and node_modules)
tar -czf "$BACKUP_FILE" \
    --exclude='node_modules' \
    --exclude='.git' \
    --exclude='*.log' \
    --exclude='.cache' \
    --exclude='__pycache__' \
    openclaw-config projects scripts

echo ""
echo "✓ Backup created: $BACKUP_FILE"
echo ""
echo "To restore on a new server:"
echo "  cp $BACKUP_FILE /path/to/new/server/"
echo "  tar -xzf $BACKUP_FILE"
echo "  ./setup.sh"
