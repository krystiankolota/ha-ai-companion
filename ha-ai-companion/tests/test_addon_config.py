import re
from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))


def test_option_defaults_match_list_schema():
    # Supervisor rejects a default that is not one of the list(...) values, even "" with `?`,
    # and then the add-on cannot start or save settings.
    for key, rule in CONFIG["schema"].items():
        m = re.fullmatch(r"list\((.+)\)\??", str(rule))
        if m and key in CONFIG["options"]:
            assert str(CONFIG["options"][key]) in m.group(1).split("|"), key
