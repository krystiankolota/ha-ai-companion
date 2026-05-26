"""
Tests for fetch_url and learn_hacs_component tools.

Covers:
- fetch_url: domain allowlist, happy path, truncation, HTTP errors, timeout, binary content
- learn_hacs_component: freshness cache, HACS store lookup, fetch pipeline,
  graceful degradation (no CHANGELOG, no examples, rate-limited)
"""
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.tools import AgentTools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tools(memory_dir=None):
    """Build AgentTools with minimal mocked dependencies."""
    tools = AgentTools.__new__(AgentTools)
    tools.config_manager = None
    tools.agent_system = None
    tools._lovelace_cache = {}
    tools._turn_cache = {}
    tools.conversation_manager = None

    if memory_dir is not None:
        from src.memory.manager import MemoryManager
        tools.memory_manager = MemoryManager(memory_dir=str(memory_dir))
    else:
        tools.memory_manager = None

    return tools


def mock_aiohttp_response(status=200, text="content", content_type="text/plain"):
    """Build a mock aiohttp response context manager."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.text = AsyncMock(return_value=text)

    resp_ctx = MagicMock()
    resp_ctx.__aenter__ = AsyncMock(return_value=resp)
    resp_ctx.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=resp_ctx)

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    return session_ctx, session, resp


# ---------------------------------------------------------------------------
# fetch_url — domain allowlist
# ---------------------------------------------------------------------------

class TestFetchUrlAllowlist:
    async def test_blocked_domain_no_http_call(self):
        tools = make_tools()
        with patch("aiohttp.ClientSession") as mock_cls:
            result = await tools.fetch_url("https://evil.com/file.txt")
        assert result["success"] is False
        assert "not allowed" in result["error"].lower()
        mock_cls.assert_not_called()

    async def test_raw_githubusercontent_allowed(self):
        tools = make_tools()
        session_ctx, session, _ = mock_aiohttp_response(200, "readme content")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/owner/repo/main/README.md")
        assert result["success"] is True
        assert result["content"] == "readme content"

    async def test_api_github_allowed(self):
        tools = make_tools()
        session_ctx, session, _ = mock_aiohttp_response(200, '{"items":[]}', "application/json")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://api.github.com/repos/owner/repo/contents/examples")
        assert result["success"] is True

    async def test_data_home_assistant_io_allowed(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(200, '{"items":[]}', "application/json")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://data.home-assistant.io/custom_components.json")
        assert result["success"] is True

    async def test_github_com_allowed(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(200, "page content")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://github.com/owner/repo")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# fetch_url — happy path + truncation
# ---------------------------------------------------------------------------

class TestFetchUrlHappyPath:
    async def test_returns_content(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(200, "hello world")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/x/y/main/f.md")
        assert result["success"] is True
        assert result["content"] == "hello world"
        assert result["truncated"] is False
        assert "raw.githubusercontent.com" in result["url"]

    async def test_truncates_at_max_chars(self):
        tools = make_tools()
        long_text = "a" * 10_000
        session_ctx, _, _ = mock_aiohttp_response(200, long_text)
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/x/y/main/f.md", max_chars=4000)
        assert result["success"] is True
        assert result["truncated"] is True
        assert len(result["content"]) > 4000  # includes truncation notice
        assert "truncated" in result["content"]
        assert result["content"][:4000] == long_text[:4000]

    async def test_max_chars_capped_at_8000(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(200, "x" * 100)
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url(
                "https://raw.githubusercontent.com/x/y/main/f.md", max_chars=99999
            )
        # Shouldn't error — cap is silently applied
        assert result["success"] is True


# ---------------------------------------------------------------------------
# fetch_url — error paths
# ---------------------------------------------------------------------------

class TestFetchUrlErrors:
    async def test_http_404(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(404, "not found")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/x/y/main/missing.md")
        assert result["success"] is False
        assert "404" in result["error"]

    async def test_http_403(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(403, "rate limited")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://api.github.com/repos/x/y/contents/examples")
        assert result["success"] is False
        assert "403" in result["error"]

    async def test_timeout_returns_error_string(self):
        import aiohttp as _aiohttp
        tools = make_tools()

        resp_ctx = MagicMock()
        resp_ctx.__aenter__ = AsyncMock(side_effect=_aiohttp.ServerTimeoutError())
        resp_ctx.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=resp_ctx)
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/x/y/main/f.md")
        assert result["success"] is False
        assert isinstance(result["error"], str)

    async def test_binary_content_type_rejected(self):
        tools = make_tools()
        session_ctx, _, _ = mock_aiohttp_response(200, b"binary", "image/png")
        with patch("aiohttp.ClientSession", return_value=session_ctx):
            result = await tools.fetch_url("https://raw.githubusercontent.com/x/y/main/img.png")
        assert result["success"] is False
        assert "non-text" in result["error"].lower()

    async def test_invalid_url_returns_error(self):
        tools = make_tools()
        result = await tools.fetch_url("not-a-url")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# learn_hacs_component — slug generation
# ---------------------------------------------------------------------------

class TestLearnHacsSlug:
    async def test_slug_from_hyphenated_name(self):
        tools = make_tools()
        # mock fetch_url to return not_found so we only test slug logic
        async def _fake_fetch(url, max_chars=4000):
            if "data.home-assistant.io" in url or "hacs/default" in url:
                return {"success": True, "content": "[]"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result.get("slug") == "bubble-card"

    async def test_slug_from_spaced_name(self):
        tools = make_tools()
        async def _fake_fetch(url, max_chars=4000):
            return {"success": True, "content": "[]"}
        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Mini Graph Card")
        assert result.get("slug") == "mini-graph-card"

    async def test_slug_stable_already_hyphenated(self):
        tools = make_tools()
        async def _fake_fetch(url, max_chars=4000):
            return {"success": True, "content": "[]"}
        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="mini-graph-card")
        assert result.get("slug") == "mini-graph-card"


# ---------------------------------------------------------------------------
# learn_hacs_component — freshness cache
# ---------------------------------------------------------------------------

class TestLearnHacsFreshness:
    async def test_cached_if_files_young(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)
        # Write a fresh pattern file
        f = tmp_path / "pattern_bubble-card_syntax.md"
        f.write_text("some syntax")
        # mtime is current (just created) — should be cached

        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result["status"] == "cached"
        assert "bubble-card" in result["slug"]
        assert "age_days" in result
        assert result["age_days"] < 1

    async def test_not_cached_if_files_old(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)
        f = tmp_path / "pattern_bubble-card_syntax.md"
        f.write_text("old syntax")
        # Backdate mtime to 40 days ago
        old_ts = time.time() - 40 * 86400
        os.utime(f, (old_ts, old_ts))

        # Mock fetch_url to prevent real HTTP calls
        async def _fake_fetch(url, max_chars=4000):
            if "data.home-assistant.io" in url:
                return {"success": True, "content": json.dumps([
                    {"full_name": "Clooos/Bubble-Card"}
                ])}
            if "README.md" in url:
                return {"success": True, "content": "# Bubble Card README"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        # Should have proceeded to fetch, not returned cached
        assert result.get("status") != "cached"

    async def test_no_memory_manager_skips_freshness(self):
        tools = make_tools(memory_dir=None)
        # No memory manager — should not error, just skip freshness check

        async def _fake_fetch(url, max_chars=4000):
            if "data.home-assistant.io" in url:
                return {"success": True, "content": json.dumps([
                    {"full_name": "Clooos/Bubble-Card"}
                ])}
            if "README" in url:
                return {"success": True, "content": "README content"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result.get("status") in ("ok", "not_found")  # no crash


# ---------------------------------------------------------------------------
# learn_hacs_component — HACS store lookup
# ---------------------------------------------------------------------------

class TestLearnHacsStoreLookup:
    async def test_resolves_name_from_store_list(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)
        store_json = json.dumps([{"full_name": "Clooos/Bubble-Card"}])

        async def _fake_fetch(url, max_chars=4000):
            if "data.home-assistant.io" in url:
                return {"success": True, "content": store_json}
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "# README"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result["status"] == "ok"
        assert "Clooos/Bubble-Card" in result["repo_url"]

    async def test_not_found_returns_status(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            # Return empty stores
            return {"success": True, "content": "[]"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="completely-unknown-xyz")
        assert result["status"] == "not_found"
        assert "github_url" in result["message"].lower() or "provide" in result["message"].lower()

    async def test_direct_github_url_skips_store(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)
        fetched_urls = []

        async def _fake_fetch(url, max_chars=4000):
            fetched_urls.append(url)
            if "README.md" in url:
                return {"success": True, "content": "# README"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(
            github_url="https://github.com/Clooos/Bubble-Card"
        )
        # No HACS store URL should have been fetched
        assert not any("data.home-assistant.io" in u or "hacs/default" in u for u in fetched_urls)
        assert result["status"] == "ok"
        assert result["readme"] is not None


# ---------------------------------------------------------------------------
# learn_hacs_component — fetch pipeline degradation
# ---------------------------------------------------------------------------

class TestLearnHacsFetchPipeline:
    async def test_readme_populated(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "# Bubble Card\nGreat card!"}
            if "data.home-assistant.io" in url:
                return {"success": True, "content": json.dumps([{"full_name": "Clooos/Bubble-Card"}])}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result["status"] == "ok"
        assert "Bubble Card" in result["readme"]

    async def test_changelog_none_if_missing(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "README"}
            # All CHANGELOG variants return 404
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        # Use github_url directly to skip store lookup
        result = await tools.learn_hacs_component(
            github_url="https://github.com/Clooos/Bubble-Card"
        )
        assert result["status"] == "ok"
        assert result["changelog"] is None

    async def test_examples_empty_if_dir_missing(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "README"}
            if "api.github.com" in url and "examples" in url:
                return {"success": False, "error": "HTTP 404"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(
            github_url="https://github.com/Clooos/Bubble-Card"
        )
        assert result["status"] == "ok"
        assert result["examples"] == []

    async def test_examples_empty_if_rate_limited(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "README"}
            if "api.github.com" in url and "examples" in url:
                return {"success": False, "error": "HTTP 403"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(
            github_url="https://github.com/Clooos/Bubble-Card"
        )
        assert result["status"] == "ok"
        assert result["examples"] == []

    async def test_full_ok_with_examples(self, tmp_path):
        tools = make_tools(memory_dir=tmp_path)

        async def _fake_fetch(url, max_chars=4000):
            if "README.md" in url and "main" in url:
                return {"success": True, "content": "# README"}
            if "CHANGELOG.md" in url and "main" in url:
                return {"success": True, "content": "## v2.0.0\n- Breaking change"}
            if "data.home-assistant.io" in url:
                return {"success": True, "content": json.dumps([{"full_name": "Clooos/Bubble-Card"}])}
            if "api.github.com" in url and "examples" in url:
                return {"success": True, "content": json.dumps([
                    {"type": "file", "name": "basic.yaml", "size": 200,
                     "download_url": "https://raw.githubusercontent.com/Clooos/Bubble-Card/main/examples/basic.yaml"}
                ])}
            if "basic.yaml" in url:
                return {"success": True, "content": "type: custom:bubble-card\ncard_type: button"}
            return {"success": False, "error": "HTTP 404"}

        tools.fetch_url = _fake_fetch  # type: ignore
        result = await tools.learn_hacs_component(name="Bubble-Card")
        assert result["status"] == "ok"
        assert result["readme"] is not None
        assert result["changelog"] is not None
        assert len(result["examples"]) == 1
        assert result["examples"][0]["filename"] == "basic.yaml"

    async def test_no_name_no_url_returns_error(self):
        tools = make_tools()
        result = await tools.learn_hacs_component()
        assert result.get("success") is False
        assert "error" in result
