#!/bin/bash
#
# create_db.sh
# Creates an external connection object so VianAI can interface with a database connector.
# This creates a connection to ClickHouse with a new database using the defined table structure.
#
# Prerequisites: Run set_table_structure.sh first to define the table schema.
#
set -e

# ═══════════════════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════════════════

print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Creates an external connection object for VianAI to interface with ClickHouse.
This script creates a new database with the defined table structure.

Prerequisites:
  - Run set_table_structure.sh first to define the table schema

Options:
  DB_NAME=NAME                 Name for the database
                               Default: lifescience
  CONN_NAME=NAME               Name for the base database connection
                               Default: lifescience_base
  TABLE_SCHEMA_NAME=NAME       Name of the table schema (must match set_table_structure.sh)
                               Default: lifescience
  PARENT_CONN_NAME=NAME        Parent connection name
                               Default: Internal clickhouse DB
  debug=true|false             Enable verbose debug output
                               Default: false

Examples:
  # Using defaults
  ./$(basename "$0")

  # Custom database name
  ./$(basename "$0") DB_NAME=my_inventory_db

  # Full custom configuration
  ./$(basename "$0") DB_NAME=custom_db CONN_NAME=custom_db_base TABLE_SCHEMA_NAME=custom_schema debug=true
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

DB_NAME="${DB_NAME:-lifescience}"
CONN_NAME="${CONN_NAME:-lifescience_base}"
TABLE_SCHEMA_NAME="${TABLE_SCHEMA_NAME:-lifescience}"
PARENT_CONN_NAME="${PARENT_CONN_NAME:-Internal clickhouse DB}"

# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DEBUG" == "true" ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Configuration:"
  echo "  DB_NAME: $DB_NAME"
  echo "  CONN_NAME: $CONN_NAME"
  echo "  TABLE_SCHEMA_NAME: $TABLE_SCHEMA_NAME"
  echo "  PARENT_CONN_NAME: $PARENT_CONN_NAME"
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
# DATABASE CREATION
# ═══════════════════════════════════════════════════════════════════════════════

echo "🔧 Creating database: $DB_NAME"

create_db_output=$(vianctl metadata create-db \
  --db-name "$DB_NAME" \
  --conn-name "$CONN_NAME" \
  --table-schema-name "$TABLE_SCHEMA_NAME" \
  --parent-conn-name "$PARENT_CONN_NAME")
  

# Extract status and message
db_status=$(echo "$create_db_output" | jq -r '.status // "unknown"')
db_message=$(echo "$create_db_output" | jq -r '.message // "No message provided"')

# Display result based on status
case "$db_status" in
  "success")
    echo "✅ Database created successfully"
    echo "   Database: $DB_NAME"
    echo "   Message: $db_message"
    ;;
  "error")
    echo "❌ Database creation failed: $db_message"
    [[ "$DEBUG" == "true" ]] && echo "$create_db_output" | jq .
    exit 1
    ;;
  *)
    echo "⚠️ Unknown response status: $db_status"
    echo "   Message: $db_message"
    [[ "$DEBUG" == "true" ]] && echo "$create_db_output" | jq .
    ;;
esac

# Show full JSON output if debug is enabled
[[ "$DEBUG" == "true" ]] && echo "$create_db_output" | jq .
