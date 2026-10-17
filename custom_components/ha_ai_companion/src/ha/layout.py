"""Detect where automations / scripts / scenes live in a HA config dir.

Reads configuration.yaml (and package files) without resolving includes, so
it works on any setup: single file, split dirs, inline, or packages.
"""
from pathlib import Path
from typing import Optional, Dict, List

from .yaml_blocks import load_tolerant

DOMAINS = ("automation", "script", "scene")
_MAX_PACKAGE_FILES = 200


def _tag(value):
    """'!include_dir_merge_list automations' -> ('!include_dir_merge_list', 'automations')."""
    if isinstance(value, str) and value.startswith("!"):
        tag, _, arg = value.partition(" ")
        return tag, arg.strip()
    return None, None


def _yaml_files(dir_path: Path) -> List[Path]:
    if not dir_path.is_dir():
        return []
    # Same filter as HA's include_dir loaders: no secrets.yaml, no hidden files/dirs.
    files = (p for p in dir_path.rglob("*.yaml")
             if p.is_file() and p.name != "secrets.yaml"
             and not any(part.startswith(".") for part in p.relative_to(dir_path).parts))
    return sorted(files)[:_MAX_PACKAGE_FILES]


def _rel(config_dir: Path, p: Path) -> str:
    return p.relative_to(config_dir).as_posix()


def _resolve(config_dir: Path, base: Path, value, source: str) -> List[dict]:
    """Map one domain value to file entries. format: list | mapping | item | inline."""
    tag, arg = _tag(value)
    if tag is None:
        if value is None:
            return []
        return [{"path": source, "format": "inline"}]
    target = (base / arg).resolve()
    try:
        target.relative_to(config_dir.resolve())
    except ValueError:
        return []
    if tag == "!include":
        fmt = "mapping" if isinstance(_safe_load(target), dict) else "list"
        return [{"path": _rel(config_dir.resolve(), target), "format": fmt, "via": tag}]
    if tag in ("!include_dir_merge_list", "!include_dir_list", "!include_dir_named", "!include_dir_merge_named"):
        fmt = {"!include_dir_merge_list": "list", "!include_dir_list": "item",
               "!include_dir_named": "item", "!include_dir_merge_named": "mapping"}[tag]
        return [{"path": _rel(config_dir.resolve(), p), "format": fmt, "dir": _rel(config_dir.resolve(), target)}
                for p in _yaml_files(target)] or [{"path": _rel(config_dir.resolve(), target), "format": fmt, "dir": _rel(config_dir.resolve(), target), "empty_dir": True}]
    return []


def _safe_load(path: Path):
    try:
        return load_tolerant(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _domain_keys(cfg: dict, domain: str):
    # HA allows several keys per domain: "automation", "automation manual", "automation ui".
    return [k for k in cfg if isinstance(k, str) and (k == domain or k.startswith(domain + " "))]


def detect_layout(config_dir) -> Dict[str, List[dict]]:
    config_dir = Path(config_dir)
    main = config_dir / "configuration.yaml"
    cfg = _safe_load(main)
    layout: Dict[str, List[dict]] = {d: [] for d in DOMAINS}
    if not isinstance(cfg, dict):
        return layout
    for domain in DOMAINS:
        for key in _domain_keys(cfg, domain):
            layout[domain] += _resolve(config_dir, config_dir, cfg[key], "configuration.yaml")

    ha = cfg.get("homeassistant")
    packages = ha.get("packages") if isinstance(ha, dict) else None
    package_files: List[Path] = []
    tag, arg = _tag(packages)
    if tag in ("!include_dir_named", "!include_dir_merge_named"):
        package_files = _yaml_files(config_dir / arg)
    elif tag == "!include":
        package_files = [config_dir / arg]
    for pf in package_files:
        data = _safe_load(pf)
        if not isinstance(data, dict):
            continue
        # merge_named: file holds {package_name: {...}}; named: file is the package itself.
        pkgs = data.values() if tag == "!include_dir_merge_named" else [data]
        for pkg in pkgs:
            if not isinstance(pkg, dict):
                continue
            for domain in DOMAINS:
                for key in _domain_keys(pkg, domain):
                    for entry in _resolve(config_dir, pf.parent, pkg[key], _rel(config_dir, pf)):
                        if entry["format"] == "inline":
                            entry = {**entry, "format": "package", "key": key}
                        layout[domain].append(entry)
    return layout


def editable_list_files(layout: Dict[str, List[dict]], domain: str) -> List[str]:
    """Files whose top level is a list of items (safe for upsert_item)."""
    return [e["path"] for e in layout.get(domain, []) if e["format"] == "list" and not e.get("empty_dir")]


def default_target(layout: Dict[str, List[dict]], domain: str):
    """The file HA's UI editor writes (`domain: !include x.yaml`), else the first list file."""
    entries = [e for e in layout.get(domain, []) if e["format"] == "list" and not e.get("empty_dir")]
    entries.sort(key=lambda e: e.get("via") != "!include")
    return entries[0]["path"] if entries else None


def reload_domains(layout: Dict[str, List[dict]], files: List[str]) -> Optional[List[str]]:
    """Domains whose `<domain>.reload` covers every changed file, or None when a
    full reload is needed (configuration.yaml, packages, inline, other files)."""
    owners: Dict[str, str] = {}
    dirs: List[tuple] = []
    full_reload = {"configuration.yaml"}
    for domain, entries in layout.items():
        for e in entries:
            if e["format"] in ("inline", "package"):
                full_reload.add(e["path"])
                continue
            owners[e["path"]] = domain
            if e.get("dir"):
                dirs.append((e["dir"].rstrip("/") + "/", domain))
    found = set()
    for f in files:
        f = f.replace("\\", "/")
        if f in full_reload:
            return None
        d = owners.get(f) or next((dom for pre, dom in dirs if f.startswith(pre)), None)
        if d is None:
            return None
        found.add(d)
    return sorted(found) or None
