"""Tests for HACS services without a Home Assistant installation."""
import asyncio
import importlib.util
import json
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import aiohttp
import pytest


class SupportsResponse(Enum):
    NONE = "none"
    OPTIONAL = "optional"


class HomeAssistantError(Exception):
    pass


class FakeServices:
    def __init__(self):
        self.handlers = {}

    def async_register(self, domain, name, handler, **kwargs):
        self.handlers[(domain, name)] = (handler, kwargs)


class MissingEndpoint:
    status = 404

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def text(self):
        return "Not Found"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, *args, **kwargs):
        return MissingEndpoint()


def mock_approval_response(monkeypatch, result, status=200):
    calls = []

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            if isinstance(result, Exception):
                raise result
            return result

        async def text(self):
            return str(result) if isinstance(result, Exception) else json.dumps(result)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            response = Response()
            response.status = status
            return response

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    return calls


@pytest.fixture
def integration(monkeypatch):
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    modules = {
        name: ModuleType(name)
        for name in (
            "homeassistant",
            "homeassistant.config_entries",
            "homeassistant.const",
            "homeassistant.core",
            "homeassistant.exceptions",
            "homeassistant.helpers",
            "homeassistant.helpers.config_validation",
            "voluptuous",
        )
    }
    modules["homeassistant"].__path__ = []
    modules["homeassistant.helpers"].__path__ = []
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].Platform = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].ServiceCall = object
    modules["homeassistant.core"].SupportsResponse = SupportsResponse
    modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantError
    cv = modules["homeassistant.helpers.config_validation"]
    cv.config_entry_only_config_schema = lambda domain: None
    cv.string = str
    cv.boolean = bool
    modules["homeassistant.helpers"].config_validation = cv
    vol = modules["voluptuous"]
    vol.Required = lambda key: key
    vol.Optional = lambda key, default=None: key
    vol.Schema = lambda schema: schema
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    component = Path(__file__).resolve().parents[2] / "custom_components" / "ha_ai_companion"
    package_name = "_test_ha_ai_companion"
    const_spec = importlib.util.spec_from_file_location(f"{package_name}.const", component / "const.py")
    constants = importlib.util.module_from_spec(const_spec)
    const_spec.loader.exec_module(constants)
    monkeypatch.setitem(sys.modules, constants.__name__, constants)
    spec = importlib.util.spec_from_file_location(
        package_name, component / "__init__.py", submodule_search_locations=[str(component)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, package_name, module)
    spec.loader.exec_module(module)

    backend = ModuleType(f"{package_name}.src.main")
    backend.agent_system = object()
    src = ModuleType(f"{package_name}.src")
    src.__path__ = []
    src.main = backend
    monkeypatch.setitem(sys.modules, src.__name__, src)
    monkeypatch.setitem(sys.modules, backend.__name__, backend)

    hass = SimpleNamespace(
        data={module.DOMAIN: {"entry": {"port": 1234}}},
        services=FakeServices(),
    )
    return SimpleNamespace(module=module, backend=backend, hass=hass)


async def service_handler(integration, name):
    await integration.module._register_services(
        integration.hass, SimpleNamespace(entry_id="entry")
    )
    return integration.hass.services.handlers[(integration.module.DOMAIN, name)]


async def chat_handler(integration):
    return await service_handler(integration, "chat")


@pytest.mark.asyncio
async def test_chat_service_supports_optional_response(integration):
    _, options = await chat_handler(integration)
    assert options.get("supports_response") is SupportsResponse.OPTIONAL


@pytest.mark.asyncio
async def test_approval_service_supports_optional_response(integration):
    _, options = await service_handler(integration, "approve")
    assert options.get("supports_response") is SupportsResponse.OPTIONAL


@pytest.mark.asyncio
@pytest.mark.parametrize("return_response", [True, False])
@pytest.mark.parametrize("approved", [True, False])
async def test_approval_returns_success_or_rejection(integration, monkeypatch, approved, return_response):
    result = {
        "success": True,
        "applied": approved,
        "message": "Applied" if approved else "Changes rejected by user",
    }
    calls = mock_approval_response(monkeypatch, result)
    handler, _ = await service_handler(integration, "approve")
    response = await handler(
        SimpleNamespace(
            data={"change_id": "change-1", "approved": approved, "validate": False},
            return_response=return_response,
        )
    )

    assert calls == [(
        "http://localhost:1234/api/approve",
        {"change_id": "change-1", "approved": approved, "validate": False},
    )]
    assert response == (result if return_response else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("return_response", [True, False])
@pytest.mark.parametrize(
    ("approved", "result", "details"),
    [
        (
            True,
            {"success": False, "applied": False, "message": "Changeset expired"},
            ["Changeset expired"],
        ),
        (
            False,
            {"success": False, "applied": False, "message": "Changeset not found"},
            ["Changeset not found"],
        ),
        (
            True,
            {"success": True, "applied": False, "message": "Changes were not applied"},
            ["Changes were not applied"],
        ),
        (
            True,
            {
                "success": True,
                "applied": True,
                "message": "Partially applied",
                "applied_files": ["written.yaml"],
                "failed_files": [{"file_path": "failed.yaml", "error": "changed since proposal"}],
            },
            ["written.yaml", "failed.yaml", "changed since proposal"],
        ),
        (
            True,
            {
                "success": True,
                "applied": True,
                "message": "Partially applied",
                "applied_files": ["written.yaml"],
                "failed_files": [{"file_path": "validation", "error": "invalid configuration"}],
            },
            ["written.yaml", "validation", "invalid configuration"],
        ),
        (
            True,
            {
                "success": True,
                "applied": True,
                "message": "Files written, reload failed",
                "applied_files": ["written.yaml"],
                "reload_error": "reload rejected",
            },
            ["written.yaml", "reload rejected"],
        ),
    ],
)
async def test_approval_failures_stop_action(integration, monkeypatch, approved, result, details, return_response):
    mock_approval_response(monkeypatch, result)
    handler, _ = await service_handler(integration, "approve")
    with pytest.raises(HomeAssistantError) as error:
        await handler(
            SimpleNamespace(
                data={"change_id": "change-1", "approved": approved},
                return_response=return_response,
            )
        )
    for detail in details:
        assert detail in str(error.value)


@pytest.mark.asyncio
async def test_approval_http_error_reports_uncertain_application(integration, monkeypatch):
    mock_approval_response(monkeypatch, {"detail": "Backend unavailable"}, status=503)
    handler, _ = await service_handler(integration, "approve")
    with pytest.raises(HomeAssistantError) as error:
        await handler(SimpleNamespace(data={"change_id": "change-1"}, return_response=True))
    assert "503" in str(error.value)
    assert "Backend unavailable" in str(error.value)
    assert "may have been applied" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError("offline"), asyncio.TimeoutError()])
