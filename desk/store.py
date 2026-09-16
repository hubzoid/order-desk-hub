"""The desk's own records, in one SQLite file in the hub's data folder.

The SDK owns work items, operations, approvals, intake items and files. The
desk keeps only what it adds: the case (one per incoming document), its
immutable proposal revisions with their evidence, the ERP documents it
created, duplicate keys and sender bindings, and an append-only event log.

Every change is a compare-and-set on the case `revision`, so two operators can
never both win a claim, and two writers never overwrite each other.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .paths import data_dir

STATES = ("received", "needs_owner", "needs_clarification", "draft_ready", "erp_draft_saved", "waiting_approval",
          "submitting", "done", "duplicate", "rejected", "cancelled", "error", "ambiguous")
OPEN = ("received", "needs_owner", "needs_clarification", "draft_ready", "erp_draft_saved", "waiting_approval",
        "submitting", "error", "ambiguous")
_JSON = ("question_json", "differences_json", "sender_json", "flags_json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_cases (
  id TEXT PRIMARY KEY, hub_id TEXT NOT NULL, number INTEGER NOT NULL, kind TEXT, source TEXT NOT NULL,
  source_event_id TEXT NOT NULL, intake_id TEXT, triage_work_item_id TEXT, channel TEXT, sender_json TEXT,
  received_at TEXT, company TEXT, party_type TEXT, party_id TEXT, party_bound_by TEXT, state TEXT NOT NULL,
  owner TEXT, generation INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1, policy_digest TEXT,
  business_reference TEXT, duplicate_of TEXT, proposal_revision INTEGER, erp_doctype TEXT, erp_name TEXT,
  erp_docstatus INTEGER, erp_modified TEXT, approval_digest TEXT, content_digest TEXT, error_code TEXT,
  error_message TEXT, question_json TEXT, differences_json TEXT, flags_json TEXT, preview INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE (hub_id, source_event_id));
CREATE UNIQUE INDEX IF NOT EXISTS desk_cases_number ON desk_cases (hub_id, number);
CREATE INDEX IF NOT EXISTS desk_cases_state ON desk_cases (hub_id, state);
CREATE TABLE IF NOT EXISTS desk_documents (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, ordinal INTEGER NOT NULL, label TEXT NOT NULL, filename TEXT,
  media_type TEXT, sha256 TEXT, size INTEGER, file_ref TEXT, parser TEXT, parse_status TEXT NOT NULL,
  error_code TEXT, elements_json TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS desk_proposals (
  case_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT, payload_json TEXT, digest TEXT, evidence_json TEXT,
  questions_json TEXT, warnings_json TEXT, extraction_json TEXT, corrections_json TEXT, policy_digest TEXT,
  erp_snapshot_json TEXT, created_by TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL,
  PRIMARY KEY (case_id, revision));
CREATE TABLE IF NOT EXISTS desk_erp_links (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, proposal_revision INTEGER, action TEXT NOT NULL, work_item_id TEXT,
  operation_id TEXT, erp_doctype TEXT, erp_name TEXT, erp_modified TEXT, docstatus INTEGER, digest TEXT,
  state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE (case_id, work_item_id));
CREATE TABLE IF NOT EXISTS desk_duplicate_keys (
  hub_id TEXT NOT NULL, company TEXT NOT NULL, kind TEXT NOT NULL, party_id TEXT NOT NULL, reference TEXT NOT NULL,
  case_id TEXT NOT NULL, content_digest TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (hub_id, company, kind, party_id, reference));
CREATE TABLE IF NOT EXISTS desk_bindings (
  company TEXT NOT NULL, channel_kind TEXT NOT NULL, sender TEXT NOT NULL, party_type TEXT NOT NULL,
  party_id TEXT NOT NULL, source_ref TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0, created_by TEXT,
  created_at REAL NOT NULL, revoked_at REAL, PRIMARY KEY (company, channel_kind, sender));
CREATE TABLE IF NOT EXISTS desk_events (
  id TEXT PRIMARY KEY, case_id TEXT, revision INTEGER, type TEXT NOT NULL, actor TEXT, payload_json TEXT,
  at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS desk_events_case ON desk_events (case_id, at);
CREATE TABLE IF NOT EXISTS desk_meta (key TEXT PRIMARY KEY, value TEXT);
"""


class StoreError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, **fields):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.fields = {k: v for k, v in fields.items() if v is not None}

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.fields}

    def __reduce__(self):
        return (_rebuild, (self.code, self.message, self.status, self.fields))


def _rebuild(code, message, status, fields):
    return StoreError(code, message, status=status, **fields)


def now() -> float:
    return time.time()


