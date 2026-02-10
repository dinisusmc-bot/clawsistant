#!/bin/bash
set -e

BACKUP_FILE="$1"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: ./restore.sh backup.tar.gz"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "=== Bot Restore ==="
echo "Restoring from: $BACKUP_FILE"
echo ""

# Extract backup
tar -xzf "$BACKUP_FILE"

echo ""
echo "✓ Restore complete"
echo ""
echo "Run setup.sh to complete installation:"
echo "  ./setup.sh"
