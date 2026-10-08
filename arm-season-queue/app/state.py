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
            CREATE TABLE IF NOT EXISTS rejected_rips (
              id TEXT PRIMARY KEY, event TEXT NOT NULL UNIQUE, created REAL NOT NULL,
              drive TEXT NOT NULL, job INTEGER, review_status TEXT NOT NULL,
              ejection_status TEXT NOT NULL, body TEXT NOT NULL);
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
            old_rejections = list(db.execute("""SELECT r.id,r.body FROM rejected_rips r
                JOIN events e ON e.id=r.event
                WHERE e.job IS NULL AND e.status IN ('ready','rejected_for_review')"""))
            for rejection in old_rejections:
                body = json.loads(rejection['body'])
                body.setdefault('audit_trail', []).append({
                    'at': time.time(), 'action': 'prior_presentation_superseded',
                    'detail': 'A new presentation arrived before this rejected item was inserted; prior unpaired evidence, including failed ejections, cannot claim the new insertion.',
                })
                body.setdefault('ejection', {})['status'] = 'superseded_before_insertion'
                db.execute("UPDATE rejected_rips SET ejection_status='superseded_before_insertion',body=? WHERE id=?",
                           (encode(body), rejection['id']))
                db.execute("UPDATE events SET status='invalidated' WHERE id=(SELECT event FROM rejected_rips WHERE id=?) AND job IS NULL",
                           (rejection['id'],))
            db.execute("UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ('processing','ready','review','rejected_for_review')")
            db.execute('INSERT INTO events(id,created,status,body) VALUES (?,?,?,?)',
                       (event, time.time(), 'processing', encode({'source': source})))
        return event

    def add_rejection(self, event, drive, body, *, job=None):
        rejection_id = str(uuid4())
        now = time.time()
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO rejected_rips
                (id,event,created,drive,job,review_status,ejection_status,body)
                VALUES (?,?,?,?,?,?,?,?)''',
                (rejection_id, event, now, drive, job, 'pending',
                 'pending' if job is not None else 'awaiting_disc', encode(body)))
            row = db.execute('SELECT * FROM rejected_rips WHERE event=?', (event,)).fetchone()
            return dict(row)

    def update_rejection(self, rejection_id, *, job=None, review_status=None, ejection_status=None, body=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM rejected_rips WHERE id=?', (rejection_id,)).fetchone()
            if not row:
                raise ValueError('Rejected-rip record not found')
            db.execute('''UPDATE rejected_rips SET job=?,review_status=?,ejection_status=?,body=? WHERE id=?''', (
                row['job'] if job is None else job,
                row['review_status'] if review_status is None else review_status,
                row['ejection_status'] if ejection_status is None else ejection_status,
                row['body'] if body is None else encode(body), rejection_id,
            ))

    def rejection(self, rejection_id):
        rows = self.rows('SELECT * FROM rejected_rips WHERE id=?', (rejection_id,))
        if not rows:
            return None
        rows[0]['body'] = json.loads(rows[0]['body'])
        return rows[0]

    def rejections(self, status='all'):
        if status == 'pending':
            rows = self.rows("SELECT * FROM rejected_rips WHERE review_status NOT IN ('resolved','ignored') ORDER BY created DESC")
        elif status == 'resolved':
            rows = self.rows("SELECT * FROM rejected_rips WHERE review_status IN ('resolved','ignored') ORDER BY created DESC")
        elif status == 'all':
            rows = self.rows('SELECT * FROM rejected_rips ORDER BY created DESC')
        else:
            raise ValueError('Filter status must be pending, resolved, or all')
        for row in rows:
            row['body'] = json.loads(row['body'])
        return rows

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
            candidates = list(db.execute("SELECT id,status FROM events WHERE job IS NULL AND status IN ('processing','ready','rejected_for_review') AND created>=? AND created<=? AND released=1", (now-ttl, now)))
            if len(candidates) == 1:
                db.execute('UPDATE events SET job=? WHERE id=?', (job, candidates[0]['id']))
                db.execute('UPDATE rejected_rips SET job=? WHERE event=? AND job IS NULL',
                           (job, candidates[0]['id']))
            else:
                db.execute("UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ('processing','ready','rejected_for_review')")
                for candidate in candidates:
                    if candidate['status'] == 'rejected_for_review':
                        row = db.execute('SELECT id,body FROM rejected_rips WHERE event=?', (candidate['id'],)).fetchone()
                        if row:
                            body = json.loads(row['body'])
                            body.setdefault('audit_trail', []).append({
                                'at': now, 'action': 'insertion_pairing_ambiguous',
                                'detail': 'No unique capture-to-insertion association; no automatic eject was attempted.',
                            })
                            body.setdefault('ejection', {})['status'] = 'pairing_ambiguous'
                            db.execute("UPDATE rejected_rips SET ejection_status='pairing_ambiguous',body=? WHERE id=?",
                                       (encode(body), row['id']))

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

    def invalidate_pending(self, *, include_rejections=False):
        now = time.time()
        with self.connect() as db:
            rows = []
            if include_rejections:
                rows = list(db.execute('''SELECT r.id,r.body FROM rejected_rips r JOIN events e ON e.id=r.event
                                          WHERE e.job IS NULL AND e.status IN ('ready','rejected_for_review') '''))
            for row in rows:
                body = json.loads(row['body'])
                body.setdefault('ejection', {})['status'] = 'capture_invalidated'
                body.setdefault('audit_trail', []).append({
                    'at': now, 'action': 'capture_invalidated_before_insertion',
                    'detail': 'Capture was invalidated; it cannot be paired with or eject a later insertion.',
                })
                db.execute("UPDATE rejected_rips SET ejection_status='capture_invalidated',body=? WHERE id=?",
                           (encode(body), row['id']))
            states = "'processing','ready','review','rejected_for_review'" if include_rejections else "'processing','ready','review'"
            db.execute(f"UPDATE events SET status='invalidated' WHERE job IS NULL AND status IN ({states})")
