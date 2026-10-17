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
export REASONING_EFFORT=$(bashio::config 'reasoning_effort' ${REASONING_EFFORT:-})
export ENABLE_CACHE_CONTROL=$(bashio::config 'enable_cache_control' ${ENABLE_CACHE_CONTROL:-false})
export MAX_ITERATIONS=$(bashio::config 'max_iterations' ${MAX_ITERATIONS:-})
export USAGE_TRACKING=$(bashio::config 'usage_tracking' ${USAGE_TRACKING:-stream_options})
# Fast model for side tasks; blank = main model.
export FAST_MODEL=$(bashio::config 'fast_model' ${FAST_MODEL:-})
export SUGGESTION_PROMPT=$(bashio::config 'suggestion_prompt' ${SUGGESTION_PROMPT:-})
export NODERED_URL=$(bashio::config 'nodered_url' ${NODERED_URL:-})
export NODERED_TOKEN=$(bashio::config 'nodered_token' ${NODERED_TOKEN:-})
export NODERED_FLOWS_FILE=$(bashio::config 'nodered_flows_file' ${NODERED_FLOWS_FILE:-})
export ARTIFICIAL_ANALYSIS_API_KEY=$(bashio::config 'artificial_analysis_api_key' ${ARTIFICIAL_ANALYSIS_API_KEY:-})

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
