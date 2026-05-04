#!/bin/bash
#
# set_table_structure.sh
# Creates a VianAI config to store the schema for tables when creating tables.
#
# This script must be run FIRST before creating the database, as it defines
# the table structure that will be used during database creation.
#
# ⚠️  VERSION COMPATIBILITY:
#     - VianAI 4.3R1: vianctl vianai_config set only works as UPDATE
#                     (returns 404 if no config exists; must create config first)
#     - VianAI 4.3R2+: vianctl vianai_config set works as UPSERT
#                     (creates if not exists, updates if exists)
#
set -e

# ═══════════════════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════════════════

print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Creates a VianAI config to store the table schema definition.
This must be run before create_db.sh.

⚠️  VERSION COMPATIBILITY:
    - VianAI 4.3R1: vianctl vianai_config set only works as UPDATE
                    (returns 404 if no config exists; must create config first)
    - VianAI 4.3R2+: vianctl vianai_config set works as UPSERT
                    (creates if not exists, updates if exists)

Options:
  TABLE_STRUCTURE_CONFIG_FILE=PATH   Path to JSON config file
                                     Default: ./config/table_config.json
  TABLE_SCHEMA_NAME=NAME             Name for the table schema
                                     Default: lifescience
  debug=true|false                   Enable verbose debug output
                                     Default: false

Examples:
  # Using defaults
  ./$(basename "$0")

  # Custom config file
  ./$(basename "$0") TABLE_STRUCTURE_CONFIG_FILE=./config/custom_table.json

  # Custom schema name with debug
  ./$(basename "$0") TABLE_SCHEMA_NAME=my_schema debug=true
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

TABLE_STRUCTURE_CONFIG_FILE="${TABLE_STRUCTURE_CONFIG_FILE:-${SCRIPT_DIR}/../config/table_config.json}"
TABLE_SCHEMA_NAME="${TABLE_SCHEMA_NAME:-lifescience}"

# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

if [[ ! -f "$TABLE_STRUCTURE_CONFIG_FILE" ]]; then
  echo "❌ Error: Table structure config file not found: $TABLE_STRUCTURE_CONFIG_FILE"
  exit 1
fi

# Read and validate JSON
if ! TABLES_JSON=$(jq -c '.' "$TABLE_STRUCTURE_CONFIG_FILE" 2>/dev/null); then
  echo "❌ Error: Invalid JSON in config file: $TABLE_STRUCTURE_CONFIG_FILE"
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DEBUG" == "true" ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Configuration:"
  echo "  TABLE_STRUCTURE_CONFIG_FILE: $TABLE_STRUCTURE_CONFIG_FILE"
  echo "  TABLE_SCHEMA_NAME: $TABLE_SCHEMA_NAME"
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
# SET TABLE STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════

echo "📋 Setting table structure configuration..."

if vianctl vianai_config set \
  --system "vianai" \
  --module "dataloading" \
  --name "$TABLE_SCHEMA_NAME" \
  --value "$TABLES_JSON"; then
  echo "✅ Table structure configuration set successfully"
  echo "   Schema Name: $TABLE_SCHEMA_NAME"
else
  echo "❌ Failed to set table structure configuration"
  exit 1
fi
