#!/usr/bin/with-contenv bashio

# Disable Python output buffering for real-time streaming
export PYTHONUNBUFFERED=1

# Get configuration from add-on options
export OPENAI_API_URL=$(bashio::config 'openai_api_url' ${OPENAI_API_URL:-})
export OPENAI_API_KEY=$(bashio::config 'openai_api_key' ${OPENAI_API_KEY:-})
export OPENAI_MODEL=$(bashio::config 'openai_model' ${OPENAI_MODEL:-})
export LOG_LEVEL=$(bashio::config 'log_level' ${LOG_LEVEL:-info})
export SYSTEM_PROMPT_FILE=$(bashio::config 'system_prompt_file' ${SYSTEM_PROMPT_FILE:-})
export TEMPERATURE=$(bashio::config 'temperature' ${TEMPERATURE:-})
export ENABLE_CACHE_CONTROL=$(bashio::config 'enable_cache_control' ${ENABLE_CACHE_CONTROL:-false})
export USAGE_TRACKING=$(bashio::config 'usage_tracking' ${USAGE_TRACKING:-stream_options})
export SUGGESTION_USAGE_TRACKING=$(bashio::config 'suggestion_usage_tracking' ${SUGGESTION_USAGE_TRACKING:-default})
export CONFIG_USAGE_TRACKING=$(bashio::config 'config_usage_tracking' ${CONFIG_USAGE_TRACKING:-default})
export SUGGESTION_MODEL=$(bashio::config 'suggestion_model' ${SUGGESTION_MODEL:-})
export SUGGESTION_API_URL=$(bashio::config 'suggestion_api_url' ${SUGGESTION_API_URL:-})
export SUGGESTION_API_KEY=$(bashio::config 'suggestion_api_key' ${SUGGESTION_API_KEY:-})
export CONFIG_MODEL=$(bashio::config 'config_model' ${CONFIG_MODEL:-})
export CONFIG_API_URL=$(bashio::config 'config_api_url' ${CONFIG_API_URL:-})
export CONFIG_API_KEY=$(bashio::config 'config_api_key' ${CONFIG_API_KEY:-})
export SUGGESTION_PROMPT=$(bashio::config 'suggestion_prompt' ${SUGGESTION_PROMPT:-})
export NODERED_URL=$(bashio::config 'nodered_url' ${NODERED_URL:-})
export NODERED_TOKEN=$(bashio::config 'nodered_token' ${NODERED_TOKEN:-})
export NODERED_FLOWS_FILE=$(bashio::config 'nodered_flows_file' ${NODERED_FLOWS_FILE:-})

# Home Assistant configuration
export HA_CONFIG_DIR="/homeassistant"
export ADDON_CONFIG_DIR="/config"
export BACKUP_DIR="/backup/config-agent"

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# Log startup
bashio::log.info "Starting HA AI Companion..."
bashio::log.info "OpenAI API: ${OPENAI_API_URL}"
bashio::log.info "Model: ${OPENAI_MODEL}"
bashio::log.info "HA Config: ${HA_CONFIG_DIR}"

# Start application
exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8099 \
    --log-level "${LOG_LEVEL}" \
    --no-access-log \
    --timeout-keep-alive 300