def new_id() -> str:
    return str(uuid.uuid4())


def dumps(v) -> str:
    return json.dumps(v, sort_keys=True, default=str, separators=(",", ":"))


class Store:
    def __init__(self, hub_dir):
        self.hub_dir = Path(hub_dir)
        self.path = data_dir(self.hub_dir) / "desk.sqlite"
        with closing(self._connect()) as c:
            c.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def tx(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # -- rows -----------------------------------------------------------------------------------
    @staticmethod
    def _case(row) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for col in _JSON:
            out[col] = json.loads(out[col]) if out.get(col) else None
        return out

    def get_case(self, case_id: str, *, conn=None) -> dict | None:
        sql = "SELECT * FROM desk_cases WHERE id=? OR ('CASE-' || printf('%06d', number))=?"
        if conn is not None:
            return self._case(conn.execute(sql, (case_id, case_id)).fetchone())
        with self.read() as c:
            return self._case(c.execute(sql, (case_id, case_id)).fetchone())

    def require_case(self, case_id: str, hub_id: str) -> dict:
        case = self.get_case(case_id)
        if case is None or case["hub_id"] != hub_id:
            raise StoreError("not_found", "No such case here, or you can't see it.", status=404)
        return case

    def find_by_source(self, hub_id: str, source_event_id: str) -> dict | None:
        with self.read() as c:
            return self._case(c.execute("SELECT * FROM desk_cases WHERE hub_id=? AND source_event_id=?",
                                        (hub_id, source_event_id)).fetchone())

    def create_case(self, *, hub_id: str, source: str, source_event_id: str, **fields) -> tuple[dict, bool]:
        """One case per incoming document: the same source event returns the
        existing case."""
        with self.tx() as c:
            existing = c.execute("SELECT * FROM desk_cases WHERE hub_id=? AND source_event_id=?",
                                 (hub_id, source_event_id)).fetchone()
            if existing is not None:
                return self._case(existing), False
            number = (c.execute("SELECT MAX(number) FROM desk_cases WHERE hub_id=?", (hub_id,)).fetchone()[0] or 0) + 1
            cid, t = new_id(), now()
            cols = {"id": cid, "hub_id": hub_id, "number": number, "source": source, "source_event_id": source_event_id,
                    "state": fields.pop("state", "received"), "created_at": t, "updated_at": t}
            for k, v in fields.items():
                cols[k] = dumps(v) if k in _JSON and v is not None else v
            c.execute(f"INSERT INTO desk_cases ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      tuple(cols.values()))
            self._event(c, cid, 1, "case.received", fields.get("triage_work_item_id") and "intake" or source,
                        {"source": source})
            return self._case(c.execute("SELECT * FROM desk_cases WHERE id=?", (cid,)).fetchone()), True

    def update_case(self, case_id: str, *, expected_revision: int | None = None, expect_state=None,
                    event: str | None = None, actor: str | None = None, payload: dict | None = None,
                    conn=None, **fields) -> dict:
        def run(c):
            cur = c.execute("SELECT revision, state FROM desk_cases WHERE id=?", (case_id,)).fetchone()
            if cur is None:
                raise StoreError("not_found", "No such case.", status=404)
            if expected_revision is not None and int(expected_revision) != cur["revision"]:
                raise StoreError("revision_conflict", "Someone changed this case since you loaded it. Reload and "
                                 "try again.", status=409, current_revision=cur["revision"])
            if expect_state is not None:
                states = (expect_state,) if isinstance(expect_state, str) else tuple(expect_state)
                if cur["state"] not in states:
                    raise StoreError("invalid_state", f"This case is {cur['state']}.", status=409,
                                     current_revision=cur["revision"], state=cur["state"])
            sets, params = ["revision=revision+1", "updated_at=?"], [now()]
            for k, v in fields.items():
                sets.append(f"{k}=?")
                params.append(dumps(v) if k in _JSON and v is not None else v)
            params += [case_id, cur["revision"]]
            done = c.execute(f"UPDATE desk_cases SET {', '.join(sets)} WHERE id=? AND revision=?", params)
            if done.rowcount != 1:
                raise StoreError("revision_conflict", "Someone changed this case a moment ago.", status=409)
            if event:
                self._event(c, case_id, cur["revision"] + 1, event, actor, payload)
            return self._case(c.execute("SELECT * FROM desk_cases WHERE id=?", (case_id,)).fetchone())
        if conn is not None:
            return run(conn)
        with self.tx() as c:
            return run(c)

    def list_cases(self, hub_id: str, *, state: str | None = None, owner: str | None = None, limit: int = 25,
                   before: int | None = None, include_preview: bool = False) -> list[dict]:
        where, params = ["hub_id=?"], [hub_id]
        if state:
            where.append("state=?")
            params.append(state)
        if owner:
            where.append("owner=?")
            params.append(owner)
        if before:
            where.append("number<?")
            params.append(before)
        if not include_preview:
            where.append("preview=0")
        params.append(max(1, min(int(limit), 100)))
        with self.read() as c:
            rows = c.execute(f"SELECT * FROM desk_cases WHERE {' AND '.join(where)} ORDER BY number DESC LIMIT ?",
                             params).fetchall()
        return [self._case(r) for r in rows]

    def counts(self, hub_id: str) -> dict:
        with self.read() as c:
            rows = c.execute("SELECT state, COUNT(*), MIN(created_at) FROM desk_cases WHERE hub_id=? AND preview=0 "
                             "GROUP BY state", (hub_id,)).fetchall()
        return {r[0]: {"count": r[1], "oldest": r[2]} for r in rows}

    # -- documents and proposals ---------------------------------------------------------------------
    def add_document(self, case_id: str, **fields) -> dict:
        with self.tx() as c:
            existing = c.execute("SELECT * FROM desk_documents WHERE case_id=? AND ordinal=?",
                                 (case_id, fields["ordinal"])).fetchone()
            if existing is not None:
                c.execute("DELETE FROM desk_documents WHERE id=?", (existing["id"],))
            row = {"id": new_id(), "case_id": case_id, "created_at": now(), **fields}
            if "elements_json" in row and not isinstance(row["elements_json"], str):
                row["elements_json"] = dumps(row["elements_json"])
            c.execute(f"INSERT INTO desk_documents ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                      tuple(row.values()))
            return row

    def documents(self, case_id: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute("SELECT * FROM desk_documents WHERE case_id=? ORDER BY ordinal", (case_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["elements"] = json.loads(d.pop("elements_json") or "[]")
            out.append(d)
        return out

    def add_proposal(self, case_id: str, *, created_by: str, reason: str, conn=None, **fields) -> int:
        def run(c):
            rev = (c.execute("SELECT MAX(revision) FROM desk_proposals WHERE case_id=?", (case_id,)).fetchone()[0] or 0) + 1
            row = {"case_id": case_id, "revision": rev, "created_by": created_by, "reason": reason, "created_at": now()}
            for k, v in fields.items():
                row[k] = dumps(v) if k.endswith("_json") and not isinstance(v, str) and v is not None else v
            c.execute(f"INSERT INTO desk_proposals ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                      tuple(row.values()))
            return rev
        if conn is not None:
            return run(conn)
        with self.tx() as c:
            return run(c)

    def proposal(self, case_id: str, revision: int | None = None) -> dict | None:
        with self.read() as c:
            if revision is None:
                row = c.execute("SELECT * FROM desk_proposals WHERE case_id=? ORDER BY revision DESC LIMIT 1",
                                (case_id,)).fetchone()
            else:
                row = c.execute("SELECT * FROM desk_proposals WHERE case_id=? AND revision=?",
                                (case_id, revision)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for k in list(out):
            if k.endswith("_json"):
                out[k[:-5]] = json.loads(out.pop(k)) if out.get(k) else None
        return out

    def proposals(self, case_id: str) -> list[dict]:
        with self.read() as c:
            revs = [r[0] for r in c.execute("SELECT revision FROM desk_proposals WHERE case_id=? ORDER BY revision",
                                            (case_id,))]
        return [self.proposal(case_id, r) for r in revs]

    # -- ERP links ------------------------------------------------------------------------------------
    def link(self, case_id: str, *, work_item_id: str, action: str, insert_only: bool = False, **fields) -> dict:
        """Record or update the link from a case to one of its work items.
        `insert_only`: the caller that admitted the work item records it only if
        the work item has not already recorded its own outcome (a fast worker
        can finish before the admitting call returns)."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM desk_erp_links WHERE case_id=? AND work_item_id=?",
                            (case_id, work_item_id)).fetchone()
            t = now()
            if row is not None and insert_only:
                return dict(row)
            if row is None:
                data = {"id": new_id(), "case_id": case_id, "work_item_id": work_item_id, "action": action,
                        "state": fields.pop("state", "requested"), "created_at": t, "updated_at": t, **fields}
                c.execute(f"INSERT INTO desk_erp_links ({','.join(data)}) VALUES ({','.join('?' * len(data))})",
                          tuple(data.values()))
            elif fields:
                sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
                c.execute(f"UPDATE desk_erp_links SET {sets} WHERE id=?", (*fields.values(), t, row["id"]))
            return dict(c.execute("SELECT * FROM desk_erp_links WHERE case_id=? AND work_item_id=?",
                                  (case_id, work_item_id)).fetchone())

    def links(self, case_id: str) -> list[dict]:
        with self.read() as c:
            return [dict(r) for r in c.execute("SELECT * FROM desk_erp_links WHERE case_id=? ORDER BY created_at",
                                               (case_id,))]

    # -- duplicates ------------------------------------------------------------------------------------
    def claim_business_key(self, *, hub_id: str, company: str, kind: str, party_id: str, reference: str,
                           case_id: str, content_digest: str) -> dict:
        """Record this case as the owner of a business key, or return the case
        that already holds it."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM desk_duplicate_keys WHERE hub_id=? AND company=? AND kind=? AND party_id=? "
                            "AND reference=?", (hub_id, company, kind, party_id, reference)).fetchone()
            if row is None:
                c.execute("INSERT INTO desk_duplicate_keys VALUES (?,?,?,?,?,?,?,?)",
                          (hub_id, company, kind, party_id, reference, case_id, content_digest, now()))
                return {"case_id": case_id, "content_digest": content_digest, "new": True}
            if row["case_id"] == case_id and row["content_digest"] != content_digest:
                c.execute("UPDATE desk_duplicate_keys SET content_digest=? WHERE hub_id=? AND company=? AND kind=? AND "
                          "party_id=? AND reference=?", (content_digest, hub_id, company, kind, party_id, reference))
                return {"case_id": case_id, "content_digest": content_digest, "new": False}
            return {"case_id": row["case_id"], "content_digest": row["content_digest"], "new": False}

    def release_business_key(self, case_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM desk_duplicate_keys WHERE case_id=?", (case_id,))

    # -- bindings -----------------------------------------------------------------------------------------
    def bind(self, *, company: str, channel_kind: str, sender: str, party_type: str, party_id: str, source_ref: str,
             actor: str, synthetic: bool = False) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO desk_bindings (company, channel_kind, sender, party_type, party_id, source_ref, "
                      "synthetic, created_by, created_at, revoked_at) VALUES (?,?,?,?,?,?,?,?,?,NULL) "
                      "ON CONFLICT (company, channel_kind, sender) DO UPDATE SET party_type=excluded.party_type, "
                      "party_id=excluded.party_id, source_ref=excluded.source_ref, synthetic=excluded.synthetic, "
                      "created_by=excluded.created_by, created_at=excluded.created_at, revoked_at=NULL",
                      (company, channel_kind, sender.strip().lower(), party_type, party_id, source_ref,
                       1 if synthetic else 0, actor, now()))
            self._event(c, None, None, "binding.set", actor, {"channel_kind": channel_kind, "party_id": party_id})

    def revoke_binding(self, *, company: str, channel_kind: str, sender: str, actor: str) -> bool:
        with self.tx() as c:
            done = c.execute("UPDATE desk_bindings SET revoked_at=? WHERE company=? AND channel_kind=? AND sender=? "
                             "AND revoked_at IS NULL", (now(), company, channel_kind, sender.strip().lower())).rowcount
            if done:
                self._event(c, None, None, "binding.revoked", actor, {"channel_kind": channel_kind})
            return bool(done)

    def binding(self, company: str, channel_kind: str, sender: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM desk_bindings WHERE company=? AND channel_kind=? AND sender=? AND "
                            "revoked_at IS NULL", (company, channel_kind, (sender or "").strip().lower())).fetchone()
        return dict(row) if row else None

    def bindings(self, company: str) -> list[dict]:
        with self.read() as c:
            return [dict(r) for r in c.execute("SELECT * FROM desk_bindings WHERE company=? ORDER BY channel_kind, "
                                               "sender", (company,))]

    # -- events -------------------------------------------------------------------------------------------
    def _event(self, c, case_id, revision, type_, actor, payload):
        c.execute("INSERT INTO desk_events (id, case_id, revision, type, actor, payload_json, at) VALUES (?,?,?,?,?,?,?)",
                  (new_id(), case_id, revision, type_, actor, dumps(payload or {}), now()))

    def event(self, case_id, type_, actor, payload=None, revision=None):
        with self.tx() as c:
            self._event(c, case_id, revision, type_, actor, payload)

    def events(self, case_id: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute("SELECT * FROM desk_events WHERE case_id=? ORDER BY at", (case_id,)).fetchall()
        return [dict(r, payload=json.loads(r["payload_json"] or "{}")) for r in rows]

    def meta(self, key: str, default=None):
        with self.read() as c:
            row = c.execute("SELECT value FROM desk_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO desk_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                      (key, dumps(value)))


def case_number(case: dict) -> str:
    return f"CASE-{case['number']:06d}"
