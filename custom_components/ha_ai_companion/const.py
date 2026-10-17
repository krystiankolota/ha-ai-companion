"""Constants for the AI Configuration Agent integration."""

DOMAIN = "ha_ai_companion"

# Configuration constants
CONF_API_KEY = "api_key"
CONF_API_URL = "api_url"
CONF_MODEL = "model"
CONF_LOG_LEVEL = "log_level"
CONF_TEMPERATURE = "temperature"
CONF_REASONING_EFFORT = "reasoning_effort"
CONF_SYSTEM_PROMPT_FILE = "system_prompt_file"
CONF_ENABLE_CACHE_CONTROL = "enable_cache_control"
CONF_USAGE_TRACKING = "usage_tracking"
CONF_NODERED_FLOWS_FILE = "nodered_flows_file"
CONF_NODERED_URL = "nodered_url"
CONF_NODERED_TOKEN = "nodered_token"
CONF_SUGGESTION_PROMPT = "suggestion_prompt"
# Fast model: side tasks (suggestions, summaries, memory). Blank = main model.
CONF_FAST_MODEL = "fast_model"
CONF_AA_API_KEY = "artificial_analysis_api_key"
CONF_MAX_SUGGESTIONS = "max_suggestions"
CONF_MAX_ITERATIONS = "max_iterations"

# Default values
DEFAULT_API_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_LOG_LEVEL = "info"
DEFAULT_USAGE_TRACKING = "stream_options"
DEFAULT_MAX_SESSIONS = 50
DEFAULT_MAX_SUGGESTIONS = 10
