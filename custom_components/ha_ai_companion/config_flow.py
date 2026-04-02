"""Config flow for AI Configuration Agent integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_API_URL,
    CONF_MODEL,
    CONF_LOG_LEVEL,
    CONF_TEMPERATURE,
    CONF_SYSTEM_PROMPT_FILE,
    CONF_ENABLE_CACHE_CONTROL,
    CONF_USAGE_TRACKING,
    CONF_NODERED_FLOWS_FILE,
    CONF_NODERED_URL,
    CONF_NODERED_TOKEN,
    CONF_SUGGESTION_PROMPT,
    CONF_SUGGESTION_MODEL,
    CONF_SUGGESTION_API_URL,
    CONF_SUGGESTION_API_KEY,
    CONF_CONFIG_MODEL,
    CONF_CONFIG_API_URL,
    CONF_CONFIG_API_KEY,
    CONF_SUGGESTION_USAGE_TRACKING,
    CONF_CONFIG_USAGE_TRACKING,
    CONF_INPUT_PRICE_PER_1M,
    CONF_OUTPUT_PRICE_PER_1M,
    CONF_MAX_TOKENS,
    CONF_SUGGESTION_MAX_TOKENS,
    CONF_CONFIG_MAX_TOKENS,
    CONF_MAX_SESSIONS,
    CONF_MAX_SUGGESTIONS,
    CONF_SUGGESTION_TEMPERATURE,
    DEFAULT_API_URL,
    DEFAULT_MODEL,
    DEFAULT_LOG_LEVEL,
    DEFAULT_USAGE_TRACKING,
    DEFAULT_MAX_SESSIONS,
    DEFAULT_MAX_SUGGESTIONS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_API_KEY): cv.string,
    vol.Optional(CONF_API_URL, default=DEFAULT_API_URL): cv.string,
    vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): cv.string,
    vol.Optional(CONF_LOG_LEVEL, default=DEFAULT_LOG_LEVEL): vol.In(["debug", "info", "warning", "error"]),
    vol.Optional(CONF_TEMPERATURE): vol.Coerce(float),
    vol.Optional(CONF_SYSTEM_PROMPT_FILE): cv.string,
    vol.Optional(CONF_ENABLE_CACHE_CONTROL, default=False): cv.boolean,
    vol.Optional(CONF_USAGE_TRACKING, default=DEFAULT_USAGE_TRACKING): vol.In(["stream_options", "usage", "disabled"]),
    vol.Optional(CONF_NODERED_URL): cv.string,
    vol.Optional(CONF_NODERED_TOKEN): cv.string,
    vol.Optional(CONF_NODERED_FLOWS_FILE): cv.string,
    vol.Optional(CONF_SUGGESTION_PROMPT): cv.string,
    vol.Optional(CONF_SUGGESTION_MODEL): cv.string,
    vol.Optional(CONF_SUGGESTION_API_URL): cv.string,
    vol.Optional(CONF_SUGGESTION_API_KEY): cv.string,
    vol.Optional(CONF_CONFIG_MODEL): cv.string,
    vol.Optional(CONF_CONFIG_API_URL): cv.string,
    vol.Optional(CONF_CONFIG_API_KEY): cv.string,
    vol.Optional(CONF_SUGGESTION_USAGE_TRACKING, default="default"): vol.In(["default", "stream_options", "usage", "disabled"]),
    vol.Optional(CONF_CONFIG_USAGE_TRACKING, default="default"): vol.In(["default", "stream_options", "usage", "disabled"]),
    vol.Optional(CONF_INPUT_PRICE_PER_1M): vol.Coerce(float),
    vol.Optional(CONF_OUTPUT_PRICE_PER_1M): vol.Coerce(float),
    vol.Optional(CONF_MAX_TOKENS): cv.positive_int,
    vol.Optional(CONF_SUGGESTION_MAX_TOKENS): cv.positive_int,
    vol.Optional(CONF_CONFIG_MAX_TOKENS): cv.positive_int,
    vol.Optional(CONF_MAX_SESSIONS): cv.positive_int,
    vol.Optional(CONF_MAX_SUGGESTIONS): cv.positive_int,
    vol.Optional(CONF_SUGGESTION_TEMPERATURE): cv.string,
})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    if not data.get(CONF_API_KEY):
        raise ValueError("API key is required")

    try:
        import openai
        import os

        original_key = os.environ.get("OPENAI_API_KEY")
        original_url = os.environ.get("OPENAI_API_BASE")

        try:
            os.environ["OPENAI_API_KEY"] = data[CONF_API_KEY]
            if data.get(CONF_API_URL):
                os.environ["OPENAI_API_BASE"] = data[CONF_API_URL]

            client = openai.OpenAI(
                api_key=data[CONF_API_KEY],
                base_url=data.get(CONF_API_URL, DEFAULT_API_URL)
            )

            try:
                models = client.models.list()
                _LOGGER.debug("Successfully connected to API, found %d models", len(list(models)))
            except Exception as e:
                _LOGGER.debug("Model listing not supported (this is OK): %s", str(e))

        finally:
            if original_key:
                os.environ["OPENAI_API_KEY"] = original_key
            elif "OPENAI_API_KEY" in os.environ:
                del os.environ["OPENAI_API_KEY"]

            if original_url:
                os.environ["OPENAI_API_BASE"] = original_url
            elif "OPENAI_API_BASE" in os.environ:
                del os.environ["OPENAI_API_BASE"]

    except Exception as err:
        _LOGGER.error("Failed to validate API connection: %s", err)
        raise ValueError(f"Cannot connect to API: {err}")

    return {"title": "AI Configuration Agent"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AI Configuration Agent."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except ValueError as err:
                _LOGGER.error("Validation error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected exception: %s", err)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id("ha_ai_companion")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(import_data)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow — split into 3 steps for clarity."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._options: dict[str, Any] = {**config_entry.data, **config_entry.options}

    def _get(self, key: str, default=None):
        """Get current value from accumulated options or config entry."""
        return self._options.get(key, default)

    # ── Step 1: Core model settings ──────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 — Core API and model settings."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_suggestion_phase()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_API_URL, default=self._get(CONF_API_URL, DEFAULT_API_URL)): cv.string,
                vol.Optional(CONF_MODEL, default=self._get(CONF_MODEL, DEFAULT_MODEL)): cv.string,
                vol.Optional(CONF_MAX_TOKENS, default=self._get(CONF_MAX_TOKENS, 0)): cv.positive_int,
                vol.Optional(CONF_TEMPERATURE, default=self._get(CONF_TEMPERATURE)): vol.Coerce(float),
                vol.Optional(CONF_LOG_LEVEL, default=self._get(CONF_LOG_LEVEL, DEFAULT_LOG_LEVEL)): vol.In(["debug", "info", "warning", "error"]),
                vol.Optional(CONF_USAGE_TRACKING, default=self._get(CONF_USAGE_TRACKING, DEFAULT_USAGE_TRACKING)): vol.In(["stream_options", "usage", "disabled"]),
                vol.Optional(CONF_ENABLE_CACHE_CONTROL, default=self._get(CONF_ENABLE_CACHE_CONTROL, False)): cv.boolean,
                vol.Optional(CONF_MAX_SESSIONS, default=self._get(CONF_MAX_SESSIONS, DEFAULT_MAX_SESSIONS)): cv.positive_int,
                vol.Optional(CONF_INPUT_PRICE_PER_1M, default=self._get(CONF_INPUT_PRICE_PER_1M, 0.0)): vol.Coerce(float),
                vol.Optional(CONF_OUTPUT_PRICE_PER_1M, default=self._get(CONF_OUTPUT_PRICE_PER_1M, 0.0)): vol.Coerce(float),
            }),
        )

    # ── Step 2: Suggestion & Config phase overrides ───────────────────────────

    async def async_step_suggestion_phase(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2 — Suggestion and config phase model overrides."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_advanced()

        return self.async_show_form(
            step_id="suggestion_phase",
            data_schema=vol.Schema({
                vol.Optional(CONF_SUGGESTION_MODEL, default=self._get(CONF_SUGGESTION_MODEL, "")): cv.string,
                vol.Optional(CONF_SUGGESTION_API_URL, default=self._get(CONF_SUGGESTION_API_URL, "")): cv.string,
                vol.Optional(CONF_SUGGESTION_API_KEY, default=self._get(CONF_SUGGESTION_API_KEY, "")): cv.string,
                vol.Optional(CONF_SUGGESTION_MAX_TOKENS, default=self._get(CONF_SUGGESTION_MAX_TOKENS, 0)): cv.positive_int,
                vol.Optional(CONF_SUGGESTION_TEMPERATURE, default=self._get(CONF_SUGGESTION_TEMPERATURE, "")): cv.string,
                vol.Optional(CONF_SUGGESTION_PROMPT, default=self._get(CONF_SUGGESTION_PROMPT, "")): cv.string,
                vol.Optional(CONF_MAX_SUGGESTIONS, default=self._get(CONF_MAX_SUGGESTIONS, DEFAULT_MAX_SUGGESTIONS)): cv.positive_int,
                vol.Optional(CONF_CONFIG_MODEL, default=self._get(CONF_CONFIG_MODEL, "")): cv.string,
                vol.Optional(CONF_CONFIG_API_URL, default=self._get(CONF_CONFIG_API_URL, "")): cv.string,
                vol.Optional(CONF_CONFIG_API_KEY, default=self._get(CONF_CONFIG_API_KEY, "")): cv.string,
                vol.Optional(CONF_CONFIG_MAX_TOKENS, default=self._get(CONF_CONFIG_MAX_TOKENS, 0)): cv.positive_int,
                vol.Optional(CONF_SUGGESTION_USAGE_TRACKING, default=self._get(CONF_SUGGESTION_USAGE_TRACKING, "default")): vol.In(["default", "stream_options", "usage", "disabled"]),
                vol.Optional(CONF_CONFIG_USAGE_TRACKING, default=self._get(CONF_CONFIG_USAGE_TRACKING, "default")): vol.In(["default", "stream_options", "usage", "disabled"]),
            }),
        )

    # ── Step 3: Node-RED & Advanced ──────────────────────────────────────────

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3 — Node-RED integration and advanced settings."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(title="", data=self._options)

        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema({
                vol.Optional(CONF_NODERED_URL, default=self._get(CONF_NODERED_URL, "")): cv.string,
                vol.Optional(CONF_NODERED_TOKEN, default=self._get(CONF_NODERED_TOKEN, "")): cv.string,
                vol.Optional(CONF_NODERED_FLOWS_FILE, default=self._get(CONF_NODERED_FLOWS_FILE, "")): cv.string,
                vol.Optional(CONF_SYSTEM_PROMPT_FILE, default=self._get(CONF_SYSTEM_PROMPT_FILE, "")): cv.string,
            }),
        )
