"""Memory v2 — small SQLite store of durable facts the agent cannot read from HA.

Lifecycle: candidate -> active -> superseded | stale | rejected.
Only ``active`` records are injected. Recall is query-driven and capped.
"""
import hashlib
import logging
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Sequences that signal UTF-8 bytes mis-decoded as latin-1/cp1252 (mojibake).
# Legit Polish text uses single codepoints (ś, ż, ó) that do NOT contain these.
_MOJIBAKE_MARKERS = ('Ã', 'Å', 'Â', 'â€', 'ï¸', 'Ä\x85', 'Ä\x99', 'Ã³', 'Ä…')

# entity_id pattern: domain.object_id (lowercase). Excludes file extensions.
_ENTITY_RE = re.compile(r'\b([a-z][a-z0-9_]*\.[a-z0-9_]+)\b')
_NON_ENTITY_SUFFIX = ('.md', '.yaml', '.yml', '.json', '.py', '.js', '.css', '.html', '.txt')


def _mojibake_score(text: str) -> int:
    return sum(text.count(m) for m in _MOJIBAKE_MARKERS)


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8-decoded-as-latin-1 corruption, conservatively.

    Only transforms when the latin-1 round-trip both succeeds and strictly
    reduces the mojibake-marker count without introducing replacement chars,
    so clean text is never touched.
    """
    if _mojibake_score(text) == 0:
        return text
    try:
        repaired = text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if '�' in repaired and '�' not in text:
        return text
    if _mojibake_score(repaired) < _mojibake_score(text):
        return repaired
    return text


def extract_entities(content: str) -> List[str]:
    """Return sorted unique HA entity_ids referenced in memory content."""
    found = _ENTITY_RE.findall(content or "")
    out = {
        e for e in found
        if not e.endswith(_NON_ENTITY_SUFFIX) and 2 <= len(e.split('.', 1)[0]) <= 30
    }
    return sorted(out)


def _read_marker(raw: str, key: str) -> Optional[str]:
    """Extract a single-line ``<!-- key: value -->`` marker value, or None."""
    m = re.search(rf"<!--\s*{re.escape(key)}:\s*(.*?)\s*-->", raw)
    return m.group(1).strip() if m else None

KINDS = ("preference", "fact", "decision", "procedure")
STATUSES = ("candidate", "active", "superseded", "stale", "rejected")

MAX_TEXT_CHARS = 800
PREFERENCE_BUDGET = 1200
RECALL_BUDGET = 1200
RECALL_LIMIT = 5

_STOPWORDS = {
    "aby", "albo", "ale", "bez", "byc", "być", "co", "czy", "dla", "do", "gdy",
    "i", "ich", "jak", "jako", "jest", "jeszcze", "lub", "ma", "moze", "może",
    "na", "nad", "nie", "od", "oraz", "po", "pod", "przez", "sie", "się", "tak",
    "ten", "to", "w", "we", "z", "za", "ze", "że", "a", "an", "and", "are", "as",
    "at", "be", "but", "by", "for", "from", "has", "have", "in", "is", "it", "of",
    "on", "or", "the", "this", "to", "was", "were", "with",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT,
    text TEXT NOT NULL,
    entities TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    supersedes INTEGER,
    replaces TEXT NOT NULL DEFAULT '',
    hash TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    last_used TEXT
);
CREATE INDEX IF NOT EXISTS ix_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS ix_mem_hash ON memories(hash);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


# Domains whose dotted names are usually services, not entities.
SERVICE_DOMAINS = {"notify", "tts", "persistent_notification", "homeassistant", "shell_command", "rest_command", "python_script"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fold(text: str) -> str:
    """Casefold and strip diacritics so 'Łazienka' matches 'lazienka'."""
    t = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    return "".join(c for c in t if not unicodedata.combining(c))


def _tokens(text: str) -> set:
    words = (w for w in re.split(r"[\W_]+", _fold(text)) if len(w) >= 3 and w not in _STOPWORDS)
    return {w[:5] if len(w) >= 5 else w for w in words}


def extraction_rejection_reason(text: str, max_chars: Optional[int] = 300) -> Optional[str]:
    """Reject extracted memories that are too long, structured, or mostly HA entity IDs."""
    text = (text or "").strip()
    if max_chars is not None and len(text) > max_chars:
        return f"exceeds {max_chars} characters"
    if re.search(r"(?m)^\s{0,3}#{1,6}(?:\s|$)", text):
        return "contains a Markdown heading"
    if len(text.splitlines()) > 3:
        return "contains more than three lines"
    words = text.split()
    entity_tokens = re.findall(r"\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\b", text, re.IGNORECASE)
    if words and len(entity_tokens) / len(words) > 0.4:
        return "mostly contains Home Assistant entity IDs"
    return None


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(_fold(text).split()).encode("utf-8")).hexdigest()[:16]


class MemoryStore:
    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.memory_dir / "memory.db"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
        migrated = self.migrate_markdown()
        if migrated:
            logger.info("MemoryStore migrated %d markdown memory file(s) to candidates", migrated)

    # ------------------------------------------------------------------ basics

    def _rows(self, sql: str, args: Iterable = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(args)).fetchall()]

    def _exec(self, sql: str, args: Iterable = ()) -> int:
        with self._lock, self._db:
            cur = self._db.execute(sql, tuple(args))
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def get(self, mem_id: int) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM memories WHERE id = ?", (mem_id,))
        return rows[0] if rows else None

    def list(self, status: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return self._rows(sql + " ORDER BY updated DESC, id DESC", args)

    def counts(self) -> Dict[str, int]:
        return {r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) AS n FROM memories GROUP BY status")}

    # ------------------------------------------------------------------ writes

    def add(self, kind: str, text: str, key: Optional[str] = None, status: str = "active",
            source: str = "", replaces: Optional[List[int]] = None) -> Dict[str, Any]:
        """Add a record. Exact duplicates (by normalized hash) are not stored twice.

        Returns {"id", "status", "duplicate_of"?, "superseded": [...], "similar": [...]}.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        text = _fix_mojibake(text or "").strip()
        if not text:
            raise ValueError("text is empty")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"text is {len(text)} chars; limit is {MAX_TEXT_CHARS}. Keep one short fact per memory.")
        h = _hash(text)
        old_ids = {int(i) for i in (replaces or [])}
        if key:
            old_ids |= {r["id"] for r in self._rows(
                "SELECT id FROM memories WHERE key = ? AND status IN ('active','candidate')", (key,))}
        dup = self._rows("SELECT id, status, replaces FROM memories WHERE hash = ? AND status IN ('active','candidate','rejected')", (h,))
        if dup:
            d = dup[0]
            old_ids.discard(d["id"])
            superseded = []
            if old_ids and d["status"] == "active":
                superseded = self._retire(old_ids, keep=d["id"])
            elif old_ids and d["status"] == "candidate":
                pending = {int(i) for i in d["replaces"].split(",") if i} | old_ids
                self._exec("UPDATE memories SET replaces=? WHERE id=?", (",".join(map(str, sorted(pending))), d["id"]))
            return {"id": d["id"], "status": d["status"], "duplicate_of": d["id"], "superseded": superseded, "similar": []}

        now = _now()
        ordered = sorted(old_ids)
        new_id = self._exec(
            "INSERT INTO memories(kind, key, text, entities, status, source, supersedes, replaces, hash, created, updated)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, key, text, ",".join(extract_entities(text)), status, source,
             ordered[-1] if ordered else None, ",".join(map(str, ordered)), h, now, now))
        # Candidates only record what they would replace; retirement happens on approval.
        superseded = self._retire(old_ids, keep=new_id) if status == "active" else []
        similar = [] if old_ids else self.similar(text, exclude=new_id)
        return {"id": new_id, "status": status, "superseded": superseded,
                "pending_replaces": ordered if status != "active" else [], "similar": similar}

    def _retire(self, ids: Iterable[int], keep: int) -> List[int]:
        now, done = _now(), []
        for oid in sorted(set(ids) - {keep}):
            if self._exec("UPDATE memories SET status='superseded', updated=? WHERE id=? AND status IN ('active','candidate')",
                          (now, oid)):
                done.append(oid)
        return done

    def update(self, mem_id: int, text: Optional[str] = None, kind: Optional[str] = None,
               status: Optional[str] = None) -> bool:
        rec = self.get(mem_id)
        if not rec:
            return False
        if kind and kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if text is not None:
            text = _fix_mojibake(text).strip()
            if not text or len(text) > MAX_TEXT_CHARS:
                raise ValueError(f"text must be 1-{MAX_TEXT_CHARS} chars")
        new_text = text if text is not None else rec["text"]
        self._exec("UPDATE memories SET text=?, kind=?, status=?, entities=?, hash=?, updated=? WHERE id=?",
                   (new_text, kind or rec["kind"], status or rec["status"], ",".join(extract_entities(new_text)),
                    _hash(new_text), _now(), mem_id))
        if status == "active" and rec["status"] != "active":
            ids = {int(i) for i in (rec.get("replaces") or "").split(",") if i}
            if rec["key"]:
                ids |= {r["id"] for r in self._rows(
                    "SELECT id FROM memories WHERE key = ? AND status = 'active'", (rec["key"],))}
            self._retire(ids, keep=mem_id)
        return True

    def delete(self, mem_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE id = ?", (mem_id,)) > 0

    # ------------------------------------------------------------------ recall

    def similar(self, text: str, exclude: Optional[int] = None, threshold: float = 0.5) -> List[Dict[str, Any]]:
        """Active/candidate records whose token overlap with ``text`` is high (dedupe hint)."""
        toks = _tokens(text)
        if not toks:
            return []
        out = []
        for r in self._rows("SELECT id, kind, text FROM memories WHERE status IN ('active','candidate')"):
            if r["id"] == exclude:
                continue
            other = _tokens(r["text"])
            score = len(toks & other) / max(1, min(len(toks), len(other)))
            if score >= threshold:
                out.append({"id": r["id"], "kind": r["kind"], "text": r["text"][:160], "overlap": round(score, 2)})
        return sorted(out, key=lambda x: -x["overlap"])[:3]

    def search(self, query: str = "", kind: Optional[str] = None, statuses=("active",),
               limit: int = 20) -> List[Dict[str, Any]]:
        q = _tokens(query or "")
        marks = ",".join("?" for _ in statuses)
        sql, args = f"SELECT * FROM memories WHERE status IN ({marks})", list(statuses)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        rows = self._rows(sql + " ORDER BY updated DESC", args)
        if not (query or "").strip():
            return rows[:limit]
        if not q:
            return []
        active_token_counts: Dict[str, int] = {}
        for r in self.list(status="active"):
            for token in _tokens(r["text"] + " " + (r["key"] or "") + " " + r["entities"]):
                active_token_counts[token] = active_token_counts.get(token, 0) + 1
        scored = []
        for r in rows:
            hay = _tokens(r["text"] + " " + (r["key"] or "") + " " + r["entities"].replace(".", " ").replace("_", " "))
            shared = q & hay
            if len(shared) >= 2 or any(active_token_counts.get(token, 0) <= 2 for token in shared):
                scored.append((len(shared), r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    def recall(self, query: str = "") -> str:
        """Prompt block: active preferences (capped) + up to 5 query-matched records (capped)."""
        picked: List[Dict[str, Any]] = []

        def line(r):
            return f"- [{r['kind']} #{r['id']}] {r['text']}"

        def take(rows, budget):
            spent = 0
            for r in rows:
                if any(p["id"] == r["id"] for p in picked) or spent + len(line(r)) > budget:
                    continue
                picked.append(r)
                spent += len(line(r))

        take(self.list(status="active", kind="preference"), PREFERENCE_BUDGET)
        if query:
            take(self.search(query, limit=RECALL_LIMIT * 2)[:RECALL_LIMIT], RECALL_BUDGET)
        if not picked:
            return ""
        now = _now()
        with self._lock, self._db:
            self._db.executemany("UPDATE memories SET last_used=? WHERE id=?", [(now, r["id"]) for r in picked])
        return "## Memory (user-provided facts; trust HA state over these if they conflict)\n" + "\n".join(map(line, picked))

    # ------------------------------------------------------------------ health

    def doctor(self, valid_entity_ids: Optional[set] = None, apply: bool = True) -> Dict[str, Any]:
        """Report (and optionally fix) graph problems.

        - active/candidate records that reference entity_ids missing from HA -> stale
        - near-duplicate active records
        - supersede links pointing at missing records
        - old candidates, unused records, and conflicts
        """
        issues: Dict[str, Any] = {
            "stale": [], "unverified": [], "duplicates": [], "broken_links": [],
            "unused": [], "conflicts": [], "expired_candidates": [], "candidates": 0,
        }
        live = self.list(status="active") + self.list(status="candidate")
        now = datetime.now(timezone.utc)
        cutoff_90 = now - timedelta(days=90)
        cutoff_30 = now - timedelta(days=30)

        def parse_timestamp(value):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            except (AttributeError, TypeError, ValueError):
                return None

        for r in live:
            if r["status"] == "candidate":
                created = parse_timestamp(r.get("created"))
                if created and created < cutoff_30:
                    issues["expired_candidates"].append(r["id"])
            elif r["kind"] != "preference":
                reference_date = parse_timestamp(r.get("last_used")) if r.get("last_used") else parse_timestamp(r.get("created"))
                if reference_date and reference_date < cutoff_90:
                    issues["unused"].append(r["id"])
        if valid_entity_ids:
            by_obj: Dict[str, List[str]] = {}
            for eid in valid_entity_ids:
                by_obj.setdefault(eid.split(".", 1)[-1], []).append(eid)
            domains = {e.split(".", 1)[0] for e in valid_entity_ids}
            for r in live:
                missing = [e for e in r["entities"].split(",") if e and e not in valid_entity_ids]
                if not missing:
                    continue
                # Procedures quote example ids; service-like domains (notify.x) and domains with no
                # entities at all may be service names. Report those, never retire them.
                if (r["kind"] == "procedure"
                        or any(m.split(".", 1)[0] in SERVICE_DOMAINS or m.split(".", 1)[0] not in domains for m in missing)):
                    issues["unverified"].append({"id": r["id"], "missing": missing})
                else:
                    issues["stale"].append({
                        "id": r["id"], "missing": missing,
                        "likely_now": sorted({c for m in missing for c in by_obj.get(m.split(".", 1)[-1], [])}),
                    })
        active = [r for r in live if r["status"] == "active"]
        for i, a in enumerate(active):
            for b in active[i + 1:]:
                ta, tb = _tokens(a["text"]), _tokens(b["text"])
                if ta and tb and len(ta & tb) / min(len(ta), len(tb)) >= 0.8:
                    issues["duplicates"].append([a["id"], b["id"]])
                overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
                numbers_a = set(re.findall(r"\d+[:.]?\d*", a["text"]))
                numbers_b = set(re.findall(r"\d+[:.]?\d*", b["text"]))
                same_key = bool(a.get("key") and a["key"] == b.get("key"))
                if same_key or (overlap >= 0.5 and numbers_a and numbers_b and numbers_a != numbers_b):
                    issues["conflicts"].append({
                        "ids": [a["id"], b["id"]],
                        "key": a.get("key") if same_key else None,
                    })
        ids = {r["id"] for r in self._rows("SELECT id FROM memories")}
        issues["broken_links"] = [r["id"] for r in self._rows("SELECT id, supersedes FROM memories WHERE supersedes IS NOT NULL")
                                  if r["supersedes"] not in ids]
        issues["candidates"] = len([r for r in live if r["status"] == "candidate"])
        if apply:
            now = _now()
            for s in issues["stale"]:
                self._exec("UPDATE memories SET status='stale', updated=? WHERE id=?", (now, s["id"]))
            for rid in issues["broken_links"]:
                self._exec("UPDATE memories SET supersedes=NULL WHERE id=?", (rid,))
            for rid in issues["expired_candidates"]:
                self._exec("UPDATE memories SET status='rejected', updated=? WHERE id=? AND status IN ('candidate','stale')",
                           (now, rid))
        issues["candidates"] = len([r for r in live if r["status"] == "candidate"]) - (
            len(issues["expired_candidates"]) if apply else 0
        )
        self._exec("INSERT OR REPLACE INTO meta(k, v) VALUES ('doctor_last_run', ?)", (_now(),))
        return issues

    # ------------------------------------------------------------------ migration

    def migrate_markdown(self) -> int:
        """Import v1 markdown files once. pattern_* (HACS docs) stay active; the rest become candidates."""
        if self._rows("SELECT v FROM meta WHERE k='migrated_v1'"):
            return 0
        files = sorted(p for p in self.memory_dir.glob("*.md") if p.is_file())
        archive = self.memory_dir / "archive_v1"
        count = 0
        for p in files:
            try:
                raw = p.read_text(encoding="utf-8")
            except Exception:
                continue
            body = _fix_mojibake(re.sub(r"<!--.*?-->\n?", "", raw, flags=re.DOTALL)).strip()
            stem = p.stem
            if body:
                kind, status = "fact", "candidate"
                if stem.startswith(("preference_", "identity_")) or _read_marker(raw, "type") == "preference":
                    kind = "preference"
                elif stem.startswith("pattern_"):
                    kind, status = "procedure", "active"
                chunks = _chunks(body, MAX_TEXT_CHARS)
                for i, chunk in enumerate(chunks):
                    key = None
                    if kind == "procedure":
                        key = stem if len(chunks) == 1 else f"{stem}_part{i + 1}"
                    try:
                        self.add(kind, chunk, key=key, status=status, source=f"v1:{p.name}")
                        count += 1
                    except ValueError:
                        pass
            archive.mkdir(exist_ok=True)
            try:
                p.replace(archive / p.name)
            except Exception as exc:
                logger.warning("MemoryStore could not archive %s: %s", p.name, exc)
        self._exec("INSERT OR REPLACE INTO meta(k, v) VALUES ('migrated_v1', ?)", (_now(),))
        return count


def _chunks(text: str, size: int) -> List[str]:
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for line in text.splitlines():
        while len(line) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out if c.strip()]
