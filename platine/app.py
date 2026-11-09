"""Fenetre de l'application (pywebview) et API appelee par l'interface."""
import base64
import shutil
import subprocess
import json
import os
import sys
import threading
import time

import webview

from . import __version__, ai, naming, updater, winutil
from .config import CACHE_DIR, CONFIG_PATH, DB_PATH, GENRES, LOG_PATH, THUMB_DIR, config, resource_path
from .db import db
from .pipeline import engine, set_artist_genre


# « Single » est ce que Platine ecrit quand il n'y a pas d'album : ce n'est pas un vrai album
ALBUM_KEY = "(CASE WHEN LOWER(TRIM(COALESCE(album, ''))) IN ('', 'single') THEN '' ELSE LOWER(TRIM(album)) END)"


def _thumb_url(name):
    if not name:
        return None
    p = os.path.join(THUMB_DIR, name)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


class Api:
    def __init__(self):
        self._window = None
        self._update = None          # derniere version disponible (verifiee en arriere-plan)
        self._updating = False

    # ---------------------------------------------------------------- etat
    def get_state(self):
        here = "status='ok' AND root=?"
        r = (db.root,)
        n = db.one(f"SELECT COUNT(*) AS n FROM tracks WHERE {here}", r)["n"]
        genres = db.q(f"SELECT genre, COUNT(*) AS n FROM tracks WHERE {here} AND genre IS NOT NULL "
                      "GROUP BY genre ORDER BY n DESC", r)
        no_art = db.one(f"SELECT COUNT(*) AS n FROM tracks WHERE {here} AND has_art=0", r)["n"]
        covers = db.one(f"SELECT COUNT(*) AS n FROM tracks WHERE {here} AND cover_of IS NOT NULL", r)["n"]
        dups = db.one("SELECT COUNT(*) AS n FROM tracks WHERE status='doublon' AND root=?", r)["n"]
        review = db.one("SELECT COUNT(*) AS n FROM review WHERE resolved=0 AND root=?", r)["n"]
        lib_sql, lib_args = db.library_sql()
        lib_total = db.one(f"SELECT COUNT(*) AS n FROM {lib_sql}", lib_args)["n"]
        lib_genres = db.q(f"SELECT genre, COUNT(*) AS n FROM {lib_sql} WHERE genre IS NOT NULL GROUP BY genre "
                          "ORDER BY n DESC", lib_args)
        folder_ok = bool(config["music_dir"]) and os.path.isdir(config["music_dir"])
        usb_ok = bool(config["usb_dir"]) and os.path.isdir(os.path.join(config["usb_dir"], "VRAC"))
        pending = len(engine.pending()) if folder_ok and not engine.running else 0
        recent = db.q(f"SELECT filename, artist, title, year, genre, thumb, cover_of FROM tracks WHERE {here} "
                      "ORDER BY added DESC LIMIT 12", r)
        for x in recent:
            x["thumb"] = _thumb_url(x["thumb"])
        pub = config.public()
        key = ai.get_key()          # la cle est dans le coffre-fort de Windows, pas dans reglages.json
        pub["api_key_set"], pub["api_key_hint"] = bool(key), ("••••" + key[-4:]) if key else ""
        return {"version": __version__, "config": pub, "count": n, "genres": genres, "no_art": no_art,
                "covers": covers, "doublons": dups, "review": review, "running": engine.running,
                "progress": engine.progress, "folder_ok": folder_ok, "usb_ok": usb_ok, "pending": pending,
                "lib_total": lib_total, "lib_genres": lib_genres,
                "budget": ai.budget_status(), "ai_ready": ai.has_key() and config["ai_enabled"],
                "imported": bool(db.get_meta("imported")),
                "spend": db.q("SELECT ts, usd, note FROM spend ORDER BY ts DESC LIMIT 8"),
                "last_run": db.get_meta("last_run"), "recent": recent, "genre_list": GENRES,
                "update": self._update, "updating": self._updating, "update_enabled": bool(updater.UPDATE_REPO)}

    def get_events(self):
        out = []
        while not engine.events.empty() and len(out) < 200:
            ev = engine.events.get_nowait()
            if ev.get("thumb"):
                ev["thumb"] = _thumb_url(ev["thumb"])
            out.append(ev)
        return out

    # ---------------------------------------------------------------- actions
    def pick_folder(self):
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=config["music_dir"] or "")
        if res:
            path = res[0] if isinstance(res, (list, tuple)) else res
            config.update(music_dir=path)
            return path
        return None

    def pick_usb(self):
        """Cle USB de destination (deja organisee : VRAC, dossiers de genre, FRANCAIS...)."""
        from .usbkey import find_root
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=config["usb_dir"] or "")
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        root = find_root(path)
        if not root:
            return {"error": "Ce dossier ne contient pas de dossier VRAC : choisis le dossier de la clé où se trouvent "
                             "VRAC, xxxRECENTS et les dossiers de genre."}
        config.update(usb_dir=root)
        return {"path": root}

    def clear_usb(self):
        config.update(usb_dir="")
        return True

    def start(self, full=False):
        return engine.start(bool(full))

    def stop(self):
        engine.stop()
        return True

    def last_run_info(self):
        return engine.last_run()

    def undo_last(self):
        return engine.start_undo()

    def open_usb(self):
        if config["usb_dir"]:
            winutil.open_path(config["usb_dir"])

    def open_folder(self, sub=""):
        winutil.open_path(os.path.join(config["music_dir"], sub) if sub else config["music_dir"])

    def save_settings(self, data):
        allowed = {k: v for k, v in data.items() if k in ("genre_folders", "cover_folder", "watch", "autostart",
                                                          "ai_enabled", "ai_budget_eur", "ai_model", "onboarded")}
        if "ai_budget_eur" in allowed:
            allowed["ai_budget_eur"] = max(0.0, min(50.0, float(allowed["ai_budget_eur"] or 0)))
        config.update(**allowed)
        if "autostart" in allowed:
            winutil.set_autostart(bool(allowed["autostart"]))
        return config.public()

    def set_api_key(self, key, workspace=""):
        key, workspace = (key or "").strip(), (workspace or "").strip()
        if not key:
            ai.set_key("")
            config.update(ai_workspace="")
            return {"ok": True, "msg": "Clé supprimée"}
        try:
            ok, msg = ai.test_key(key, workspace)
        except Exception as e:  # ne jamais laisser l'interface sans reponse
            ok, msg = False, f"Erreur : {e}"
        if ok:
            ai.set_key(key)
            config.update(ai_workspace=workspace)
        return {"ok": ok, "msg": msg, "workspace": "workspace" in msg.lower()}

    # ---------------------------------------------------------------- a verifier
    def get_reviews(self):
        rows = db.q("SELECT * FROM review WHERE resolved=0 AND root=? ORDER BY ts DESC LIMIT 300", (db.root,))
        for r in rows:
            r["suggestion"] = json.loads(r["suggestion"]) if r["suggestion"] else None
            r["options"] = json.loads(r["options"]) if r["options"] else []
            t = db.track(r["filename"]) if r["filename"] else None
            r["thumb"] = _thumb_url(t["thumb"]) if t else None
        return rows

    def resolve_review(self, rid, value):
        r = db.one("SELECT * FROM review WHERE id=?", (rid,))
        if not r:
            return False
        kind, f = r["kind"], r["filename"]
        t = db.track(f) if f else None
        try:
            if value is None:
                pass  # ignorer
            elif kind == "year" and t:
                engine.edit_track(f, t["artist"], t["title"], int(value), t["genre"], t["cover_of"])
            elif kind == "genre" and t:
                set_artist_genre(t["artist"], value, "utilisateur")
                db.x("UPDATE tracks SET genre=? WHERE artist=?", (value, t["artist"]))
                engine.edit_track(f, t["artist"], t["title"], t["year"], value, t["cover_of"])
            elif kind == "cover" and t and value not in ("Non", False):
                orig = value if isinstance(value, str) and value != "Oui" else json.loads(r["suggestion"] or "null")
                if orig:
                    engine.edit_track(f, t["artist"], t["title"], t["year"], t["genre"], orig)
            elif kind == "name" and isinstance(value, dict):
                ext = os.path.splitext(f)[1]
                new = naming.build_name(value.get("artist", ""), value.get("title", ""), [], None, None, ext)
                src, dst = os.path.join(config["music_dir"], f), os.path.join(config["music_dir"], new)
                if os.path.exists(src) and not os.path.exists(dst):
                    os.replace(src, dst)        # sera range au prochain « Ranger maintenant », jamais tout seul
        finally:
            db.x("UPDATE review SET resolved=1 WHERE id=?", (rid,))
        return True

    # ---------------------------------------------------------------- bibliotheque
    def _lib(self, query, genre):
        lib_sql, args = db.library_sql()
        where = "1=1"
        if genre:
            where += " AND genre=?"
            args.append(genre)
        if query:
            where += " AND (filename LIKE ? OR album LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        return lib_sql, where, args

    def _rows(self, rows):
        here = db.root
        for r in rows:
            r["thumb"] = _thumb_url(r["thumb"])
            r["here"] = r.get("root") == here          # modifiable seulement si le fichier est dans le dossier choisi
        return rows

    def get_library(self, query="", genre="", offset=0, limit=60):
        lib_sql, where, args = self._lib(query, genre)
        total = db.one(f"SELECT COUNT(*) AS n FROM {lib_sql} WHERE {where}", args)["n"]
        rows = db.q(f"SELECT * FROM {lib_sql} WHERE {where} ORDER BY artist COLLATE NOCASE, year LIMIT ? OFFSET ?",
                    args + [limit, offset])
        return {"total": total, "rows": self._rows(rows)}

    def get_albums(self, query="", genre="", offset=0, limit=60):
        """Bibliotheque regroupee par album (les morceaux sans album a la fin)."""
        lib_sql, where, args = self._lib(query, genre)
        key = ALBUM_KEY
        total = db.one(f"SELECT COUNT(DISTINCT {key}) AS n FROM {lib_sql} WHERE {where}", args)["n"]
        rows = db.q(f"SELECT {key} AS k, MAX(album) AS album, COUNT(*) AS n, COUNT(DISTINCT LOWER(artist)) AS n_artists, "
                    f"MAX(artist) AS artist, MIN(year) AS year, MAX(year) AS year_max, MAX(thumb) AS thumb, MAX(genre) AS genre "
                    f"FROM {lib_sql} WHERE {where} GROUP BY {key} ORDER BY ({key} = ''), album COLLATE NOCASE "
                    f"LIMIT ? OFFSET ?", args + [limit, offset])
        for r in rows:
            r["thumb"] = _thumb_url(r["thumb"])
        return {"total": total, "rows": rows}

    def get_album(self, key, genre=""):
        lib_sql, where, args = self._lib("", genre)
        rows = db.q(f"SELECT * FROM {lib_sql} WHERE {where} AND {ALBUM_KEY}=? "
                    "ORDER BY artist COLLATE NOCASE, year, title COLLATE NOCASE", args + [key])
        return self._rows(rows)

    # ---------------------------------------------------------------- mises a jour
    def check_update(self):
        self._update = updater.check()
        return self._update

    def start_update(self):
        """Telecharge puis installe la nouvelle version (Platine se ferme et se rouvre tout seul)."""
        if not self._update or self._updating:
            return False
        if engine.running:
            return "busy"
        self._updating = True

        def work():
            try:
                engine.emit("update", step="download", done=0, total=self._update.get("size") or 0)
                path = updater.download(self._update, lambda d, t: engine.emit("update", step="download", done=d, total=t))
                engine.emit("update", step="install")
                time.sleep(1.2)                       # laisse l'interface afficher « Installation… »
                updater.install(path)
            except Exception as e:
                self._updating = False
                engine.emit("update", step="error", msg=str(e))
        threading.Thread(target=work, daemon=True).start()
        return True

    def reset_all(self):
        """Remet Platine comme a l'installation : registre, reglages, historique, pochettes en cache.

        Les musiques ne sont jamais touchees. La cle API reste dans le coffre-fort de Windows.
        """
        if engine.running:
            return False
        try:
            winutil.set_autostart(False)
        except Exception:
            pass
        with db._lock:
            db.con.close()
            for f in (DB_PATH, CONFIG_PATH, LOG_PATH):
                try:
                    os.remove(f)
                except OSError:
                    pass
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(sys.argv[0])]
            subprocess.Popen(args, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            threading.Timer(0.3, lambda: os._exit(0)).start()
        return True

    def edit_track(self, filename, data):
        new = engine.edit_track(filename, data.get("artist", ""), data.get("title", ""), data.get("year"),
                                data.get("genre"), data.get("cover") or None)
        return new


def update_checker(api):
    """Verifie les nouvelles versions au demarrage, puis toutes les 6 heures."""
    time.sleep(8)
    while True:
        if api.check_update():
            engine.emit("update_available", version=api._update["version"])
        time.sleep(6 * 3600)


def watcher(api):
    """Repere les musiques deposees pendant que Platine est ouvert et previent. Ne lance JAMAIS de rangement."""
    seen, seen_dir = None, None
    while True:
        time.sleep(10)
        try:
            folder = config["music_dir"]
            if not (folder and os.path.isdir(folder)) or engine.running:
                continue
            files = set(engine.root_files())
            if seen is None or seen_dir != folder:
                seen, seen_dir = files, folder
                continue
            new = files - seen
            seen = files
            if new and config["watch"]:
                engine.emit("new_files", count=len(new))
        except Exception:
            pass


def main():
    api = Api()
    html = resource_path("web", "index.html")
    window = webview.create_window("Platine", html, js_api=api, width=1280, height=820, min_size=(1000, 680),
                                   background_color="#0d0f14", minimized="--minimized" in sys.argv)
    api._window = window
    threading.Thread(target=watcher, args=(api,), daemon=True).start()
    threading.Thread(target=update_checker, args=(api,), daemon=True).start()
    webview.start(debug="--debug" in sys.argv, icon=resource_path("assets", "platine.ico"))


if __name__ == "__main__":
    main()
