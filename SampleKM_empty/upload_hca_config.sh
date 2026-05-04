#!/bin/bash
#
# upload_hca_config.sh
# Uploads HCA configs to VianAI.
#
# Prerequisites:
#   - Run set_table_structure.sh to define the table schema
#   - Run create_db.sh to create the database
#   - Run ingest_data.sh to load data into the database
#
set -e

# ═══════════════════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════════════════

print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Uploads HCA configs to VianAI.

Prerequisites:
  - Run set_table_structure.sh to define the table schema
  - Run create_db.sh to create the database
  - Run ingest_data.sh to load data into the database

Options:
  CONFIG_DIR=PATH                Path to HCA config directory
  PROJECT_NAME=NAME              Name of the project
  debug=true|false               Enable verbose debug output
  Default: false

Examples:
  # Using defaults
  ./$(basename "$0")

  # Custom config directory
  ./$(basename "$0") CONFIG_DIR=./hca/config

  # Custom project name
  ./$(basename "$0") PROJECT_NAME=my_project

  # With debug output
  ./$(basename "$0") debug=true
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

SCRIPT_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CONFIG_DIR="${CONFIG_DIR:-${SCRIPT_DIR}/../hca/content}"
PROJECT_NAME="${PROJECT_NAME:-lifescience}"
DEBUG="${debug:-false}"

if [[ "$DEBUG" == "true" ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Configuration:"
  echo "  CONFIG_DIR: $CONFIG_DIR"
  echo "  PROJECT_NAME: $PROJECT_NAME"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════════

is_logged_in=$(vianctl auth check 2>/dev/null | jq -r '.Authorized')
if [[ "$is_logged_in" == "false" ]]; then
  vianctl auth login
fi


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD HCA CONFIGS
# ═══════════════════════════════════════════════════════════════════════════════

find "$CONFIG_DIR" -type f | while read -r src_file; do
  rel_path="${src_file#$CONFIG_DIR/}"
  tgt_file="/source/hca/${PROJECT_NAME}/${rel_path}"
  if vianctl cache raw "$tgt_file" "$src_file"; then
    echo "✅ $src_file uploaded successfully"
  else
    echo "❌ Failed to upload $src_file"
    exit 1
  fi
done

echo "✅ HCA configs uploaded successfully"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""