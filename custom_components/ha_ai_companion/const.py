"""Constants for the AI Configuration Agent integration."""

DOMAIN = "ha_ai_companion"

# Configuration constants
CONF_API_KEY = "api_key"
CONF_API_URL = "api_url"
CONF_MODEL = "model"
CONF_LOG_LEVEL = "log_level"
CONF_TEMPERATURE = "temperature"
CONF_SYSTEM_PROMPT_FILE = "system_prompt_file"
CONF_ENABLE_CACHE_CONTROL = "enable_cache_control"
CONF_USAGE_TRACKING = "usage_tracking"
CONF_NODERED_FLOWS_FILE = "nodered_flows_file"
CONF_NODERED_URL = "nodered_url"
CONF_NODERED_TOKEN = "nodered_token"
CONF_SUGGESTION_PROMPT = "suggestion_prompt"
CONF_SUGGESTION_MODEL = "suggestion_model"
CONF_SUGGESTION_API_URL = "suggestion_api_url"
CONF_SUGGESTION_API_KEY = "suggestion_api_key"
CONF_CONFIG_MODEL = "config_model"
CONF_CONFIG_API_URL = "config_api_url"
CONF_CONFIG_API_KEY = "config_api_key"

# Default values
DEFAULT_API_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_LOG_LEVEL = "info"
DEFAULT_USAGE_TRACKING = "stream_options"
