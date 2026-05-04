#!/bin/bash
#
# create_datasource.sh
# Creates a datasource by cloning metadata and configuring it for VianAI.
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

Creates a datasource by cloning metadata and configuring it for VianAI.
This is the final step in the data pipeline setup.

Prerequisites:
  - Run set_table_structure.sh to define the table schema
  - Run create_db.sh to create the database
  - Run ingest_data.sh to load data into the database

Options:
  METADATA_NAME=NAME           Name for the metadata/datasource
                               Default: lifescience
  DB_NAME=NAME                 Database name to use
                               Default: lifescience
  HCA_CONFIG_PATH=PATH         Path to HCA configuration. Should be the same as the PROJECT_NAME in the upload_hca_config.sh script.
                               Default: /source/hca/<METADATA_NAME>
  COMPANY_METADATA_FILE=PATH   Path to JSON file with company metadata
                               Default: ./config/company_metadata.json
  DEFAULT_CURRENCY=CODE        Default currency code
                               Default: USD
  debug=true|false             Enable verbose debug output
                               Default: false

Examples:
  # Using defaults
  ./$(basename "$0")

  # Custom names
  ./$(basename "$0") METADATA_NAME=my_datasource DB_NAME=my_db

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

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
DEBUG="${debug:-false}"

METADATA_NAME="${METADATA_NAME:-lifescience}"
DB_NAME="${DB_NAME:-lifescience}"
HCA_CONFIG_PATH="${HCA_CONFIG_PATH:-/source/hca/${METADATA_NAME}}"
DEFAULT_CURRENCY="${DEFAULT_CURRENCY:-USD}"
COMPANY_METADATA_FILE="${COMPANY_METADATA_FILE:-${SCRIPT_DIR}/../config/company_metadata.json}"

# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY METADATA CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Load company metadata from external file if it exists, otherwise use default
if [[ -f "$COMPANY_METADATA_FILE" ]]; then
  if ! COMPANY_METADATA=$(jq -c '.' "$COMPANY_METADATA_FILE" 2>/dev/null); then
    echo "❌ Error: Invalid JSON in company metadata file: $COMPANY_METADATA_FILE"
    exit 1
  fi
else
  # Default company metadata
  COMPANY_METADATA='[
    {
      "parent_company_name": "TCS",
      "company_name": "TCS",
      "company_code": "USA",
      "company_country": "United States",
      "company_currency": "USD",
      "global_currency": "USD"
    }
  ]'
  echo "ℹ️  Using default company metadata (no file found at: $COMPANY_METADATA_FILE)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD UPDATE FIELDS
# ═══════════════════════════════════════════════════════════════════════════════

UPDATE_FIELDS=$(jq -n \
  --arg name "$METADATA_NAME" \
  --arg db_name "$DB_NAME" \
  --arg project_name "$METADATA_NAME" \
  --arg default_currency "$DEFAULT_CURRENCY" \
  --argjson company_metadata "$COMPANY_METADATA" \
  '{
    name: $name,
    db_name: $db_name,
    project_name: $project_name,
    entity_type: "single",
    default_currency: $default_currency,
    company_metadata: $company_metadata,
    data: {
      tables: []
    }
  }')

# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

if [[ "$DEBUG" == "true" ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Configuration:"
  echo "  METADATA_NAME: $METADATA_NAME"
  echo "  DB_NAME: $DB_NAME"
  echo "  HCA_CONFIG_PATH: $HCA_CONFIG_PATH"
  echo "  DEFAULT_CURRENCY: $DEFAULT_CURRENCY"
  echo "  COMPANY_METADATA_FILE: $COMPANY_METADATA_FILE"
  echo ""
  echo "Update Fields:"
  echo "$UPDATE_FIELDS" | jq .
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
# CLONE METADATA
# ═══════════════════════════════════════════════════════════════════════════════

echo "🛠️  Cloning metadata..."
echo "   Base metadata: $DB_NAME"
echo "   New metadata name: $METADATA_NAME"

clone_output=$(vianctl metadata clone \
  --base-metadata "$DB_NAME" \
  --qnametadata-version "new" \
  --version "hca" \
  --hca-config-path "$HCA_CONFIG_PATH" \
  --update-fields "$UPDATE_FIELDS")

# Check for success
uuid=$(echo "$clone_output" | jq -r '.model_setup_response[0].uuid // empty')
if [[ -n "$uuid" ]]; then
  echo "✅ Metadata cloned successfully"
  echo "   UUID: $uuid"
else
  echo "❌ Failed to clone metadata"
  [[ "$DEBUG" == "true" ]] && echo "$clone_output" | jq .
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR JOB
# ═══════════════════════════════════════════════════════════════════════════════

job_id=$(echo "$clone_output" | jq -r '.autogen_response.job_id // empty')

if [[ -z "$job_id" ]]; then
  echo "⚠️  No job ID returned, skipping job monitoring"
  exit 0
fi

echo "📡 Waiting for job to complete: $job_id"

status_output=$(vianctl jobs get "$job_id" --wait)
status=$(echo "$status_output" | jq -r '.status // "unknown"')
success=$(echo "$status_output" | jq -r '.result.success // "false"')

if [[ "$status" == "done" && "$success" == "true" ]]; then
  echo "✅ Job completed successfully"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Datasource creation complete!"
  echo "  Datasource Name: $METADATA_NAME"
  echo "  Database: $DB_NAME"
  echo "  Job ID: $job_id"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "❌ Job failed or incomplete"
  echo "   Status: $status"
  echo "   Success: $success"
  [[ "$DEBUG" == "true" ]] && echo "$status_output" | jq .
  exit 1
fi
