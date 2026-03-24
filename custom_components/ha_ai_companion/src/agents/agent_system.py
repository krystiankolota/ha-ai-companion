import logging
import os
from typing import Dict, Any, Optional, List
from openai import AsyncOpenAI
from ..agents.tools import AgentTools
from ..config import ConfigurationManager
from ..memory.manager import MemoryManager
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict


logger = logging.getLogger(__name__)


@dataclass
class Changeset:
    """Represents a proposed set of configuration changes."""
    changeset_id: str
    file_changes: List[Dict[str, str]]  # List of {file_path, new_content}
    created_at: str
    expires_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentSystem:
    """
    Multi-agent system for Home Assistant configuration management.

    Uses OpenAI's GPT models to provide intelligent configuration assistance:
    - Understanding user requests
    - Reading and analyzing configuration
    - Proposing safe changes
    - Explaining configuration decisions
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        system_prompt: Optional[str] = None,
        enable_cache_control: bool = False,
        usage_tracking: str = 'stream_options',
        memory_manager: Optional[MemoryManager] = None,
    ):
        """
        Initialize the agent system.

        Args:
            config_manager: ConfigurationManager for file operations
            system_prompt: Optional custom system prompt. If not provided, uses default.
            enable_cache_control: Whether to enable cache control for API calls (default: False)
            usage_tracking: How to request usage tracking from the API:
                - 'stream_options': Use stream_options.include_usage (OpenAI format)
                - 'usage': Use usage.include (alternative format)
                - 'disabled': Don't request usage tracking
            memory_manager: Optional MemoryManager for persistent cross-session memories
        """
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.tools = AgentTools(config_manager, agent_system=self, memory_manager=memory_manager)

        # Initialize main OpenAI client
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("No OpenAI API key configured")
            self.client = None
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv('OPENAI_API_URL', 'https://api.openai.com/v1')
            )

        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o')

        # Suggestion model — independent provider, all 3 vars must be set to activate.
        # No fallback mixing: if any of the 3 are missing, main client+model is used.
        _s_model = os.getenv('SUGGESTION_MODEL')
        _s_url   = os.getenv('SUGGESTION_API_URL')
        _s_key   = os.getenv('SUGGESTION_API_KEY')
        if _s_model and _s_url and _s_key:
            self.suggestion_model = _s_model
            self.suggestion_client = AsyncOpenAI(api_key=_s_key, base_url=_s_url)
        else:
            self.suggestion_model = self.model
            self.suggestion_client = self.client

        # Config model — independent provider, all 3 vars must be set to activate.
        _c_model = os.getenv('CONFIG_MODEL')
        _c_url   = os.getenv('CONFIG_API_URL')
        _c_key   = os.getenv('CONFIG_API_KEY')
        if _c_model and _c_url and _c_key:
            self.config_model = _c_model
            self.config_client = AsyncOpenAI(api_key=_c_key, base_url=_c_url)
        else:
            self.config_model = self.model
            self.config_client = self.client

        # Get temperature from environment variable, use None if not specified
        temperature_str = os.getenv('TEMPERATURE')
        self.temperature = float(temperature_str) if temperature_str else None

        # Store cache control setting
        self.enable_cache_control = enable_cache_control

        # Store usage tracking mode
        self.usage_tracking = usage_tracking

        logger.info(f"AgentSystem initialized with model: {self.model}")
        if self.suggestion_model != self.model:
            logger.info(f"Suggestion model override: {self.suggestion_model}")
        if self.config_model != self.model:
            logger.info(f"Config model override: {self.config_model}")
        if self.temperature is not None:
            logger.info(f"Temperature: {self.temperature}")
        logger.info(f"Cache control: {'enabled' if self.enable_cache_control else 'disabled'}")
        logger.info(f"Usage tracking: {self.usage_tracking}")

        # In-memory storage for pending changesets
        self.pending_changesets: Dict[str, Changeset] = {}

        # System prompt for the configuration agent
        base_prompt = system_prompt or self._get_default_system_prompt()

        # Append user-defined suggestion prompt if configured
        suggestion_prompt = os.getenv('SUGGESTION_PROMPT', '').strip()
        if suggestion_prompt:
            base_prompt = base_prompt + "\n\nAdditional instructions for automation suggestions:\n" + suggestion_prompt
            logger.info(f"Appended custom suggestion prompt ({len(suggestion_prompt)} characters)")

        self.system_prompt = base_prompt
        if system_prompt:
            logger.info(f"Using custom system prompt ({len(system_prompt)} characters)")
        else:
            logger.info("Using default system prompt")

    def _get_default_system_prompt(self) -> str:
        """Get the default system prompt for the configuration agent."""
        return """You are a Home Assistant Configuration Assistant with persistent memory.

