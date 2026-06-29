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
# Research layer (cheaper: reading/exploring, suggestions). Was "suggestion_*".
CONF_RESEARCH_MODEL = "research_model"
CONF_RESEARCH_API_URL = "research_api_url"
CONF_RESEARCH_API_KEY = "research_api_key"
CONF_RESEARCH_USAGE_TRACKING = "research_usage_tracking"
# Reasoning layer (stronger: planning/writing changes). Was "config_*".
CONF_REASONING_MODEL = "reasoning_model"
CONF_REASONING_API_URL = "reasoning_api_url"
CONF_REASONING_API_KEY = "reasoning_api_key"
CONF_REASONING_USAGE_TRACKING = "reasoning_usage_tracking"
CONF_INPUT_PRICE_PER_1M = "input_price_per_1m"
CONF_OUTPUT_PRICE_PER_1M = "output_price_per_1m"
CONF_MAX_SUGGESTIONS = "max_suggestions"
CONF_MAX_ITERATIONS = "max_iterations"

# Default values
DEFAULT_API_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_LOG_LEVEL = "info"
DEFAULT_USAGE_TRACKING = "stream_options"
DEFAULT_MAX_SESSIONS = 50
DEFAULT_MAX_SUGGESTIONS = 10
