"""Mode-separated corpus with transactional overlays and an immutable change log."""
import gzip
import json
import sqlite3
import hashlib
import copy
from contextlib import contextmanager, closing
from pathlib import Path

from .config import data_dir
from .errors import AppError
DEFAULT_DATA = data_dir()


def read_json(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


class Store:
    def __init__(self, root=DEFAULT_DATA, ledger_path=None):
        self.root = Path(root)
        self._cache = {}
        self.ledger_path = Path(ledger_path) if ledger_path is not None else self.root/'rolling.sqlite3'

    @contextmanager
    def _db(self):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.ledger_path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS matches (mode TEXT, match_id TEXT, fingerprint TEXT, payload TEXT, PRIMARY KEY(mode,match_id))')
        db.execute('CREATE TABLE IF NOT EXISTS withdrawn (mode TEXT, match_id TEXT, PRIMARY KEY(mode,match_id))')
        db.execute('''CREATE TABLE IF NOT EXISTS changes (
            revision INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, match_id TEXT,
            action TEXT, reason TEXT, before_payload TEXT, after_payload TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def fingerprint(match):
        m = copy.deepcopy(match)
        m.pop('role', None)
        m['source'].pop('local_raw_path', None)
        m['source'].pop('data_origin', None)
        return hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def _base(self, mode):
        if mode not in ('epl', 'dota2'):
            raise ValueError('mode must be epl or dota2')
        if mode not in self._cache:
            path = self.root/mode/'catalog.json'
            catalog = read_json(path) if path.exists() else {'matches': []}
            self._cache[mode] = [read_json(self.root/mode/r['file']) for r in catalog['matches']]
        return {m['match_id']: m for m in self._cache[mode]}

    def _corpus(self, mode, db=None):
        by_id = self._base(mode)
        if db is not None:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'matches' in tables:
                for row in db.execute('SELECT payload FROM matches WHERE mode=?', (mode,)):
                    m = json.loads(row[0]); by_id[m['match_id']] = m
            if 'withdrawn' in tables:
                for row in db.execute('SELECT match_id FROM withdrawn WHERE mode=?', (mode,)):
                    by_id.pop(row[0], None)
        return list(by_id.values())

    def corpus(self, mode):
        if not self.ledger_path.exists():
            return self._corpus(mode)
        with closing(sqlite3.connect(self.ledger_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            return self._corpus(mode, db)

    @staticmethod
    def _revision(db, mode, mid):
        return db.execute('SELECT COALESCE(MAX(revision),0) FROM changes WHERE mode=? AND match_id=?', (mode, mid)).fetchone()[0]

    @staticmethod
    def _log(db, mode, mid, action, reason, before, after):
        encode = lambda m: json.dumps(m, ensure_ascii=False, allow_nan=False) if m is not None else None
        cursor = db.execute('INSERT INTO changes (mode,match_id,action,reason,before_payload,after_payload) VALUES (?,?,?,?,?,?)',
                            (mode, mid, action, reason, encode(before), encode(after)))
        return cursor.lastrowid

    def ingest(self, match):
        return self.ingest_many([match])[0]

    def ingest_many(self, matches):
        """Validate all conflicts under the same write lock; commit all or nothing."""
        if not isinstance(matches, list) or not 1 <= len(matches) <= 1000:
            raise AppError('invalid_batch', 'Batch must contain 1..1000 matches')
        results = []
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {(mode, m['match_id']): m for mode in {m['mode'] for m in matches} for m in self._corpus(mode, db)}
            for match in matches:
                mode, mid = match['mode'], match['match_id']
                if db.execute('SELECT 1 FROM withdrawn WHERE mode=? AND match_id=?', (mode, mid)).fetchone():
                    raise AppError('match_withdrawn', 'Use replace with the current revision to restore a withdrawn match', 409)
                fp = self.fingerprint(match); prior = existing.get((mode, mid))
                if prior is not None and self.fingerprint(prior) != fp:
                    raise AppError('match_conflict', 'Conflicting content for existing match_id; use replace', 409)
                if prior is not None and prior['role'] in ('historical', 'committed'):
                    results.append(dict(status='already_present', mode=mode, match_id=mid)); continue
                payload = copy.deepcopy(match); payload['role'] = 'committed'
                db.execute('INSERT OR REPLACE INTO matches VALUES (?,?,?,?)', (mode, mid, fp, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
                revision = self._log(db, mode, mid, 'ingest', 'Completed match ingestion', prior, payload)
                existing[(mode, mid)] = payload
                results.append(dict(status='committed', mode=mode, match_id=mid, eligible_after=payload['end_order'], revision=revision))
        return results

    def history_status(self, mode, mid):
        """Read-only, including tombstones; never creates a database."""
        mid = str(mid)
        def inspect(db=None):
            active = next((m for m in self._corpus(mode, db) if m['match_id'] == mid), None)
            changes = []
            if db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name='changes'").fetchone():
                changes = [dict(zip(('revision', 'action', 'reason', 'created_at'), row)) for row in db.execute(
                    'SELECT revision,action,reason,created_at FROM changes WHERE mode=? AND match_id=? ORDER BY revision', (mode, mid))]
            if active is None and not changes:
                raise AppError('match_not_found', 'Match not found in this mode', 404)
            return dict(mode=mode, match_id=mid, status='active' if active else 'withdrawn',
                        revision=changes[-1]['revision'] if changes else 0,
                        fingerprint=self.fingerprint(active) if active else None, changes=changes)
        if not self.ledger_path.exists():
            return inspect()
        with closing(sqlite3.connect(self.ledger_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            return inspect(db)

    def change(self, mode, mid, expected_revision, reason, replacement=None):
        """CAS revision prevents stale edits/ABA; base JSON is never overwritten."""
        self._base(mode); mid = str(mid)
        if type(expected_revision) is not int or expected_revision < 0:
            raise AppError('invalid_revision', 'expected_revision must be a nonnegative integer')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise AppError('invalid_reason', 'Supply a reason of 1..500 characters')
        if replacement is not None and (replacement['mode'] != mode or replacement['match_id'] != mid):
            raise AppError('match_id_mismatch', 'Replacement must retain the same mode and match_id')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            corpus = self._corpus(mode, db)
            before = next((m for m in corpus if m['match_id'] == mid), None)
            revision = self._revision(db, mode, mid)
            if before is None and not revision:
                raise AppError('match_not_found', 'Match not found in this mode', 404)
            if revision != expected_revision:
                raise AppError('revision_conflict', 'Match changed; read history-status and review before retrying', 409)
            if before is None and replacement is None:
                return dict(status='already_withdrawn', mode=mode, match_id=mid, revision=revision, affected_matches=[])
            if replacement is not None:
                after = copy.deepcopy(replacement)
                after['role'] = before['role'] if before else 'committed'
                # Restoring a withdrawn development match must not promote it to history.
                if before is None:
                    row = db.execute('SELECT before_payload FROM changes WHERE mode=? AND match_id=? AND action=? ORDER BY revision DESC LIMIT 1', (mode, mid, 'withdraw')).fetchone()
                    if row and row[0]: after['role'] = json.loads(row[0])['role']
                if before is not None and self.fingerprint(before) == self.fingerprint(after):
                    return dict(status='unchanged', mode=mode, match_id=mid, revision=revision, affected_matches=[])
                db.execute('INSERT OR REPLACE INTO matches VALUES (?,?,?,?)', (mode, mid, self.fingerprint(after), json.dumps(after, ensure_ascii=False, allow_nan=False)))
                db.execute('DELETE FROM withdrawn WHERE mode=? AND match_id=?', (mode, mid))
                action = 'replace' if before else 'restore'
            else:
                after = None; action = 'withdraw'
                db.execute('DELETE FROM matches WHERE mode=? AND match_id=?', (mode, mid))
                db.execute('INSERT INTO withdrawn VALUES (?,?)', (mode, mid))
            revision = self._log(db, mode, mid, action, reason.strip(), before, after)
            affected = [m['match_id'] for m in corpus if m['match_id'] == mid or any(
                x is not None and x['role'] in ('historical', 'committed') and m['partition'] == x['partition'] and x['end_order'] < m['start_order']
                for x in (before, after))]
            if after is not None and mid not in affected: affected.insert(0, mid)
            return dict(status={'replace':'replaced','restore':'restored','withdraw':'withdrawn'}[action], mode=mode, match_id=mid,
                        revision=revision, affected_matches=affected, reanalysis_required=True)

    def affected_by(self, mode, revision):
        self._base(mode)
        if type(revision) is not int or revision < 1:
            raise AppError('invalid_revision', 'revision must be a positive change revision')
        if not self.ledger_path.exists():
            raise AppError('change_not_found', 'Change revision not found', 404)
        with closing(sqlite3.connect(self.ledger_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='changes'").fetchone():
                raise AppError('change_not_found', 'Change revision not found', 404)
            row = db.execute('SELECT match_id,before_payload,after_payload FROM changes WHERE mode=? AND revision=?', (mode,revision)).fetchone()
            if row is None:raise AppError('change_not_found', 'Change revision not found', 404)
            mid, before, after = row
            versions = [json.loads(x) for x in (before,after) if x]
            corpus = self._corpus(mode, db)
            targets = [m['match_id'] for m in corpus if m['match_id']==mid or any(
                x['role'] in ('historical','committed') and x['partition']==m['partition'] and x['end_order']<m['start_order'] for x in versions)]
            return targets, corpus

    def match(self, mode, match_id):
        for m in self.corpus(mode):
            if m['match_id'] == str(match_id):
                return m
        raise AppError('match_not_found', 'Match not found in this mode', 404)

    def analysis_inputs(self, mode, mid, current=None):
        corpus = self.corpus(mode)
        m = current if current is not None else next((x for x in corpus if x['match_id'] == str(mid)), None)
        if m is None:
            raise AppError('match_not_found', 'Match not found in this mode', 404)
        history = [x for x in corpus if x['role'] in ('historical', 'committed') and x['match_id'] != m['match_id']
                   and x['partition'] == m['partition'] and x['end_order'] < m['start_order']]
        return m, history

    def history(self, current):
        return [m for m in self.corpus(current['mode']) if m['role'] in ('historical','committed')
                and m['match_id'] != current['match_id'] and m['partition'] == current['partition']
                and m['end_order'] < current['start_order']]

    def list_matches(self, mode, role='development'):
        return [{k:m[k] for k in ('match_id','title','date','phases','role')} for m in self.corpus(mode) if role=='all' or m['role']==role]