Your role is to help users manage their Home Assistant configuration files safely and effectively, suggest useful automations, and remember important facts about their setup across sessions.

Key Responsibilities:
1. **Understanding Requests**: Interpret user requests about Home Assistant configuration
2. **Reading Configuration**: Use tools to examine current configuration files
3. **Proposing Changes**: Suggest configuration changes with clear explanations using the propose_config_changes tool without requesting confirmation
4. **Safety First**: Always explain the impact of changes before proposing them
5. **Best Practices**: Guide users toward Home Assistant best practices
6. **Automation Suggestions**: Proactively suggest useful automations based on the user's devices and current states
7. **Memory Management**: Remember important facts about the user's setup across sessions

Available Tools:
- search_config_files: Search for terms in configuration files (use first when reading config). Includes all Lovelace dashboards as lovelace.yaml (default) and lovelace/{url_path}.yaml (custom).
- propose_config_changes: Propose file changes for user approval. Supports lovelace.yaml and lovelace/{url_path}.yaml for dashboard edits.
- list_dashboards: List all Lovelace dashboards with their url_path and virtual file path
- create_dashboard: Create a new Lovelace dashboard (returns url_path for editing)
- delete_dashboard: Delete a Lovelace dashboard by url_path
- get_entity_states: Get live current states of all entities (use for automation suggestions)
- get_nodered_flows: Read Node-RED flows exported to a JSON file (use when suggesting automations to avoid duplicating existing flows)
- read_memories: Read persistent memory files from previous sessions
- save_memory: Save a memory file to persist knowledge across sessions
- delete_memory: Delete an outdated memory file
- list_memory_stats: Audit memory files — sizes, ages, stale flags

Dashboard Guidelines:
- Call list_dashboards first to discover what dashboards exist and their url_path values
- The default dashboard is always available as lovelace.yaml
- Custom dashboards are available as lovelace/{url_path}.yaml (e.g. lovelace/kitchen.yaml)
- To review a dashboard: use search_config_files with 'lovelace' or call list_dashboards then search_config_files
- To edit a dashboard: read it via search_config_files, then propose_config_changes with the correct path
- To create a dashboard: call create_dashboard (returns url_path), then populate it via propose_config_changes
- To delete a dashboard: call delete_dashboard with the url_path (cannot delete the default dashboard)
- Dashboard YAML structure: must include at minimum 'title' and 'views' keys

Important Guidelines:
- NEVER suggest changes directly - always use propose_config_changes
- Always read the current configuration before proposing changes
- Explain your reasoning in your response when calling propose_config_changes
- The user can accept or reject your proposed config changes through their own UI
- Preserve all existing code, comments and structure when possible
- Only change what's needed to complete the request of the user
- Validate that changes align with Home Assistant documentation
- Warn users about potential breaking changes
- Remember when searching for files that terms are case-insensitive

Memory Guidelines:
- Categories: preference_, identity_, device_, baseline_, pattern_, correction_ (use as filename prefix)
- Examples: preference_lighting.md, device_nicknames.md, pattern_morning_routine.md

SAVE only when the user explicitly states a persistent fact:
- Preferences ("I prefer 22°C", "always dim at night")
- Device nicknames or locations ("Button 1 is the desk button")
- Room/home layout facts
- Baseline sensor norms ("100 ppm CO2 is normal here")
- Recurring routines or schedules
- Corrections to previously stored facts

DO NOT save — if in doubt, don't:
- Current states or live sensor readings (these change constantly)
- Actions just performed ("I turned on X")
- One-time commands (not stated as ongoing preference)
- Device specs, capabilities, or HA integration details
- Inferred or assumed facts the user never stated
- Single-event observations or troubleshooting notes
- Anything already derivable from the HA configuration

Anti-bloat rules (enforced by the system, also your responsibility):
- Max 25 files total — merge related facts into one file rather than creating many small ones
- Max 800 chars per file — be terse; bullet points only, no prose
- When updating a memory, overwrite the whole file — never append stale info
- Use the `replaces` field when correcting a previous memory to delete the old file atomically
- Call list_memory_stats periodically and proactively delete/merge stale or oversized files
- At session end: if you learned something new, save it; if something is now stale, delete it

Context injection:
- Memory is already injected into this prompt at session start — check it before calling read_memories
- Call read_memories only for a specific file not shown in the injected context

