import asyncio
import json
import time

from src.usage.advisor import ModelAdvisor, _norm


def _m(mid, p_in, p_out, cache=None, tools=True, ctx=200_000, out=("text",), exp=None):
    pricing = {"prompt": str(p_in), "completion": str(p_out)}
    if cache is not None:
        pricing["input_cache_read"] = str(cache)
    return {
        "id": mid, "name": mid, "context_length": ctx, "expiration_date": exp,
        "supported_parameters": ["tools", "tool_choice"] if tools else ["temperature"],
        "architecture": {"output_modalities": list(out)}, "pricing": pricing,
    }


CATALOG = [
    _m("openai/gpt-6.1-sol", 2e-6, 1e-5, 1e-7),
    _m("openai/gpt-6-luna", 1e-7, 5e-7, 1e-8),
    _m("deepseek/deepseek-v4.1-flash", 1.5e-7, 6e-7, 3e-9),
    _m("openai/gpt-6.1-sol:batch", 1e-6, 5e-6),
    _m("~deepseek/deepseek-flash-latest", 1e-8, 1e-7),
    _m("acme/no-tools", 1e-7, 1e-7, tools=False),
    _m("acme/tiny-ctx", 1e-7, 1e-7, ctx=32_000),
    _m("acme/image-out", 1e-7, 1e-7, out=("image", "text")),
    _m("acme/free", 0, 0),
    _m("acme/expired", 1e-7, 1e-7, exp="2000-01-01"),
]
AA = [
    {"slug": "gpt-6-1-sol", "evaluations": {"artificial_analysis_intelligence_index": 80, "artificial_analysis_coding_index": 78}},
    {"slug": "gpt-6-luna-reasoning", "evaluations": {"artificial_analysis_intelligence_index": 70, "artificial_analysis_coding_index": 60}},
    {"slug": "deepseek-v4.1-flash", "evaluations": {"artificial_analysis_intelligence_index": 66, "artificial_analysis_coding_index": 65}},
]


def test_norm():
    assert _norm("openai/GPT-6.1-sol:batch") == "gpt-6-1-sol"
    assert _norm("deepseek-v4.1-flash") == "deepseek-v4-1-flash"


