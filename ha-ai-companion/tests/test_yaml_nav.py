"""Text-level YAML item navigation/editing and HA layout detection."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yaml

from src.ha.layout import default_target, detect_layout
from src.ha.yaml_blocks import (
    find_item, item_text, normalize_item_text, outline, read_range, remove_item, split_blocks, upsert_item,
)

AUTOS = """\
# Lights
- id: '1'
  alias: Morning light
  triggers:
    - trigger: time
      at: '07:00'
  # keep this comment
  actions: []

- id: '2'
  alias: Evening light
  triggers: []
  actions:
    - action: notify.mobile
      data:
        message: !secret evening_msg
"""


def test_split_blocks_attaches_column0_comments_to_next_item():
    kind, blocks = split_blocks(AUTOS)
    assert kind == "list" and len(blocks) == 2
    assert blocks[0].comment_start == 0 and blocks[0].start == 1
    assert blocks[1].value["alias"] == "Evening light"
    assert blocks[1].value["actions"][0]["data"]["message"].startswith("!secret")


def test_outline_lists_items_with_line_ranges():
    out = outline(AUTOS)
    assert out == [
        {"lines": "2-8", "item": "Morning light (id=1)"},
        {"lines": "10-16", "item": "Evening light (id=2)"},
    ]


def test_outline_mapping_file_with_children():
    cfg = "homeassistant:\n  name: Home\n" + "template:\n" + "".join(
        f"  - sensor:\n      - name: s{i}\n        state: '1'\n" for i in range(20)
    )
    out = outline(cfg, depth=2)
    assert out[0]["item"] == "homeassistant"
    assert out[1]["item"] == "template" and len(out[1]["children"]) == 20


def test_read_range_numbers_and_caps():
    r = read_range(AUTOS, 2, 4)
    assert r["content"].splitlines()[0] == "2: - id: '1'" and r["total_lines"] == 16
    assert read_range(AUTOS, 1, 999, max_lines=3)["end"] == 3


def test_upsert_replaces_only_target_item_keeps_comments_and_tags():
    block, parsed = normalize_item_text("id: '1'\nalias: Morning light v2\ntriggers: []\nactions: []\n", "x")
    new, mode = upsert_item(AUTOS, block, "1")
    assert mode == "replaced"
    assert new.startswith("# Lights\n- id: '1'\n  alias: Morning light v2\n")
    assert new.split("\n\n", 1)[1] == AUTOS.split("\n\n", 1)[1]  # item 2 byte-identical
    assert "!secret evening_msg" in new
    assert len(yaml.safe_load(new.replace("!secret ", ""))) == 2


def test_upsert_appends_new_item_with_generated_id():
    block, parsed = normalize_item_text("- alias: New\n  triggers: []\n  actions: []\n", "1700000000000")
    assert parsed["id"] == "1700000000000"
    new, mode = upsert_item(AUTOS, block, parsed["id"], parsed.get("alias"))
    assert mode == "added" and new.startswith(AUTOS)
    assert find_item(new, item_id="1700000000000").value["alias"] == "New"


def test_upsert_into_empty_list_file():
    block, _ = normalize_item_text("alias: A\nid: a1\n", "x")
    assert upsert_item("[]\n", block, "a1") == (block, "added")


def test_upsert_matches_idless_item_by_alias():
    text = "- alias: Legacy\n  triggers: []\n"
    block, parsed = normalize_item_text("alias: Legacy\ntriggers: [x]\n", "99")
    new, mode = upsert_item(text, block, parsed["id"], parsed["alias"])
    assert mode == "replaced" and new.count("alias: Legacy") == 1


def test_normalize_rejects_multiple_items():
    try:
        normalize_item_text("- alias: a\n- alias: b\n", "x")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_remove_item_and_item_text():
    b = find_item(AUTOS, item_id="1")
    assert item_text(AUTOS, b).startswith("# Lights\n- id: '1'")
    new = remove_item(AUTOS, item_id="1")
    assert new.startswith("- id: '2'") and "Morning" not in new
    assert remove_item(new, item_id="2") == "[]\n"
    assert remove_item(AUTOS, item_id="nope") is None


def _write(base, rel, text):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_detect_layout_mixed(tmp_path):
    _write(tmp_path, "configuration.yaml",
           "homeassistant:\n  packages: !include_dir_named packages\n"
           "automation: !include automations.yaml\n"
           "automation split: !include_dir_merge_list automations/\n"
           "script: !include scripts.yaml\n"
           "scene: []\n")
    _write(tmp_path, "automations.yaml", AUTOS)
    _write(tmp_path, "automations/heating.yaml", "- id: h1\n  alias: Heat\n")
    _write(tmp_path, "scripts.yaml", "wake:\n  sequence: []\n")
    _write(tmp_path, "packages/garage.yaml", "automation:\n  - id: g1\n    alias: Garage\n")
    lay = detect_layout(tmp_path)
    autos = {(e["path"], e["format"]) for e in lay["automation"]}
    assert autos == {("automations.yaml", "list"), ("automations/heating.yaml", "list"),
                     ("packages/garage.yaml", "package")}
    assert lay["script"] == [{"path": "scripts.yaml", "format": "mapping", "via": "!include"}]
    assert lay["scene"] == [{"path": "configuration.yaml", "format": "inline"}]
    assert default_target(lay, "automation") == "automations.yaml"


def test_detect_layout_rejects_include_outside_config(tmp_path):
    _write(tmp_path, "configuration.yaml", "automation: !include ../evil.yaml\n")
    assert detect_layout(tmp_path)["automation"] == []


def test_detect_layout_missing_config(tmp_path):
    assert detect_layout(tmp_path) == {"automation": [], "script": [], "scene": []}


def test_reload_domains(tmp_path):
    from src.ha.layout import reload_domains
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n"
        "script: !include scripts.yaml\n"
        "automation split: !include_dir_merge_list automations/\n"
        "scene:\n  - name: x\n    entities: {}\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    (tmp_path / "scripts.yaml").write_text("{}\n")
    (tmp_path / "automations").mkdir()
    (tmp_path / "automations" / "a.yaml").write_text("[]\n")
    lay = detect_layout(tmp_path)
    assert reload_domains(lay, ["automations.yaml"]) == ["automation"]
    assert reload_domains(lay, ["automations/new.yaml", "scripts.yaml"]) == ["automation", "script"]
    assert reload_domains(lay, ["automations.yaml", "configuration.yaml"]) is None
    assert reload_domains(lay, ["lovelace/x.yaml"]) is None
    assert reload_domains(lay, []) is None


def test_find_item_alias_must_be_unique():
    text = "- alias: Dup\n  a: 1\n- alias: Dup\n  a: 2\n- alias: Solo\n"
    assert find_item(text, alias="Dup") is None
    assert find_item(text, alias="Solo") is not None
    assert remove_item(text, alias="Dup") is None


def test_normalize_dedents_indented_input():
    block, parsed = normalize_item_text("    alias: X\n    triggers: []\n", "abc")
    assert yaml.safe_load(block) == [{"id": "abc", "alias": "X", "triggers": []}]


def test_upsert_keeps_crlf_line_endings():
    text = "- id: '1'\r\n  alias: A\r\n- id: '2'\r\n  alias: B\r\n"
    block, _ = normalize_item_text("id: '1'\nalias: A2\n", "1")
    new, how = upsert_item(text, block, "1")
    assert how == "replaced" and "\n" not in new.replace("\r\n", "")
    new, how = upsert_item(text, normalize_item_text("alias: C\n", "3")[0], "3")
    assert how == "added" and "\n" not in new.replace("\r\n", "")


def test_dir_includes_skip_secrets_and_hidden(tmp_path):
    _write(tmp_path, "configuration.yaml", "automation split: !include_dir_merge_list automations\n")
    _write(tmp_path, "automations/a.yaml", "[]\n")
    _write(tmp_path, "automations/secrets.yaml", "pw: x\n")
    _write(tmp_path, "automations/.storage/b.yaml", "[]\n")
    assert [e["path"] for e in detect_layout(tmp_path)["automation"]] == ["automations/a.yaml"]


def test_reload_domains_package_file_forces_full_reload(tmp_path):
    from src.ha.layout import reload_domains
    _write(tmp_path, "configuration.yaml",
           "homeassistant:\n  packages: !include_dir_named automations/pkg\n"
           "automation split: !include_dir_merge_list automations\n")
    _write(tmp_path, "automations/a.yaml", "[]\n")
    _write(tmp_path, "automations/pkg/p.yaml", "automation: []\nscript: {}\n")
    lay = detect_layout(tmp_path)
    assert reload_domains(lay, ["automations/a.yaml"]) == ["automation"]
    assert reload_domains(lay, ["automations/pkg/p.yaml"]) is None
