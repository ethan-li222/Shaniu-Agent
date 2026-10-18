import sqlite3
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    emotion TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    turn_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS external_tasks (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    subject TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS external_task_messages (
                    task_id TEXT NOT NULL REFERENCES external_tasks(task_id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    PRIMARY KEY(task_id,message_id));
                CREATE TABLE IF NOT EXISTS external_task_results (
                    task_id TEXT PRIMARY KEY REFERENCES external_tasks(task_id) ON DELETE CASCADE,
                    data TEXT NOT NULL, updated_at REAL NOT NULL);
            """)
            if 'avatar_id' not in {row[1] for row in db.execute('PRAGMA table_info(conversations)')}:
                db.execute("ALTER TABLE conversations ADD COLUMN avatar_id TEXT NOT NULL DEFAULT 'wav2lip_avatar_long_hair_girl'")
            db.execute('PRAGMA user_version=3')
            db.execute("UPDATE messages SET status='cancelled' WHERE status='streaming'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner, emotion):
        record = dict(id=str(uuid4()), title="新的相遇", emotion=emotion, created_at=now(), avatar_id='wav2lip_avatar_long_hair_girl')
        with self.connect() as db:
            db.execute("INSERT INTO conversations (id,owner,title,emotion,created_at,avatar_id) VALUES (?,?,?,?,?,?)", (record['id'], owner, record['title'], emotion, record['created_at'], record['avatar_id']))
        return record

    def conversation(self, cid, owner):
        with self.connect() as db:
            row = db.execute("SELECT id,title,emotion,created_at,avatar_id FROM conversations WHERE id=? AND owner=?", (cid, owner)).fetchone()
        return dict(row) if row else None

    def conversations(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,title,emotion,created_at,avatar_id FROM conversations WHERE owner=? ORDER BY created_at DESC", (owner,))]

    def messages(self, cid):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid", (cid,))]

    def add_message(self, cid, tid, role, text, status="done"):
        mid = str(uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (mid, cid, tid, role, text, status, now()))
            if role == "user":
                db.execute("UPDATE conversations SET title=? WHERE id=? AND title='新的相遇'", (text[:22], cid))
        return mid

    def finish(self, mid, text, status):
        with self.connect() as db:
            db.execute("UPDATE messages SET content=?,status=? WHERE id=?", (text, status, mid))

    def emotion(self, cid, emotion):
        with self.connect() as db:
            db.execute("UPDATE conversations SET emotion=? WHERE id=?", (emotion, cid))

    def delete(self, cid):
        with self.connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (cid,))

    def avatar(self, cid, avatar_id):
        with self.connect() as db:
            db.execute('UPDATE conversations SET avatar_id=? WHERE id=?', (avatar_id, cid))

    def memories(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,content,created_at FROM memories WHERE owner=? ORDER BY created_at DESC", (owner,))]

    def remember(self, owner, content):
        item = dict(id=str(uuid4()), content=content, created_at=now())
        with self.connect() as db:
            db.execute("INSERT INTO memories VALUES (?,?,?,?)", (item['id'], owner, content, item['created_at']))
        return item

    def forget(self, owner, mid):
        with self.connect() as db:
            return db.execute("DELETE FROM memories WHERE owner=? AND id=?", (owner, mid)).rowcount > 0

    def external_task(self, owner, task_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM external_tasks WHERE owner=? AND task_id=?', (owner, task_id)).fetchone()
        return dict(row) if row else None

    def save_external_task(self, owner, task_id, kind, subject):
        with self.connect() as db:
            db.execute('INSERT INTO external_tasks VALUES (?,?,?,?,?)', (task_id, owner, kind, subject, now()))

    def external_tasks(self, owner):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT task_id,kind,subject,created_at FROM external_tasks WHERE owner=? ORDER BY created_at DESC LIMIT 50', (owner,))]

    def link_external_task(self, owner, task_id, message_id):
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO external_task_messages
                SELECT t.task_id,m.id FROM external_tasks t,messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE t.task_id=? AND t.owner=? AND c.owner=? AND m.id=? AND m.role='assistant'
                AND NOT EXISTS (SELECT 1 FROM external_task_messages l JOIN messages prior ON prior.id=l.message_id
                    WHERE l.task_id=t.task_id AND prior.conversation_id=c.id) ''',
                (task_id, owner, owner, message_id))

    def conversation_external_tasks(self, owner, cid):
        if not self.conversation(cid, owner):
            return []
        # Restore cards for old answers, but only for real tasks owned by this
        # user. A UUID invented in model prose never becomes a task.
        owned = {t['task_id'] for t in self.external_tasks(owner)}
        with self.connect() as db:
            linked = {r[0] for r in db.execute('''SELECT l.task_id FROM external_task_messages l
                JOIN messages m ON m.id=l.message_id WHERE m.conversation_id=?''', (cid,))}
        for message in self.messages(cid):
            if message['role'] != 'assistant':
                continue
            for tid in re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', message['content']):
                tid = tid.lower()
                if tid in owned and tid not in linked:
                    self.link_external_task(owner, tid, message['id'])
                    linked.add(tid)
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT t.task_id,t.kind,t.subject,t.created_at,
                m.id AS message_id,m.turn_id FROM external_task_messages l
                JOIN external_tasks t ON t.task_id=l.task_id JOIN messages m ON m.id=l.message_id
                WHERE t.owner=? AND m.conversation_id=? ORDER BY m.rowid,t.created_at''', (owner, cid))]

    def cached_external_task(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT data,updated_at FROM external_task_results WHERE task_id=?', (task_id,)).fetchone()
        return (json.loads(row['data']), row['updated_at']) if row else (None, 0)

    def cache_external_task(self, task_id, data):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO external_task_results VALUES (?,?,?)',
                       (task_id, json.dumps(data, ensure_ascii=False), time.time()))