def test_candidates_filter_and_cost():
    mix = {"input": 10_000, "cached": 4_000, "output": 500, "calls_per_session": 4}
    cands = ModelAdvisor.candidates(CATALOG, mix, {})
    assert {c["id"] for c in cands} == {"openai/gpt-6.1-sol", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash"}
    sol = next(c for c in cands if c["id"] == "openai/gpt-6.1-sol")
    expected = 6_000 * 2e-6 + 4_000 * 1e-7 + 500 * 1e-5
    assert abs(sol["est_cost_per_call"] - expected) < 1e-9
    assert abs(sol["est_cost_per_session"] - expected * 4) < 1e-5
    assert sol["intelligence"] is None  # missing scores stay missing


def test_score_index_merges_variants_and_ranks():
    scores = ModelAdvisor.score_index(AA)
    assert scores["gpt-6-luna"]["intelligence"] == 70
    cands = ModelAdvisor.candidates(CATALOG, ModelAdvisor.usage_mix(None), scores)
    r = ModelAdvisor.rank(cands)
    assert r["quality"][0]["id"] == "openai/gpt-6.1-sol"
    assert r["value"][0]["id"] != "openai/gpt-6.1-sol"  # value pick beats the expensive top model
    assert r["fast"][0]["id"] == "openai/gpt-6-luna"


def test_rank_empty_without_scores():
    cands = ModelAdvisor.candidates(CATALOG, ModelAdvisor.usage_mix(None), {})
    assert ModelAdvisor.rank(cands) == {"quality": [], "value": [], "fast": []}


def test_catalog_benchmarks_used_without_aa_key():
    cat = [dict(_m("z-ai/glm-5.3-flash", 1.5e-7, 5e-7, 3e-8),
                benchmarks={"artificial_analysis": {"intelligence_index": 41.8, "coding_index": None, "agentic_index": 50.9}}),
           dict(_m("openai/gpt-6.1-sol", 1e-6, 5e-6, 5e-8),
                benchmarks={"artificial_analysis": {"intelligence_index": 51.8, "coding_index": None, "agentic_index": None}})]
    cands = ModelAdvisor.candidates(cat, ModelAdvisor.usage_mix(None), {})
    glm = next(c for c in cands if c["id"] == "z-ai/glm-5.3-flash")
    assert glm["agentic"] == 50.9 and glm["sessions_per_10usd"] > 0
    r = ModelAdvisor.rank(cands)
    assert r["quality"][0]["id"] == "openai/gpt-6.1-sol"  # agentic missing -> falls back to intelligence 51.8 > 50.9
    assert r["value"][0]["id"] == "z-ai/glm-5.3-flash"
    # AA API score overrides the catalog value
    api = ModelAdvisor.candidates(cat, ModelAdvisor.usage_mix(None), {"glm-5-3-flash": {"intelligence": 60, "coding": None}})
    assert next(c for c in api if c["id"] == "z-ai/glm-5.3-flash")["intelligence"] == 60


def test_usage_mix_from_history():
    s = {"calls": 5, "input_tokens": 50_000, "cached_tokens": 25_000, "output_tokens": 2_500}
    usage = {"totals": {"calls": 104, "input_tokens": 9_999_999}, "by_session": {"a": s, "b": s}}
    mix = ModelAdvisor.usage_mix(usage)
    # Sessionless background calls in totals must not leak into the mix.
    assert mix["from_history"] and mix["input"] == 10_000 and mix["calls_per_session"] == 5
    assert ModelAdvisor.usage_mix({"totals": {"calls": 94}, "by_session": {}})["from_history"] is False


def test_check_configured_flags():
    rows = ModelAdvisor.check_configured(
        {"main": "openai/gpt-6.1-sol", "fast": "acme/no-tools", "other": "openai/gpt-61-typo"},
        CATALOG, {}, True)
    issues = {r["role"]: r["issues"] for r in rows}
    assert issues["main"] == []
    assert "tool-calling" in issues["fast"][0]
    assert "Not found" in issues["other"][0]


def test_check_configured_per_role_provider():
    rows = ModelAdvisor.check_configured(
        {"main": "openai/gpt-61-typo", "fast": "gpt-4o"}, CATALOG, {}, {"main"})
    issues = {r["role"]: r["issues"] for r in rows}
    assert "Not found" in issues["main"][0]
    assert issues["fast"] == []  # direct provider: no OpenRouter catalog check


def test_strip_usage_params_keeps_session_id():
    from src.agents.agent_system import AgentSystem
    p = {"model": "m", "stream_options": {}, "extra_body": {"usage": {"include": True}, "session_id": "s1"}}
    assert AgentSystem._strip_usage_params(p) == {"model": "m", "extra_body": {"session_id": "s1"}}
    assert AgentSystem._strip_usage_params({"model": "m", "extra_body": {"usage": {}}}) == {"model": "m"}


def test_advise_caches_and_falls_back(tmp_path):
    calls = []

    async def fetch(url, headers):
        calls.append(url)
        if len(calls) > 1:
            raise RuntimeError("network down")
        return {"data": CATALOG}

    adv = ModelAdvisor(str(tmp_path), fetch=fetch)
    first = asyncio.run(adv.advise({"main": "openai/gpt-6-luna"}, None, True))
    assert first["sources"]["openrouter"]["source"] == "live"
    assert first["sources"]["artificial_analysis"]["source"] == "no-key"
    assert first["candidate_models"] == 3
    # Within TTL: served from cache, no fetch.
    assert asyncio.run(adv.advise({}, None, True))["sources"]["openrouter"]["source"] == "cache"
    assert len(calls) == 1
    # Forced refresh fails: last-known-good copy is served.
    stale = asyncio.run(adv.advise({}, None, True, force=True))
    assert stale["sources"]["openrouter"]["source"] == "stale-cache"
    assert stale["candidate_models"] == 3


def test_advise_unavailable_without_cache(tmp_path):
    async def fetch(url, headers):
        raise RuntimeError("offline")

    out = asyncio.run(ModelAdvisor(str(tmp_path), aa_api_key="k", fetch=fetch).advise({}, None, True))
    assert out["sources"]["openrouter"]["source"] == "unavailable"
    assert out["sources"]["artificial_analysis"]["source"] == "unavailable"
    assert out["candidate_models"] == 0


def test_expired_cache_refetches(tmp_path):
    (tmp_path / "openrouter_models.json").write_text(json.dumps({"fetched_at": time.time() - 3 * 86400, "data": CATALOG[:1]}))

    async def fetch(url, headers):
        return {"data": CATALOG}

    out = asyncio.run(ModelAdvisor(str(tmp_path), fetch=fetch).advise({}, None, True))
    assert out["sources"]["openrouter"]["source"] == "live" and out["candidate_models"] == 3
