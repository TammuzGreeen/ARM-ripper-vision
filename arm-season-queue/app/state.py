import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS masters (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, master TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
              released INTEGER NOT NULL DEFAULT 0, job INTEGER UNIQUE, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen (
              job INTEGER PRIMARY KEY, identity TEXT NOT NULL, first_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS reservations (
              job INTEGER PRIMARY KEY, batch TEXT NOT NULL, disc TEXT NOT NULL,
              event TEXT NOT NULL UNIQUE, state TEXT NOT NULL, publication TEXT NOT NULL DEFAULT 'pending',
              body TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS disc_claim ON reservations(batch, disc)
              WHERE state NOT IN ('failed', 'cancelled');
            CREATE TABLE IF NOT EXISTS skipped (batch TEXT, disc TEXT, PRIMARY KEY(batch, disc));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as db:
            db.execute(sql, args)

    def get(self, key, default=None):
        rows = self.rows('SELECT value FROM kv WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def put(self, key, value):
        self.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, encode(value)))

    def new_event(self, source='camera'):
        event = str(uuid4())
        with self.connect() as db:
            # A new, possibly unreadable presentation invalidates old unused evidence.
            db.execute("UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ('processing','ready','review')")
            db.execute('INSERT INTO events(id,created,status,body) VALUES (?,?,?,?)',
                       (event, time.time(), 'processing', encode({'source': source})))
        return event

    def pair_insertion(self, job, identity, ttl):
        """One capture -> one observed insertion, even if OCR is still running."""
        now = time.time()
        with self.connect() as db:
            prior = db.execute('SELECT * FROM seen WHERE job=?', (job,)).fetchone()
            if prior:
                if prior['identity'] != identity:
                    raise ValueError('ARM job ID reused / database replaced; establish a new state directory')
                return
            db.execute('INSERT INTO seen VALUES (?,?,?)', (job, identity, now))
            candidates = list(db.execute("SELECT id FROM events WHERE job IS NULL AND status IN ('processing','ready') AND created>=? AND created<=? AND released=1", (now-ttl, now)))
            if len(candidates) == 1:
                db.execute('UPDATE events SET job=? WHERE id=?', (job, candidates[0]['id']))
            else:
                db.execute("UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ('processing','ready')")

    def claim(self, job, batch, disc, event, body):
        with self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (event,)).fetchone()
            if not row or row['job'] != job or row['status'] != 'ready':
                raise ValueError('Recognition is not ready for this insertion')
            if db.execute('SELECT 1 FROM skipped WHERE batch=? AND disc=?', (batch, disc)).fetchone():
                raise ValueError('Disc is skipped; unskip before processing')
            db.execute('INSERT INTO reservations(job,batch,disc,event,state,body) VALUES (?,?,?,?,?,?)',
                       (job, batch, disc, event, 'reserved', encode(body)))
            db.execute("UPDATE events SET status='bound' WHERE id=?", (event,))

    def invalidate_pending(self):
        self.execute("UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ('processing','ready','review')")