async def test_approval_connection_error_reports_uncertain_application(integration, monkeypatch, failure):
    class DisconnectedSession(FakeSession):
        def post(self, *args, **kwargs):
            raise failure

    monkeypatch.setattr(aiohttp, "ClientSession", DisconnectedSession)
    handler, _ = await service_handler(integration, "approve")
    with pytest.raises(HomeAssistantError, match="may have been applied"):
        await handler(SimpleNamespace(data={"change_id": "change-1"}, return_response=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [["not a mapping"], ValueError("invalid JSON")])
async def test_approval_rejects_invalid_backend_result(integration, monkeypatch, result):
    mock_approval_response(monkeypatch, result)
    handler, _ = await service_handler(integration, "approve")
    with pytest.raises(HomeAssistantError, match="may have been applied"):
        await handler(SimpleNamespace(data={"change_id": "change-1"}, return_response=True))


@pytest.mark.asyncio
async def test_approval_logs_warnings_when_no_response_is_requested(integration, monkeypatch, caplog):
    result = {
        "success": True,
        "applied": True,
        "message": "Applied",
        "applied_files": ["written.yaml"],
        "config_reloaded": False,
        "validation_warnings": "Existing configuration warning",
        "restart_required": ["sensor"],
    }
    mock_approval_response(monkeypatch, result)
    handler, _ = await service_handler(integration, "approve")
    assert await handler(
        SimpleNamespace(data={"change_id": "change-1"}, return_response=False)
    ) is None
    assert "Existing configuration warning" in caplog.text
    assert "sensor" in caplog.text
    assert "not reloaded" in caplog.text


@pytest.mark.asyncio
async def test_chat_service_returns_reply_history_and_pending_proposals(integration, monkeypatch):
    from src import main

    history = [{"role": "user", "content": "Earlier question"}]
    messages = [{"role": "assistant", "content": "A proposal is ready."}]
    proposal = {"success": True, "changeset_id": "change-1"}
    calls = []
    finished = []

    class Agent:
        async def chat_stream(self, user_message, conversation_history, session_id=None, images=None):
            calls.append((user_message, conversation_history))
            yield {"event": "tool_result", "data": json.dumps({"result": proposal})}
            yield {"event": "message_complete", "data": json.dumps({"message": messages[0]})}
            yield {
                "event": "complete",
                "data": json.dumps({"messages": messages, "usage": {"input_tokens": 10}}),
            }
            finished.append(True)

    agent = Agent()
    monkeypatch.setattr(main, "agent_system", agent)
    integration.backend.agent_system = agent
    integration.backend._agent_event_stream = main._agent_event_stream
    handler, _ = await chat_handler(integration)
    result = await handler(
        SimpleNamespace(
            data={"message": "Prepare a change", "conversation_history": history},
            return_response=True,
        )
    )

    assert calls == [("Prepare a change", history)]
    assert result["response"] == "A proposal is ready."
    assert result["messages"] == messages
    assert result["changesets"] == [proposal]
    assert result["truncated"] is False
    assert finished == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("return_response", [True, False])
async def test_chat_service_preserves_truncation_and_optional_response(integration, return_response):
    async def stream(*args):
        yield {
            "event": "complete",
            "data": {
                "messages": [{"role": "assistant", "content": "Partial answer"}],
                "truncated": True,
            },
        }

    integration.backend._agent_event_stream = stream
    handler, _ = await chat_handler(integration)
    result = await handler(
        SimpleNamespace(data={"message": "Question"}, return_response=return_response)
    )

    if return_response:
        assert result["response"] == "Partial answer"
        assert result["truncated"] is True
    else:
        assert result is None


@pytest.mark.asyncio
async def test_chat_service_reports_agent_error_and_pending_proposals(integration):
    async def stream(*args):
        yield {
            "event": "tool_result",
            "data": {"result": {"success": True, "changeset_id": "change-1"}},
        }
        yield {"event": "error", "data": {"error": "Provider unavailable"}}
        yield {
            "event": "complete",
            "data": {"messages": [{"role": "assistant", "content": "Not a success"}]},
        }

    integration.backend._agent_event_stream = stream
    handler, _ = await chat_handler(integration)
    with pytest.raises(HomeAssistantError, match="Provider unavailable.*change-1"):
        await handler(SimpleNamespace(data={"message": "Question"}, return_response=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "error"),
    [
        ([{"event": "heartbeat", "data": {}}], "valid completion"),
        ([{"event": "complete", "data": {"messages": []}}], "final assistant reply"),
    ],
)
async def test_chat_service_rejects_incomplete_results(integration, events, error):
    async def stream(*args):
        for event in events:
            yield event

    integration.backend._agent_event_stream = stream
    handler, _ = await chat_handler(integration)
    with pytest.raises(HomeAssistantError, match=error):
        await handler(SimpleNamespace(data={"message": "Question"}, return_response=True))


@pytest.mark.asyncio
async def test_chat_service_rejects_uninitialized_agent(integration):
    integration.backend.agent_system = None
    handler, _ = await chat_handler(integration)
    with pytest.raises(HomeAssistantError, match="not initialized"):
        await handler(SimpleNamespace(data={"message": "Question"}, return_response=True))


@pytest.mark.asyncio
async def test_chat_service_cancellation_closes_agent_stream(integration, monkeypatch):
    from src import main

    started = asyncio.Event()
    closed = asyncio.Event()

    class Agent:
        async def chat_stream(self, **kwargs):
            try:
                started.set()
                await asyncio.Event().wait()
                yield {"event": "complete", "data": json.dumps({"messages": []})}
            finally:
                closed.set()

    agent = Agent()
    monkeypatch.setattr(main, "agent_system", agent)
    integration.backend.agent_system = agent
    integration.backend._agent_event_stream = main._agent_event_stream
    handler, _ = await chat_handler(integration)
    task = asyncio.create_task(
        handler(SimpleNamespace(data={"message": "Question"}, return_response=True))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
