"""Tests for memory v2 (MemoryStore) and the memory agent tools."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.memory.store import (
    MAX_TEXT_CHARS, PREFERENCE_BUDGET, RECALL_BUDGET, MemoryStore, _fix_mojibake, extract_entities,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path))


def test_add_dedupes_by_normalized_text(store):
    a = store.add("preference", "Respond in Polish.")
    b = store.add("preference", "  respond in   POLISH. ")
    assert b["duplicate_of"] == a["id"] and len(store.list()) == 1


def test_rejected_text_is_not_resaved(store):
    a = store.add("fact", "pompa = water pressure pump")
    store.update(a["id"], status="rejected")
    b = store.add("fact", "pompa = water pressure pump")
    assert b["duplicate_of"] == a["id"] and b["status"] == "rejected"


def test_replaces_and_key_supersede(store):
    a = store.add("preference", "Lights at 40% in the evening")
    b = store.add("preference", "Lights at 30% in the evening", replaces=[a["id"]])
    assert b["superseded"] == [a["id"]] and store.get(a["id"])["status"] == "superseded"
    assert store.get(b["id"])["supersedes"] == a["id"]
    k1 = store.add("procedure", "v1 syntax", key="pattern_x_syntax")
    k2 = store.add("procedure", "v2 syntax", key="pattern_x_syntax")
    assert store.get(k1["id"])["status"] == "superseded" and k2["superseded"] == [k1["id"]]


def test_validation(store):
    with pytest.raises(ValueError):
        store.add("bogus", "x")
    with pytest.raises(ValueError):
        store.add("fact", "x" * (MAX_TEXT_CHARS + 1))
    with pytest.raises(ValueError):
        store.add("fact", "   ")


def test_similar_hint(store):
    store.add("fact", "Krystian and Marta live here; Krystian is the primary user")
    r = store.add("fact", "Marta and Krystian live here")
    assert r["similar"] and r["similar"][0]["overlap"] >= 0.5


def test_recall_preferences_always_and_query_capped(store):
    store.add("preference", "Respond in Polish")
    store.add("fact", "cwu = hot water boiler")
    store.add("fact", "Garage door sensor is binary_sensor.brama_garaz")
    for i in range(30):
        store.add("fact", f"Unrelated filler fact number {i} about watering the garden zone {i}")
    ctx = store.recall("")
    assert "Respond in Polish" in ctx and "boiler" not in ctx
    ctx = store.recall("is the CWU boiler on?")
    assert "hot water boiler" in ctx and "Respond in Polish" in ctx
    ctx = store.recall("garden watering")
    body = ctx.split("\n", 1)[1]
    assert len(body) <= PREFERENCE_BUDGET + RECALL_BUDGET + 10
    assert body.count("\n") + 1 <= 6  # 1 preference + max 5 query hits


def test_recall_skips_candidates_and_stale(store):
    store.add("preference", "Candidate pref", status="candidate")
    s = store.add("preference", "Stale pref")
    store.update(s["id"], status="stale")
    assert store.recall("pref") == ""


def test_recall_stems_polish_allows_rare_and_filters_common_single_tokens(store):
    store.add("fact", "Rekuperator pracuje cicho nocą")
    store.add("fact", "Ustawienia rekuperatora zapisano")
    assert len(store.search("rekuperatora")) == 2

    for i in range(3):
        store.add("fact", f"Boiler nr {i} działa")
    assert not store.search("boiler")


def test_search_does_not_treat_stopword_only_query_as_empty(store):
    store.add("fact", "The user likes quiet evenings")
    assert store.search("the and with") == []


def test_preference_recall_budget_is_1200_chars(store):
    first = "First preference " + "a" * 380
    second = "Second preference " + "b" * 380
    store.add("preference", first)
    store.add("preference", second)
    body = store.recall("").split("\n", 1)[1]
    assert PREFERENCE_BUDGET == 1200
    assert first in body and second in body and len(body) > 600


def test_search_folds_polish_diacritics(store):
    store.add("fact", "Łazienka na piętrze ma wentylator")
    assert store.search("lazienka")
    assert store.search("łazienki")  # inflected form via 5-char stems


def test_doctor_marks_stale_dupes_and_links(store):
    a = store.add("fact", "Night lamp is light.lampka_nocna")
    store.add("fact", "Porch light is light.ganek")
    d1 = store.add("fact", "Boiler runs between 5 and 7 every morning")
    store._exec("INSERT INTO memories(kind,text,status,hash,created,updated) VALUES "
                "('fact','Boiler runs between 5 and 7 every single morning','active','h2','t','t')")
    store._exec("UPDATE memories SET supersedes=999 WHERE id=?", (d1["id"],))
    r = store.doctor(valid_entity_ids={"light.ganek", "switch.lampka_nocna"})
    assert r["stale"] == [{"id": a["id"], "missing": ["light.lampka_nocna"], "likely_now": ["switch.lampka_nocna"]}]
    assert store.get(a["id"])["status"] == "stale"
    assert r["duplicates"] and r["broken_links"] == [d1["id"]]
    assert store.get(d1["id"])["supersedes"] is None


def test_doctor_rejects_old_candidates_reports_unused_and_conflicts(store):
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    old_candidate = store.add("fact", "Candidate is still pending", status="candidate")
    used_long_ago = store.add("fact", "Night temperature target is 20 degrees")
    never_used = store.add("decision", "Boiler target temperature is 55 degrees")
    old_preference = store.add("preference", "Always answer in Polish")
    store._exec("UPDATE memories SET created=? WHERE id=?",
                ((now - datetime.timedelta(days=31)).isoformat(), old_candidate["id"]))
    store._exec("UPDATE memories SET last_used=? WHERE id=?",
                ((now - datetime.timedelta(days=91)).isoformat(), used_long_ago["id"]))
    store._exec("UPDATE memories SET created=? WHERE id=?",
                ((now - datetime.timedelta(days=91)).isoformat(), never_used["id"]))
    store._exec("UPDATE memories SET created=? WHERE id=?",
                ((now - datetime.timedelta(days=100)).isoformat(), old_preference["id"]))
    store._exec("UPDATE memories SET key='temperature' WHERE id IN (?,?)",
                (used_long_ago["id"], never_used["id"]))

    report = store.doctor(apply=True)
    assert report["expired_candidates"] == [old_candidate["id"]]
    assert report["candidates"] == 0
    assert store.get(old_candidate["id"])["status"] == "rejected"
    assert set(report["unused"]) == {used_long_ago["id"], never_used["id"]}
    assert old_preference["id"] not in report["unused"]
    assert any(set(pair["ids"]) == {used_long_ago["id"], never_used["id"]}
               for pair in report["conflicts"])


def test_doctor_reports_number_conflicts_by_token_overlap(store):
    earlier = store.add("preference", "Bedroom target temperature is 19 degrees at night")
    later = store.add("preference", "Bedroom target temperature is 21 degrees at night")
    report = store.doctor(apply=False)
    assert any(set(pair["ids"]) == {earlier["id"], later["id"]}
               for pair in report["conflicts"])


def test_migration_from_markdown(tmp_path):
    (tmp_path / "preference_language.md").write_text("<!-- critical -->\n<!-- updated: x -->\nRespond in Polish\n",
                                                     encoding="utf-8")
    (tmp_path / "device_x.md").write_text("pompa = pressure pump\n", encoding="utf-8")
    (tmp_path / "pattern_bubble-card_syntax.md").write_text("- type: pop-up\n", encoding="utf-8")
    s = MemoryStore(str(tmp_path))
    by_text = {r["text"]: r for r in s.list()}
    assert by_text["Respond in Polish"]["kind"] == "preference" and by_text["Respond in Polish"]["status"] == "candidate"
    assert by_text["pompa = pressure pump"]["status"] == "candidate"
    pat = by_text["- type: pop-up"]
    assert pat["kind"] == "procedure" and pat["status"] == "active" and pat["key"] == "pattern_bubble-card_syntax"
    assert not list(tmp_path.glob("*.md")) and len(list((tmp_path / "archive_v1").glob("*.md"))) == 3
    (tmp_path / "late.md").write_text("added later\n", encoding="utf-8")
    assert MemoryStore(str(tmp_path)).migrate_markdown() == 0  # runs once


def test_helpers():
    original = "Ogród i Oświetlenie"
    assert _fix_mojibake(original.encode("utf-8").decode("latin-1")) == original
    assert _fix_mojibake("Garaż") == "Garaż"
    assert extract_entities("switch.a light.b config.json sensor.time sensor.time") == [
        "light.b", "sensor.time", "switch.a"]


# --------------------------------------------------------------------- tools

def _tools(store, registry_ids=("light.ganek", "sensor.attic")):
    from src.agents.tools import AgentTools
    t = AgentTools.__new__(AgentTools)
    t.memory_manager = store

    async def states():
        return [{"entity_id": "light.ganek"}]

    async def registry():
        return [{"entity_id": e} for e in registry_ids]

    t._all_states = states
    t._get_all_entities = registry
    return t


def run(coro):
    return asyncio.run(coro)


def test_save_memory_tool_paths(store):
    t = _tools(store)
    r = run(t.save_memory("preference", "Always notify both phones"))
    assert r["success"] and store.get(r["id"])["status"] == "active"
    again = run(t.save_memory("preference", "always notify both phones"))
    assert again["duplicate"]
    bad = run(t.save_memory("fact", "x" * 900))
    assert not bad["success"] and "limit" in bad["error"]
    cand = run(t.save_memory("fact", "Marta works night shifts on weekends", status="candidate"))
    assert store.get(cand["id"])["status"] == "candidate"
    assert run(t.search_memory("night shifts"))["count"] == 0
    assert run(t.search_memory("night shifts", include_candidates=True))["count"] == 1


def test_delete_memory_rejects_and_doctor_uses_states(store):
    t = _tools(store)
    a = store.add("fact", "Porch is light.ganek")
    b = store.add("fact", "Old lamp is light.gone")
    assert run(t.delete_memory(a["id"]))["success"] and store.get(a["id"])["status"] == "rejected"
    r = run(t.memory_doctor())
    assert [s["id"] for s in r["stale"]] == [b["id"]]


def test_doctor_skips_stale_check_on_partial_inventory(store):
    a = store.add("fact", "Attic humidity is sensor.attic")
    r = run(_tools(store, registry_ids=()).memory_doctor())
    assert r["stale"] == [] and "skipped" in r["note"]
    assert store.get(a["id"])["status"] == "active"
    run(_tools(store).memory_doctor())
    assert store.get(a["id"])["status"] == "active"  # registry-only entity is valid


def test_doctor_reports_services_and_procedures_without_retiring(store):
    pref = store.add("preference", "Send security alerts to notify.mobile_app_marta")
    proc = store.add("procedure", "Example: entity: light.example", key="pattern_x_examples")
    gone = store.add("fact", "Old lamp is light.gone")
    r = store.doctor(valid_entity_ids={"light.ganek", "sensor.attic"})
    assert [s["id"] for s in r["stale"]] == [gone["id"]]
    assert {u["id"] for u in r["unverified"]} == {pref["id"], proc["id"]}
    assert store.get(pref["id"])["status"] == store.get(proc["id"])["status"] == "active"


def test_candidate_does_not_supersede_until_approved(store):
    old = store.add("preference", "Lights at 40% in the evening", key="evening_lights")
    cand = store.add("preference", "Lights at 30% in the evening", key="evening_lights", status="candidate")
    assert cand["superseded"] == [] and cand["pending_replaces"] == [old["id"]]
    assert store.get(old["id"])["status"] == "active"
    store.update(cand["id"], status="rejected")
    assert store.get(old["id"])["status"] == "active"
    cand2 = store.add("preference", "Lights at 35% in the evening", key="evening_lights", status="candidate")
    store.update(cand2["id"], status="active")
    assert store.get(old["id"])["status"] == "superseded"


def test_duplicate_still_applies_replaces(store):
    a = store.add("fact", "Garage door opener is the left button")
    b = store.add("fact", "Garage door opener is the right button")
    again = store.add("fact", "Garage door opener is the right button", replaces=[a["id"]])
    assert again["duplicate_of"] == b["id"] and again["superseded"] == [a["id"]]
    assert store.get(a["id"])["status"] == "superseded"


def test_procedure_key_is_searchable_by_slug(store):
    store.add("procedure", "Pop-up uses hash and card_type pop-up", key="pattern_bubble-card_syntax")
    assert store.search("bubble-card", kind="procedure")