Automation Suggestion Guidelines:
- When asked to suggest automations, first call get_entity_states to see what devices exist
- Also call search_config_files to see what automations already exist (avoid duplicates)
- If Node-RED is configured, call get_nodered_flows to see existing flows — do NOT suggest automations that are already implemented in Node-RED
- Suggest practical, common-sense automations based on the devices present
- Group suggestions by area/domain and explain the benefit of each
- When Node-RED flows are available, mention whether a suggestion is best done in HA automations or Node-RED
- Offer to implement any suggestion via propose_config_changes

Response Style:
- Be concise but thorough
- Use technical terms appropriately
- Provide examples when helpful
- Format code blocks with YAML syntax
- Ask clarifying questions if request is ambiguous

Remember: You're helping manage a production Home Assistant system. Safety and clarity are paramount."""

    async def chat_stream(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Process a user message and stream response events in real-time.

        Args:
            user_message: The user's message/request
            conversation_history: Optional list of previous messages
                                Format: [{"role": "user"|"assistant", "content": "..."}]

        Yields:
            Dict events with:
                - event: "token" | "tool_call" | "tool_result" | "message_complete" | "complete" | "error"
                - data: JSON string with event-specific data

        Example:
            >>> async for event in agent_system.chat_stream("Enable debug logging"):
            ...     print(event)
        """
        import json

        if not self.client:
            yield {
                "event": "error",
                "data": json.dumps({
                    "error": "OpenAI API not configured. Please set OPENAI_API_KEY environment variable."
                })
            }
            return

        try:
            logger.info(f"Agent streaming user message: {user_message[:100]}...")

            # Build messages list with prompt caching support
            # Inject datetime + memory context into system prompt
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            system_content = self.system_prompt + f"\n\nCurrent date/time: {now_str}"
            if self.memory_manager:
                try:
                    memory_context = await self.memory_manager.get_context()
                    if memory_context:
                        system_content = system_content + "\n\n" + memory_context
                except Exception as mem_err:
                    logger.warning(f"Failed to load memory context: {mem_err}")

            system_message = {
                "role": "system",
                "content": system_content
            }
            if self.enable_cache_control:
                system_message["cache_control"] = {"type": "ephemeral"}
            messages = [system_message]

            # Track the starting point for new messages (after history)
            history_length = 1  # system message
            if conversation_history:
                # Add conversation history
                # Mark the last message in history for caching if there's substantial history
                for idx, msg in enumerate(conversation_history):
                    is_last_history_msg = (idx == len(conversation_history) - 1)
                    if self.enable_cache_control and is_last_history_msg and len(conversation_history) >= 3:
                        # Cache the conversation history at this breakpoint
                        msg_with_cache = dict(msg)
                        msg_with_cache["cache_control"] = {"type": "ephemeral"}
                        messages.append(msg_with_cache)
                    else:
                        messages.append(msg)
                history_length += len(conversation_history)

            # Add current user message
            messages.append({"role": "user", "content": user_message})

            # Define available tools for function calling with cache control
            # Mark tools for caching to reduce repeated processing
            propose_tool = {
                "type": "function",
                "function": {
                    "name": "propose_config_changes",
                    "description": "Propose changes to one or more configuration files for user approval. Use this to batch multiple file changes together. First use search_config_files to read files, then provide complete new content for each as YAML strings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "changes": {
                                "type": "array",
                                "description": "Array of file changes. Each change must include file_path and new_content.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "file_path": {
                                            "type": "string",
                                            "description": "Relative path to config file (e.g., 'configuration.yaml', 'switches.yaml'). New areas can be specified with 'areas/{area_id}.json' and must include the 'name'"
                                        },
                                        "new_content": {
                                            "type": "string",
                                            "description": "The complete new content of the file as a valid YAML string. Include all lines - both changed and unchanged."
                                        }
                                    },
                                    "required": ["file_path", "new_content"]
                                }
                            },
                        },
                        "required": ["changes"]
                    }
                }
            }
            if self.enable_cache_control:
                propose_tool["cache_control"] = {"type": "ephemeral"}

            dashboard_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "list_dashboards",
                        "description": "List all Lovelace dashboards. Returns url_path, title, icon, and virtual_file path for each. Call this before reading or editing a specific dashboard.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "create_dashboard",
                        "description": "Create a new Lovelace dashboard. After creation, populate it with propose_config_changes using 'lovelace/{url_path}.yaml' as the file_path.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Human-readable dashboard title (e.g. 'Kitchen Dashboard')"
                                },
                                "url_path": {
                                    "type": "string",
                                    "description": "URL slug for the dashboard (e.g. 'kitchen'). Auto-generated from title if omitted."
                                },
                                "icon": {
                                    "type": "string",
                                    "description": "Material Design icon (e.g. 'mdi:silverware-fork-knife')"
                                }
                            },
                            "required": ["title"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_dashboard",
                        "description": "Delete a Lovelace dashboard. Cannot delete the default dashboard.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url_path": {
                                    "type": "string",
                                    "description": "Dashboard URL slug to delete (e.g. 'kitchen')"
                                }
                            },
                            "required": ["url_path"]
                        }
                    }
                },
            ]

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_config_files",
                        "description": "Search configuration files (all YAML files + all Lovelace dashboards, plus device/entity/area virtual files). The default dashboard is returned as 'lovelace.yaml'; custom dashboards as 'lovelace/{url_path}.yaml'. Devices/entities/areas are ONLY included when search_pattern is provided.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "search_pattern": {
                                    "type": "string",
                                    "description": "Optional text to search for in file contents (case-insensitive). Only files containing this text will be returned. Omit to return all files."
                                }
                            },
                            "required": []
                        }
                    }
                },
                propose_tool,
                *dashboard_tools,
                {
                    "type": "function",
                    "function": {
                        "name": "get_nodered_flows",
                        "description": "Fetch Node-RED flows via the Node-RED Admin API (or file fallback). Use when suggesting automations to see what is already built in Node-RED and avoid duplicates. Returns flow tabs and nodes with their wiring.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_entity_states",
                        "description": "Get the current live states of all Home Assistant entities. Use this when suggesting automations or when the user asks about current device states. Returns entity_id, friendly_name, state value, attributes (truncated), area name, and last_changed timestamp for each entity.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "domain_filter": {
                                    "type": "string",
                                    "description": "Optional HA domain to limit results (e.g. 'light', 'switch', 'sensor', 'binary_sensor', 'climate', 'media_player'). Omit to get all entities."
                                }
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "read_memories",
                        "description": "Read persistent memory files saved from previous sessions. Always call this at the start of a session when context about the user's setup might help. Returns all memory files or a specific one.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Specific memory file to read (e.g. 'home_structure.md'). Omit to read all memory files."
                                }
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_memory",
                        "description": "Save or update a persistent memory file. Use category prefixes: preference_, device_, identity_, baseline_, pattern_, correction_. Only save persistent facts, not current states or command echoes. Use 'replaces' when correcting a previous memory.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Filename with category prefix (e.g. 'preference_lighting.md', 'device_nicknames.md', 'pattern_morning_routine.md'). Only letters, numbers, hyphens and underscores kept; .md forced."
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Full markdown content to store. Concise factual bullet points preferred."
                                },
                                "replaces": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional list of memory filenames this new entry supersedes (e.g. when the user corrects a preference). Those files are deleted atomically."
                                }
                            },
                            "required": ["filename", "content"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_memory",
                        "description": "Delete a memory file that is no longer accurate or relevant.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Name of the memory file to delete."
                                }
                            },
                            "required": ["filename"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_memory_stats",
                        "description": "Audit memory health: returns each file's name, size in chars, age in days, and a stale flag (true when age > 90 days). Call this periodically to identify files to prune, merge, or delete.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
            ]

            # Track tool calls and results
            new_messages = []

            # Loop to handle multiple rounds of tool calls
            max_iterations = 10
            iteration = 0

            # Track cumulative token usage across all iterations
            total_input_tokens = 0
            total_output_tokens = 0
            total_cached_tokens = 0

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[ITERATION {iteration}] Calling OpenAI streaming API")

                # Select client+model: config once tool results exist, suggestion before that.
                has_tool_results = any(m.get("role") == "tool" for m in messages)
                active_model  = self.config_model  if has_tool_results else self.suggestion_model
                active_client = self.config_client if has_tool_results else self.suggestion_client
                logger.debug(f"[ITERATION {iteration}] Using {'config' if has_tool_results else 'suggestion'} model: {active_model}")

                # Call OpenAI API with streaming
                api_params = {
                    "model": active_model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": True
                }

                # Add usage tracking based on configured mode
                if self.usage_tracking == 'stream_options':
                    api_params["stream_options"] = {"include_usage": True}
                elif self.usage_tracking == 'usage':
                    api_params["usage"] = {"include": True}
                # If disabled, don't add any usage tracking parameters

                # Add temperature if specified
                if self.temperature is not None:
                    api_params["temperature"] = self.temperature

                try:
                    stream = await active_client.chat.completions.create(**api_params)
                except Exception as api_err:
                    # Some providers (e.g. Anthropic/Haiku) reject stream_options or
                    # usage params — retry without them to keep things working.
                    err_str = str(api_err).lower()
                    if 'stream_options' in err_str or 'usage' in err_str or 'extra' in err_str or 'unknown' in err_str:
                        logger.warning(f"API rejected usage-tracking params, retrying without them: {api_err}")
                        retry_params = {k: v for k, v in api_params.items() if k not in ('stream_options', 'usage')}
                        stream = await active_client.chat.completions.create(**retry_params)
                    else:
                        raise

                # Accumulate the streaming response
                accumulated_content = ""
                accumulated_tool_calls = []
                current_tool_call = None
                tool_calls_announced = False
                tool_calls_pending_announced = False
                # Track token usage
                input_tokens = 0
                output_tokens = 0
                cached_tokens = 0

                async for chunk in stream:
                    # Guard: some providers (e.g. Anthropic/Haiku) send a final
                    # usage-only chunk with an empty choices array.
                    if not chunk.choices:
                        if self.usage_tracking != 'disabled' and hasattr(chunk, 'usage') and chunk.usage:
                            input_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or getattr(chunk.usage, 'input_tokens', 0)
                            output_tokens = getattr(chunk.usage, 'completion_tokens', 0) or getattr(chunk.usage, 'output_tokens', 0)
                            if hasattr(chunk.usage, 'cached_tokens'):
                                cached_tokens = chunk.usage.cached_tokens or 0
                            elif hasattr(chunk.usage, 'prompt_tokens_details') and chunk.usage.prompt_tokens_details:
                                cached_tokens = getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                            total_input_tokens += input_tokens
                            total_output_tokens += output_tokens
                            total_cached_tokens += cached_tokens
                        continue

                    delta = chunk.choices[0].delta

                    # Capture token usage if available (present in final chunk)
                    # Only attempt to parse if usage tracking is not disabled
                    if self.usage_tracking != 'disabled' and hasattr(chunk, 'usage') and chunk.usage:
                        input_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or getattr(chunk.usage, 'input_tokens', 0)
                        output_tokens = getattr(chunk.usage, 'completion_tokens', 0) or getattr(chunk.usage, 'output_tokens', 0)

                        # Check for cached tokens - supports multiple API formats
                        if hasattr(chunk.usage, 'cached_tokens'):
                            cached_tokens = chunk.usage.cached_tokens or 0
                        elif hasattr(chunk.usage, 'prompt_tokens_details') and chunk.usage.prompt_tokens_details:
                            cached_tokens = getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                        elif hasattr(chunk.usage, 'cached_content_token_count'):
                            cached_tokens = chunk.usage.cached_content_token_count or 0

                        logger.debug(f"[USAGE] Parsed - Input: {input_tokens}, Output: {output_tokens}, Cached: {cached_tokens}")

                        # Accumulate totals
                        total_input_tokens += input_tokens
                        total_output_tokens += output_tokens
                        total_cached_tokens += cached_tokens

                    # Stream content tokens
                    if delta.content:
                        accumulated_content += delta.content
                        logger.debug(f"[STREAM] Yielding token: {delta.content[:50]}")
                        yield {
                            "event": "token",
                            "data": json.dumps({
                                "content": delta.content,
                                "iteration": iteration
                            })
                        }

                    # Handle tool calls
                    if delta.tool_calls:
                        for tool_call_delta in delta.tool_calls:
                            # Initialize new tool call
                            # Handle index if provided (OpenAI format), otherwise default to 0 (Google format)
                            index = tool_call_delta.index if tool_call_delta.index is not None else 0

                            # Ensure we have a slot for this tool call
                            while len(accumulated_tool_calls) <= index:
                                accumulated_tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                })

                            current_tool_call = accumulated_tool_calls[index]

                            # Update tool call details
                            if tool_call_delta.id:
                                current_tool_call["id"] = tool_call_delta.id
                            if tool_call_delta.function:
                                if tool_call_delta.function.name:
                                    current_tool_call["function"]["name"] = tool_call_delta.function.name
                                if tool_call_delta.function.arguments:
                                    current_tool_call["function"]["arguments"] += tool_call_delta.function.arguments

                        # Announce tool calls to UI as soon as we know them (may have partial arguments)
                        if not tool_calls_announced and any(tc.get("function", {}).get("name") for tc in accumulated_tool_calls):
                            yield {
                                "event": "tool_call",
                                "data": json.dumps({
                                    "tool_calls": accumulated_tool_calls,
                                    "iteration": iteration
                                })
                            }
                            tool_calls_announced = True

                    # Check for finish reason
                    if chunk.choices[0].finish_reason:
                        break

                # Check if we have tool calls
                if not accumulated_tool_calls:
                    # No tool calls - final response
                    logger.info(f"[ITERATION {iteration}] No tool calls, final response received")

                    # Send message complete event with full message data
                    assistant_message = {
                        "role": "assistant",
                        "content": accumulated_content
                    }
                    new_messages.append(assistant_message)

                    yield {
                        "event": "message_complete",
                        "data": json.dumps({
                            "message": assistant_message,
                            "iteration": iteration,
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cached_tokens": cached_tokens,
                                "total_tokens": input_tokens + output_tokens
                            }
                        })
                    }
                    break

                # We have tool calls - add assistant message to history
                logger.info(f"[ITERATION {iteration}] Processing {len(accumulated_tool_calls)} tool call(s)")

                assistant_message = {
                    "role": "assistant",
                    "content": accumulated_content,
                    "tool_calls": accumulated_tool_calls
                }
                messages.append(assistant_message)
                new_messages.append(assistant_message)

                # Notify about ALL tool calls upfront before executing any (only if not already announced)
                if not tool_calls_announced:
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "tool_calls": accumulated_tool_calls,
                            "iteration": iteration
                        })
                    }
                    tool_calls_announced = True

                # Execute each tool call and stream results immediately
                for tool_idx, tool_call in enumerate(accumulated_tool_calls):
                    function_name = tool_call["function"]["name"]
                    function_args = json.loads(tool_call["function"]["arguments"])

                    logger.info(f"[ITERATION {iteration}] Calling tool: {function_name}")

                    # Send individual tool execution start event
                    yield {
                        "event": "tool_start",
                        "data": json.dumps({
                            "tool_call_id": tool_call["id"],
                            "function": function_name,
                            "arguments": function_args,
                            "iteration": iteration
                        })
                    }

                    # Execute the tool function
                    if function_name == "search_config_files":
                        result = await self.tools.search_config_files(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, file_count={result.get('count')}")
                    elif function_name == "propose_config_changes":
                        if "changes" not in function_args or not isinstance(function_args["changes"], list):
                            error_msg = (
                                "ERROR: propose_config_changes requires a 'changes' parameter with a list of file changes. "
                                "Each change must have 'file_path' and 'new_content'. "
                                "You MUST first read files with search_config_files, then provide all modified content. "
                                f"Received args: {function_args}"
                            )
                            logger.error(error_msg)
                            result = {"success": False, "error": error_msg}
                        else:
                            result = await self.tools.propose_config_changes(**function_args)
                            logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, changeset_id={result.get('changeset_id')}")
                    elif function_name == "list_dashboards":
                        result = await self.tools.list_dashboards()
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, count={result.get('count')}")
                    elif function_name == "create_dashboard":
                        result = await self.tools.create_dashboard(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, url_path={result.get('url_path')}")
                    elif function_name == "delete_dashboard":
                        result = await self.tools.delete_dashboard(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}")
                    elif function_name == "get_nodered_flows":
                        result = await self.tools.get_nodered_flows()
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, count={result.get('count')}")
                    elif function_name == "get_entity_states":
                        result = await self.tools.get_entity_states(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, count={result.get('count')}")
                    elif function_name == "read_memories":
                        result = await self.tools.read_memories(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, count={result.get('count')}")
                    elif function_name == "save_memory":
                        result = await self.tools.save_memory(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}, filename={result.get('filename')}")
                    elif function_name == "delete_memory":
                        result = await self.tools.delete_memory(**function_args)
                        logger.info(f"[ITERATION {iteration}] Tool result: success={result.get('success')}")
                    elif function_name == "list_memory_stats":
                        result = await self.tools.list_memory_stats()
                        logger.info(f"[ITERATION {iteration}] Tool result: total={result.get('total')}")
                    else:
                        result = {"success": False, "error": f"Unknown tool: {function_name}"}
                        logger.error(f"[ITERATION {iteration}] Unknown tool requested: {function_name}")

                    # Add tool result to messages with cache control on the last tool result
                    is_last_tool = (tool_idx == len(accumulated_tool_calls) - 1)
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result)
                    }
                    # Mark the last tool result for caching to preserve full context
                    if self.enable_cache_control and is_last_tool:
                        tool_message["cache_control"] = {"type": "ephemeral"}

                    messages.append(tool_message)
                    new_messages.append(tool_message)

                    # Notify about tool result immediately after execution
                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "tool_call_id": tool_call["id"],
                            "function": function_name,
                            "result": result,
                            "iteration": iteration
                        })
                    }

            # Send completion event with all new messages
            if iteration >= max_iterations:
                logger.warning(f"Hit max iterations ({max_iterations}), stopping")
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "error": "Maximum iteration limit reached. Please try breaking down your request."
                    })
                }

            logger.info(f"Agent completed after {iteration} iteration(s) - Total tokens: {total_input_tokens + total_output_tokens} (input: {total_input_tokens}, output: {total_output_tokens}, cached: {total_cached_tokens})")

            yield {
                "event": "complete",
                "data": json.dumps({
                    "messages": new_messages,
                    "iterations": iteration,
                    "usage": {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "cached_tokens": total_cached_tokens,
                        "total_tokens": total_input_tokens + total_output_tokens
                    }
                })
            }

        except Exception as e:
            logger.error(f"Agent streaming error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }

    def store_changeset(self, changeset_data: Dict[str, Any]) -> str:
        """
        Store a changeset for later approval.

        Args:
            changeset_data: Dictionary with file_changes and changeset_id

        Returns:
            changeset_id
        """
        import uuid
        changeset_id = changeset_data.get('changeset_id') or str(uuid.uuid4())[:8]

        now = datetime.now()
        changeset = Changeset(
            changeset_id=changeset_id,
            file_changes=changeset_data['file_changes'],
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat()
        )

        self.pending_changesets[changeset_id] = changeset
        logger.info(f"Stored changeset {changeset_id} with {len(changeset.file_changes)} file(s)")
        return changeset_id

    async def process_approval(
        self,
        change_id: str,
        approved: bool,
        validate: bool = True
    ) -> Dict[str, Any]:
        """
        Process user's approval/rejection of proposed changes.

        Args:
            change_id: Unique identifier for the proposed change
            approved: Whether user approved the changes
            validate: Whether to validate after applying changes

        Returns:
            Dict with:
                - success: bool
                - applied: bool
                - message: str
                - error: Optional[str]
        """
        logger.info(f"Processing approval for change {change_id}: {'approved' if approved else 'rejected'}")

        # Check if changeset exists
        changeset = self.pending_changesets.get(change_id)
        if not changeset:
            return {
                "success": False,
                "applied": False,
                "message": f"Changeset {change_id} not found or has expired"
            }

        # If rejected, just remove and return
        if not approved:
            del self.pending_changesets[change_id]
            return {
                "success": True,
                "applied": False,
                "message": "Changes rejected by user"
            }

        # Check if expired
        expires_at = datetime.fromisoformat(changeset.expires_at)
        if datetime.now() > expires_at:
            del self.pending_changesets[change_id]
            return {
                "success": False,
                "applied": False,
                "message": "Changeset has expired. Please re-propose the changes."
            }

        # Apply changes
        try:
            applied_files = []
            failed_files = []

            # Step 1: Write all files first (without validation)
            for file_change in changeset.file_changes:
                file_path = file_change['file_path']
                new_content = file_change['new_content']

                try:
                    await self.config_manager.write_file_raw(
                        file_path=file_path,
                        content=new_content,
                        create_backup=True
                    )
                    applied_files.append(file_path)
                    logger.info(f"Applied changes to {file_path}")
                except Exception as e:
                    logger.error(f"Failed to apply changes to {file_path}: {e}")
                    failed_files.append({"file_path": file_path, "error": str(e)})

            # Step 2: If validation requested and files were written, validate all at once
            validation_failed = False
            if validate and applied_files:
                try:
                    logger.info("Validating configuration after writing all files...")
                    await self.config_manager.validate_config()
                    logger.info("Configuration validation passed")
                except Exception as e:
                    logger.error(f"Configuration validation failed: {e}")
                    validation_failed = True
                    # Note: We don't rollback here because backups were created
                    # Users can manually restore from backups if needed
                    failed_files.append({
                        "file_path": "validation",
                        "error": f"Configuration validation failed: {str(e)}"
                    })

            # Remove changeset from pending
            del self.pending_changesets[change_id]

            # Reload Home Assistant configuration after successful changes (only if validation passed)
            reload_success = False
            if applied_files and not validation_failed:
                try:
                    from ..ha.ha_websocket import reload_homeassistant_config

                    supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                    if supervisor_token:
                        ws_url = "ws://supervisor/core/websocket"
                        await reload_homeassistant_config(ws_url, supervisor_token)
                        reload_success = True
                        logger.info("Home Assistant configuration reloaded successfully")
                    else:
                        logger.warning("SUPERVISOR_TOKEN not available, skipping config reload")
                except Exception as e:
                    logger.warning(f"Failed to reload Home Assistant config: {e}")

            if failed_files:
                return {
                    "success": True,
                    "applied": True,
                    "message": f"Partially applied: {len(applied_files)} succeeded, {len(failed_files)} failed",
                    "applied_files": applied_files,
                    "failed_files": failed_files,
                    "config_reloaded": reload_success
                }
            else:
                message = f"Successfully applied changes to {len(applied_files)} file(s)"
                if reload_success:
                    message += " and reloaded Home Assistant configuration"
                return {
                    "success": True,
                    "applied": True,
                    "message": message,
                    "applied_files": applied_files,
                    "config_reloaded": reload_success
                }

        except Exception as e:
            logger.error(f"Error applying changeset: {e}", exc_info=True)
            return {
                "success": False,
                "applied": False,
                "message": f"Error applying changes: {str(e)}"
            }

    async def generate_suggestions(self) -> Dict[str, Any]:
        """
        Generate automation suggestions by pre-fetching context and making a single AI call.

        Fetches entity states, existing automations, and Node-RED flows, then asks
        the suggestion_model to return structured JSON suggestions.

        Returns:
            Dict with 'suggestions' list, each item having:
              - title: str
              - description: str
              - category: str (e.g. 'lighting', 'climate', 'security', 'energy', 'comfort')
              - entities: list[str]  (entity_ids involved)
              - implementation_hint: str
        """
        import json

        if not self.client:
            return {"success": False, "error": "OpenAI API not configured"}

        try:
            logger.info("Generating automation suggestions")

            # 1. Gather context
            entity_states_result = await self.tools.get_entity_states()
            entity_states_text = json.dumps(entity_states_result.get("states", []), indent=2)

            automations_result = await self.tools.search_config_files(search_pattern="automation")
            automations_text = json.dumps(automations_result.get("files", []), indent=2)

            nodered_text = ""
            try:
                nodered_result = await self.tools.get_nodered_flows()
                if nodered_result.get("success"):
                    nodered_text = json.dumps(nodered_result.get("flows", []), indent=2)
            except Exception:
                pass

            # 2. Build prompt
            suggestion_prompt_extra = os.getenv("SUGGESTION_PROMPT", "").strip()
            context_sections = [
                f"## Current entity states\n{entity_states_text}",
                f"## Existing automations\n{automations_text}",
            ]
            if nodered_text:
                context_sections.append(f"## Existing Node-RED flows\n{nodered_text}")

            # Inject dismissed suggestions so the AI won't re-suggest them
            dismissed = []
            try:
                config_dir = os.getenv("HA_CONFIG_DIR", "/config")
                dismissed_path = os.path.join(config_dir, ".ai_agent_suggestions_dismissed.json")
                if os.path.exists(dismissed_path):
                    with open(dismissed_path, "r") as f:
                        dismissed = json.load(f)
            except Exception:
                pass
            if dismissed:
                dismissed_list = "\n".join(f"- {t}" for t in dismissed)
                context_sections.append(f"## User has dismissed these suggestions — do NOT suggest them again\n{dismissed_list}")

            if suggestion_prompt_extra:
                context_sections.append(f"## Additional context\n{suggestion_prompt_extra}")

            context = "\n\n".join(context_sections)

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Home Assistant automation expert. "
                        "Based on the provided entity states and existing automations, "
                        "suggest 5-10 practical automations the user does not already have. "
                        "Do NOT suggest automations that already exist in the automation list or Node-RED flows. "
                        "Return ONLY valid JSON — an object with a 'suggestions' array. "
                        "Each suggestion must have: title (string), description (string), "
                        "category (one of: lighting, climate, security, energy, comfort, other), "
                        "entities (array of entity_id strings), implementation_hint (string with brief YAML hint)."
                    )
                },
                {
                    "role": "user",
                    "content": context
                }
            ]

            api_params = {
                "model": self.suggestion_model,
                "messages": messages,
                "stream": False,
            }
            if self.temperature is not None:
                api_params["temperature"] = self.temperature

            response = await self.client.chat.completions.create(**api_params)
            raw = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)
            suggestions = parsed.get("suggestions", parsed) if isinstance(parsed, dict) else parsed

            logger.info(f"Generated {len(suggestions)} automation suggestions")
            return {"success": True, "suggestions": suggestions}

        except Exception as e:
            logger.error(f"Failed to generate suggestions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
