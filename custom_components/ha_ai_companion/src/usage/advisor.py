"""Model advisor — data-backed model suggestions for OpenRouter users.

Sources:
- OpenRouter ``/api/v1/models`` (public, no key): price, cache price, context, tool support.
- Artificial Analysis ``/api/v2/data/llms/models`` (optional free key): quality indexes.
- The user's own ``usage.jsonl`` mix: tokens per call and calls per session.

Both remote payloads are cached on disk with their retrieval date (last-known-good):
a failed refresh serves the previous copy instead of nothing. Missing quality scores
stay missing — they are never guessed.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
CACHE_TTL_S = 24 * 3600
MIN_CONTEXT = 128_000
TOP_N = 5
# Typical turn when there is no usage history yet.
DEFAULT_MIX = {"input": 20_000, "cached": 10_000, "output": 800, "calls_per_session": 6.0}

Fetcher = Callable[[str, Dict[str, str]], Awaitable[Any]]


async def _http_get_json(url: str, headers: Dict[str, str]) -> Any:
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


def _norm(model_id: str) -> str:
    """'openai/GPT-6.1-sol:batch' -> 'gpt-6-1-sol'."""
    s = (model_id or "").lower().split("/")[-1].split(":")[0]
    return re.sub(r"[._\s]+", "-", s).strip("-")


_AA_VARIANT = re.compile(r"-(reasoning|thinking|non-reasoning|high|medium|low|minimal|preview)$")


def _price(pricing: Dict[str, Any], key: str) -> Optional[float]:
    try:
        v = float(pricing.get(key))
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


class ModelAdvisor:
    def __init__(self, cache_dir: str, aa_api_key: Optional[str] = None, fetch: Optional[Fetcher] = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.aa_api_key = aa_api_key or None
        self._fetch = fetch or _http_get_json

    # -- cached remote data -------------------------------------------------
    async def _cached(self, name: str, url: str, headers: Dict[str, str], force: bool) -> Dict[str, Any]:
        path = self.cache_dir / f"{name}.json"
        cached = None
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                cached = None
        if cached and not force and time.time() - cached.get("fetched_at", 0) < CACHE_TTL_S:
            return {**cached, "source": "cache"}
        try:
            payload = await self._fetch(url, headers)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                raise ValueError("empty or malformed response")
            fresh = {"fetched_at": time.time(), "data": data}
            path.write_text(json.dumps(fresh), encoding="utf-8")
            return {**fresh, "source": "live"}
        except Exception as exc:
            logger.warning("ModelAdvisor: %s refresh failed: %s", name, exc)
            if cached:
                return {**cached, "source": "stale-cache", "error": str(exc)}
            return {"fetched_at": None, "data": [], "source": "unavailable", "error": str(exc)}

    async def openrouter_catalog(self, force: bool = False) -> Dict[str, Any]:
        return await self._cached("openrouter_models", OPENROUTER_MODELS_URL, {}, force)

    async def aa_scores(self, force: bool = False) -> Dict[str, Any]:
        if not self.aa_api_key:
            return {"fetched_at": None, "data": [], "source": "no-key"}
        return await self._cached("aa_models", AA_MODELS_URL, {"x-api-key": self.aa_api_key}, force)

    # -- pure helpers (unit-tested) -------------------------------------------
    @staticmethod
    def usage_mix(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        # Only chat-session calls: background calls (suggestions, summaries) have
        # no session and would skew both the token mix and calls/session.
        buckets = list(((usage or {}).get("by_session") or {}).values())
        calls = sum(b.get("calls", 0) for b in buckets)
        if calls < 5:
            return {**DEFAULT_MIX, "from_history": False}
        tot = lambda k: sum(b.get(k, 0) for b in buckets)  # noqa: E731
        return {
            "input": tot("input_tokens") / calls,
            "cached": tot("cached_tokens") / calls,
            "output": tot("output_tokens") / calls,
            "calls_per_session": max(1.0, calls / len(buckets)),
            "from_history": True,
        }

    @staticmethod
    def score_index(aa_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
        """Map normalized model slug -> best quality indexes across AA variants."""
        out: Dict[str, Dict[str, Optional[float]]] = {}
        for row in aa_rows:
            ev = row.get("evaluations") or {}
            scores = {
                "intelligence": ev.get("artificial_analysis_intelligence_index"),
                "coding": ev.get("artificial_analysis_coding_index"),
            }
            if scores["intelligence"] is None and scores["coding"] is None:
                continue
            slug = _norm(row.get("slug") or row.get("name") or "")
            for key in {slug, _AA_VARIANT.sub("", slug)}:
                cur = out.setdefault(key, {"intelligence": None, "coding": None})
                for k, v in scores.items():
                    if v is not None and (cur[k] is None or v > cur[k]):
                        cur[k] = v
        return out

    @staticmethod
    def candidates(catalog: List[Dict[str, Any]], mix: Dict[str, Any],
                   scores: Dict[str, Dict[str, Optional[float]]]) -> List[Dict[str, Any]]:
        now = time.time()
        out = []
        for m in catalog:
            mid = m.get("id") or ""
            if mid.startswith("~") or ":" in mid:  # moving aliases, :free / :batch variants
                continue
            if "tools" not in (m.get("supported_parameters") or []):
                continue
            if (m.get("architecture") or {}).get("output_modalities", ["text"]) != ["text"]:
                continue
            if (m.get("context_length") or 0) < MIN_CONTEXT:
                continue
            exp = m.get("expiration_date")
            if exp and str(exp)[:10] <= time.strftime("%Y-%m-%d", time.gmtime(now)):
                continue
            pricing = m.get("pricing") or {}
            p_in, p_out = _price(pricing, "prompt"), _price(pricing, "completion")
            if not p_in or not p_out:  # free/unknown price models are rate-limited or unpriced
                continue
            p_cache = _price(pricing, "input_cache_read")
            cached = min(mix["cached"], mix["input"])
            per_call = (mix["input"] - cached) * p_in + cached * (p_cache if p_cache is not None else p_in) + mix["output"] * p_out
            s = scores.get(_norm(mid)) or scores.get(_norm(m.get("canonical_slug") or "")) or {}
            out.append({
                "id": mid,
                "name": m.get("name") or mid,
                "context": m.get("context_length"),
                "price_in_per_m": round(p_in * 1e6, 4),
                "price_out_per_m": round(p_out * 1e6, 4),
                "price_cache_per_m": round(p_cache * 1e6, 4) if p_cache is not None else None,
                "est_cost_per_call": round(per_call, 6),
                "est_cost_per_session": round(per_call * mix["calls_per_session"], 5),
                "intelligence": s.get("intelligence"),
                "coding": s.get("coding"),
            })
        return out

    @staticmethod
    def rank(cands: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        scored = [c for c in cands if c["intelligence"] is not None]
        if not scored:
            return {"reasoning": [], "research": [], "budget": []}
        top_int = max(c["intelligence"] for c in scored)

        def quality(c, field):
            return c[field] if c[field] is not None else c["intelligence"]

        # Reasoning: writes config — best coding/intelligence, cost only breaks ties.
        reasoning = sorted(scored, key=lambda c: (-quality(c, "coding"), c["est_cost_per_call"]))
        # Research: many tool-reading calls — best quality per dollar among strong models.
        strong = [c for c in scored if c["intelligence"] >= 0.8 * top_int]
        research = sorted(strong, key=lambda c: -c["intelligence"] / max(c["est_cost_per_call"], 1e-9))
        # Budget: cheapest that is still reasonably capable.
        capable = [c for c in scored if c["intelligence"] >= 0.6 * top_int]
        budget = sorted(capable, key=lambda c: c["est_cost_per_call"])
        return {"reasoning": reasoning[:TOP_N], "research": research[:TOP_N], "budget": budget[:TOP_N]}

    @staticmethod
    def check_configured(configured: Dict[str, str], catalog: List[Dict[str, Any]],
                         cands_by_id: Dict[str, Dict[str, Any]], openrouter_roles) -> List[Dict[str, Any]]:
        """Flag configured models that are missing from OpenRouter or lack tool support.

        ``openrouter_roles``: roles whose client actually talks to OpenRouter (a bool
        applies to every role)."""
        by_id = {m.get("id"): m for m in catalog}
        rows = []
        for role, model in configured.items():
            if not model:
                continue
            row: Dict[str, Any] = {"role": role, "model": model, "issues": [], "info": cands_by_id.get(model)}
            on_or = openrouter_roles if isinstance(openrouter_roles, bool) else role in openrouter_roles
            if on_or and catalog:
                m = by_id.get(model)
                if m is None:
                    row["issues"].append("Not found in the OpenRouter catalog — check the model id.")
                elif "tools" not in (m.get("supported_parameters") or []):
                    row["issues"].append("No tool-calling support on OpenRouter — the agent cannot read or change HA.")
            rows.append(row)
        return rows

    # -- entry point ----------------------------------------------------------
    async def advise(self, configured: Dict[str, str], usage: Optional[Dict[str, Any]],
                     is_openrouter, force: bool = False) -> Dict[str, Any]:
        catalog = await self.openrouter_catalog(force)
        aa = await self.aa_scores(force)
        mix = self.usage_mix(usage)
        scores = self.score_index(aa["data"])
        cands = self.candidates(catalog["data"], mix, scores)
        by_id = {c["id"]: c for c in cands}
        return {
            "mix": mix,
            "configured": self.check_configured(configured, catalog["data"], by_id, is_openrouter),
            "rankings": self.rank(cands),
            "scored_models": sum(1 for c in cands if c["intelligence"] is not None),
            "candidate_models": len(cands),
            "sources": {
                "openrouter": {k: catalog.get(k) for k in ("source", "fetched_at", "error")},
                "artificial_analysis": {k: aa.get(k) for k in ("source", "fetched_at", "error")},
            },
        }
