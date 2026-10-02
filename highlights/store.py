"""Local, mode-separated corpus. Historical and demo roles are explicit."""
import gzip
import json
import sqlite3
import hashlib
import copy
from contextlib import contextmanager,closing
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
        self.ledger_path=Path(ledger_path) if ledger_path is not None else self.root/'rolling.sqlite3'

    @contextmanager
    def _db(self):
        self.ledger_path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.ledger_path,timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS matches (mode TEXT, match_id TEXT, fingerprint TEXT, payload TEXT, PRIMARY KEY(mode,match_id))')
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def fingerprint(match):
        m=copy.deepcopy(match);m.pop('role',None)
        m['source'].pop('local_raw_path',None)
        return hashlib.sha256(json.dumps(m,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def ingest(self,match):
        return self.ingest_many([match])[0]

    def ingest_many(self,matches):
        """Commit a fully adapter-validated batch atomically and idempotently."""
        if not isinstance(matches,list) or not 1<=len(matches)<=1000:
            raise AppError('invalid_batch','Batch must contain 1..1000 matches')
        existing={}
        for mode in {m['mode'] for m in matches}:
            existing.update({(mode,m['match_id']):m for m in self.corpus(mode)})
        prepared=[];seen={}
        for match in matches:
            key=(match['mode'],match['match_id']);fp=self.fingerprint(match)
            if key in seen and seen[key]!=fp:
                raise AppError('match_conflict','Conflicting duplicate within batch',409)
            seen[key]=fp
            prior=existing.get(key)
            if prior is not None and self.fingerprint(prior)!=fp:
                raise AppError('match_conflict','Conflicting content for existing match_id',409)
            payload=copy.deepcopy(match);payload['role']='committed'
            prepared.append((key,fp,payload,prior is not None and prior['role'] in ('historical','committed')))
        results=[]
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            for (mode,mid),fp,payload,already in prepared:
                row=db.execute('SELECT fingerprint FROM matches WHERE mode=? AND match_id=?',(mode,mid)).fetchone()
                if row and row[0]!=fp:
                    raise AppError('match_conflict','Conflicting content for existing match_id',409)
                if already or row:
                    results.append(dict(status='already_present',mode=mode,match_id=mid));continue
                db.execute('INSERT INTO matches VALUES (?,?,?,?)',(mode,mid,fp,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
                results.append(dict(status='committed',mode=mode,match_id=mid,eligible_after=payload['end_order']))
        return results

    def corpus(self, mode):
        if mode not in ('epl', 'dota2'):
            raise ValueError('mode must be epl or dota2')
        if mode not in self._cache:
            catalog_path=self.root/mode/'catalog.json'
            catalog = read_json(catalog_path) if catalog_path.exists() else {'matches':[]}
            self._cache[mode] = [read_json(self.root / mode / r['file']) for r in catalog['matches']]
        by_id={m['match_id']:m for m in self._cache[mode]}
        if self.ledger_path.exists():
            with closing(sqlite3.connect(self.ledger_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                # Another writer may have created the SQLite file but not yet
                # committed its schema. Its transaction will arbitrate inserts.
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
                if exists:
                    for row in db.execute('SELECT payload FROM matches WHERE mode=?',(mode,)):
                        m=json.loads(row[0]);by_id[m['match_id']]=m
        return list(by_id.values())

    def match(self, mode, match_id):
        for m in self.corpus(mode):
            if m['match_id'] == str(match_id):
                return m
        raise AppError('match_not_found','Match not found in this mode',404)

    def history(self, current):
        return [m for m in self.corpus(current['mode']) if m['role'] in ('historical','committed')
                and m['match_id']!=current['match_id'] and m['partition']==current['partition']
                and m['end_order'] < current['start_order']]

    def list_matches(self, mode, role='development'):
        return [{k:m[k] for k in ('match_id','title','date','phases','role')} for m in self.corpus(mode) if role=='all' or m['role']==role]
