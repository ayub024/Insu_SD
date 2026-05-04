#!/bin/bash
#
# ingest_data.sh
# Reads files from a source directory and uses the dataloading API to ingest
# the data into the specified database.
#
# Prerequisites:
#   - Run set_table_structure.sh to define the table schema
#   - Run create_db.sh to create the database
#
set -e

# ═══════════════════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════════════════

print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Reads data files from a source directory and ingests them into the specified
database using the VianAI dataloading API.

Prerequisites:
  - Run set_table_structure.sh to define the table schema
  - Run create_db.sh to create the database

Options:
  DATA_DIR=PATH                Directory containing data files
                               Default: ./data
  DB_NAME=NAME                 Target database name
                               Default: lifescience
  FILE_TYPE=TYPE               File type to ingest (parquet, csv, etc.)
                               Default: parquet
  MODE=MODE                    Ingestion mode (overwrite, append)
                               Default: overwrite
  debug=true|false             Enable verbose debug output
                               Default: false

Examples:
  # Using defaults
  ./$(basename "$0")

  # Custom data directory and database
  ./$(basename "$0") DATA_DIR=/path/to/data DB_NAME=my_inventory

  # Append mode with CSV files
  ./$(basename "$0") FILE_TYPE=csv MODE=append

  # Full custom configuration
  ./$(basename "$0") DATA_DIR=./data DB_NAME=inventory FILE_TYPE=parquet MODE=overwrite debug=true
EOF
  exit 0
}

if [[ "$1" == "--help" || "$1" == "-h" || "$1" == "help" ]]; then
  print_help
fi

# ═══════════════════════════════════════════════════════════════════════════════
# ARGUMENT PARSING
# ═══════════════════════════════════════════════════════════════════════════════

for arg in "$@"; do
  if [[ "$arg" =~ ^[A-Za-z_][A-Za-z0-9_]*=.+$ ]]; then
    key="${arg%%=*}"
    value="${arg#*=}"
    export "$key"="$value"
  fi
done

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
DEBUG="${debug:-false}"

DATA_DIR="${DATA_DIR:-${SCRIPT_DIR}/../data}"
DB_NAME="${DB_NAME:-lifescience}"
FILE_TYPE="${FILE_TYPE:-parquet}"
MODE="${MODE:-replace}"

# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

if [[ ! -d "$DATA_DIR" ]]; then
  echo "❌ Error: Data directory not found: $DATA_DIR"
  exit 1
fi

# Check if there are files to ingest
file_count=$(find "$DATA_DIR" -maxdepth 1 -name "*.${FILE_TYPE}" 2>/dev/null | wc -l)
if [[ "$file_count" -eq 0 ]]; then
  echo "⚠️  Warning: No .${FILE_TYPE} files found in $DATA_DIR"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DEBUG" == "true" ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Configuration:"
  echo "  DB_NAME: $DB_NAME"
  echo "  DATA_DIR: $DATA_DIR"
  echo "  FILE_TYPE: $FILE_TYPE"
  echo "  MODE: $MODE"
  echo "  Files found: $file_count"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════

is_logged_in=$(vianctl auth check 2>/dev/null | jq -r '.Authorized')
if [[ "$is_logged_in" != "true" ]]; then
  echo "🔐 Authenticating with VianAI..."
  vianctl auth login
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DATA INGESTION
# ═══════════════════════════════════════════════════════════════════════════════

echo "📤 Uploading data to database: $DB_NAME"
echo "   Source: $DATA_DIR"
echo "   File type: $FILE_TYPE"
echo "   Mode: $MODE"

if vianctl dataloading upload "$DATA_DIR" "$DB_NAME" \
  --filetype "$FILE_TYPE" \
  --overwrite "$MODE"; then
  echo "✅ Data ingestion completed successfully"
else
  echo "❌ Data ingestion failed"
  exit 1
fi
