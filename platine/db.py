"""Base SQLite : bibliotheque, memoire des artistes, journal (annulation), points a verifier, depenses IA.

Chaque morceau est rattache au dossier (cle USB) ou il se trouve : `root`. Les doublons et l'annulation ne
regardent que le dossier choisi ; la Bibliotheque montre tout, une seule fois par chanson (`song_key`).
"""
import ctypes
import json
import os
import sqlite3
import threading
import time

from .config import DB_PATH, config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    root TEXT,                      -- dossier (cle USB) ou se trouve le fichier, voir root_id()
    filename TEXT,                  -- nom actuel dans ce dossier
    song_key TEXT,                  -- meme chanson = meme cle, quel que soit le dossier
    size INTEGER, mtime REAL,
    artist TEXT, title TEXT, version TEXT, cover_of TEXT, year INTEGER,
    album TEXT, genre TEXT,
    cutoff REAL, bitrate INTEGER, quality INTEGER,
    has_art INTEGER DEFAULT 0, thumb TEXT, art_hash TEXT,
    status TEXT DEFAULT 'ok',       -- ok | doublon | abime
    added REAL, updated REAL,
    UNIQUE(root, filename)
);
CREATE TABLE IF NOT EXISTS artists (
    key TEXT PRIMARY KEY, name TEXT, genre TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY, run INTEGER, ts REAL, action TEXT, src TEXT, dst TEXT, undone INTEGER DEFAULT 0, root TEXT
);
CREATE TABLE IF NOT EXISTS review (
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, filename TEXT, question TEXT,
    suggestion TEXT, options TEXT, detail TEXT, resolved INTEGER DEFAULT 0, root TEXT
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY, ts REAL, month TEXT, usd REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def root_id(path):
    """Identifiant stable d'un dossier : numero de serie du volume + chemin sans la lettre de lecteur.

    Une cle USB peut changer de lettre (H:, I:...) d'un branchement a l'autre : elle reste reconnue.
    """
    if not path:
        return ""
    path = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    drive, rest = os.path.splitdrive(path)
    serial = ctypes.c_uint32(0)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(drive + "\\"), None, 0,
                                                          ctypes.byref(serial), None, None, None, 0)
    except (AttributeError, OSError):
        ok = 0
    return f"{serial.value:08x}{rest}" if ok and serial.value else path


def song_key(artist, title, filename):
    from . import naming
    return f"{naming.artist_key(artist or '')}|{naming.title_key(title or '')}|{naming.version_key(filename or '')}"


class DB:
    def __init__(self, path=DB_PATH):
        self._lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._migrate()
        self.con.executescript(SCHEMA)
        if "art_hash" not in [r[1] for r in self.con.execute("PRAGMA table_info(tracks)").fetchall()]:
            self.con.execute("ALTER TABLE tracks ADD COLUMN art_hash TEXT")
        self.con.execute("CREATE INDEX IF NOT EXISTS tracks_song ON tracks(song_key, status)")
        self.con.execute("CREATE INDEX IF NOT EXISTS tracks_art ON tracks(art_hash)")
        self.con.commit()
        self._root_cache = (None, "")

    def _migrate(self):
        """Ancienne base (sans dossier par morceau) : on rattache tout au dossier actuellement choisi."""
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(tracks)").fetchall()]
        if not cols or "root" in cols:
            return
        root = root_id(config["music_dir"])
        self.con.execute("ALTER TABLE tracks RENAME TO tracks_old")
        self.con.executescript(SCHEMA)
        old = [dict(r) for r in self.con.execute("SELECT * FROM tracks_old").fetchall()]
        for r in old:
            r.pop("id", None)
            r["root"], r["song_key"] = root, song_key(r.get("artist"), r.get("title"), r.get("filename"))
            keys = list(r)
            self.con.execute(f"INSERT OR IGNORE INTO tracks({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                             [r[k] for k in keys])
        self.con.execute("DROP TABLE tracks_old")
        for table in ("journal", "review"):
            if "root" not in [r[1] for r in self.con.execute(f"PRAGMA table_info({table})").fetchall()]:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN root TEXT")
                self.con.execute(f"UPDATE {table} SET root=?", (root,))
        self.con.commit()

    @property
    def root(self):
        """Identifiant du dossier actuellement choisi (mis en cache, le calcul interroge Windows)."""
        path = config["music_dir"]
        if self._root_cache[0] != path:
            self._root_cache = (path, root_id(path))
        return self._root_cache[1]

    def q(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self._lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur.lastrowid

    # --- meta ---
    def get_meta(self, k, default=None):
        r = self.one("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def set_meta(self, k, v):
        self.x("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, json.dumps(v)))

    # --- tracks (toujours dans le dossier choisi) ---
    def track(self, filename):
        return self.one("SELECT * FROM tracks WHERE root=? AND filename=?", (self.root, filename))

    def upsert_track(self, t):
        now = time.time()
        cols = ["filename", "size", "mtime", "artist", "title", "version", "cover_of", "year", "album", "genre",
                "cutoff", "bitrate", "quality", "has_art", "thumb", "status", "art_hash"]
        vals = [t.get(c) for c in cols]
        key = song_key(t.get("artist"), t.get("title"), t.get("filename"))
        with self._lock:
            ex = self.con.execute("SELECT id FROM tracks WHERE root=? AND filename=?", (self.root, t["filename"])).fetchone()
            if ex:
                self.con.execute(f"UPDATE tracks SET {', '.join(c + '=?' for c in cols)}, song_key=?, updated=? WHERE id=?",
                                 vals + [key, now, ex[0]])
            else:
                self.con.execute(f"INSERT INTO tracks({', '.join(cols)}, root, song_key, added, updated) "
                                 f"VALUES({', '.join('?' * len(cols))}, ?, ?, ?, ?)", vals + [self.root, key, now, now])
            self.con.commit()

    def rename_track(self, old, new):
        self.x("UPDATE tracks SET filename=?, updated=? WHERE root=? AND filename=?", (new, time.time(), self.root, old))

    def library_sql(self):
        """Sous-requete : un seul exemplaire par chanson, de preference celui du dossier choisi, sinon le meilleur."""
        return ("(SELECT * FROM tracks WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY "
                "COALESCE(song_key, id) ORDER BY (root=?) DESC, COALESCE(quality, 0) DESC, updated DESC) AS rn "
                "FROM tracks WHERE status='ok') WHERE rn=1))", [self.root])

    # --- journal ---
    def log(self, run, action, src, dst):
        self.x("INSERT INTO journal(run, ts, action, src, dst, root) VALUES(?,?,?,?,?,?)",
               (run, time.time(), action, src, dst, self.root))

    # --- review ---
    def add_review(self, kind, filename, question, suggestion=None, options=None, detail=""):
        dup = self.one("SELECT id FROM review WHERE kind=? AND filename=? AND resolved=0 AND root=?",
                       (kind, filename, self.root))
        if dup:
            return dup["id"]
        return self.x("INSERT INTO review(ts, kind, filename, question, suggestion, options, detail, root) "
                      "VALUES(?,?,?,?,?,?,?,?)", (time.time(), kind, filename, question, json.dumps(suggestion),
                                                  json.dumps(options or []), detail, self.root))

    # --- spend ---
    def month_spend_usd(self, month):
        r = self.one("SELECT COALESCE(SUM(usd), 0) AS s FROM spend WHERE month=?", (month,))
        return r["s"] if r else 0.0

    def add_spend(self, month, usd, note):
        self.x("INSERT INTO spend(ts, month, usd, note) VALUES(?,?,?,?)", (time.time(), month, usd, note))


db = DB()
