"""Moteur de rangement : tout ce qui a ete fait a la main, en automatique.

Un "passage" traite les fichiers audio a la racine du dossier VRAC :
lecture -> nettoyage du nom -> qualite -> doublons -> reprises -> annee/album/pochette (Deezer, MusicBrainz)
-> genre -> Claude pour les trous -> renommage + tags -> copies par genre et dans Cover.
Rien n'est jamais supprime : les doublons de moins bonne qualite vont dans le sous-dossier "Doublons".
"""
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback

from . import ai, lookup, naming, quality, tags
from .config import GENRES, LOG_PATH, config, resource_path
from .db import db, song_key

DOUBLONS = "Doublons"
COVER_DIR = "Cover"
RESERVED = set(GENRES) | {DOUBLONS, COVER_DIR, "Reprises", "A Trier"}

DEEZER_GENRES = {  # genre Deezer -> genre Platine (secours)
    "rap/hip hop": "Rap US", "rap français": "Rap FR", "reggae": "Reggae & Dub", "dub": "Reggae & Dub",
    "electro": "Electro", "dance": "Electro", "pop": "Pop", "rock": "Rock Classique", "alternative": "Rock Alternatif & Indie",
    "metal": "Metal", "hard rock": "Hard Rock", "punk": "Punk", "chanson française": "Chanson Francaise",
    "variété française": "Chanson Francaise", "soul & funk": "Soul & Funk", "r&b": "Soul & Funk", "disco": "Disco",
    "jazz": "Jazz", "blues": "Blues & Rock'n'Roll 50s", "brazilian music": "Musique Bresilienne & Latine",
    "latin music": "Musique Bresilienne & Latine", "african music": "Musique Du Monde", "world": "Musique Du Monde",
    "films/games": "Bandes Originales", "soundtracks": "Bandes Originales",
}


def _seed_artists():
    if db.get_meta("seeded"):
        return
    path = resource_path("data", "artists_seed.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            seed = json.load(f)
        with db._lock:
            for name, genre in seed.items():
                db.con.execute("INSERT OR IGNORE INTO artists(key, name, genre, source) VALUES(?,?,?,?)",
                               (naming.artist_key(name), name, genre, "base"))
            db.con.commit()
    db.set_meta("seeded", True)


def artist_genre(artist):
    r = db.one("SELECT genre FROM artists WHERE key=?", (naming.artist_key(artist),))
    return r["genre"] if r else None


def set_artist_genre(artist, genre, source):
    db.x("INSERT OR REPLACE INTO artists(key, name, genre, source) VALUES(?,?,?,?)",
         (naming.artist_key(artist), artist, genre, source))


STAGES = {"undo_copy": "Copies par genre retirées", "undo_dup": "Doublons remis en place",
          "undo_rename": "Noms d'origine remis", "read": "Lecture des fichiers", "dupes": "Doublons et reprises", "online": "Infos en ligne",
          "claude": "Claude, cas difficiles", "apply": "Renommage et tags", "copies": "Copies par genre",
          "keytags": "Titres sur la clé"}


class Engine:
    def __init__(self):
        self.events = queue.Queue()
        self.running = False
        self.stop_flag = False
        self.progress = {"step": "", "done": 0, "total": 0}
        self._lock = threading.Lock()
        self.run_id = 0
        _seed_artists()

    # ------------------------------------------------------------- utilitaires
    def emit(self, kind, **data):
        if kind == "progress":
            self.progress = {"step": data.get("step", ""), "done": data.get("done", 0), "total": data.get("total", 0)}
        self.events.put({"kind": kind, "ts": time.time(), **data})

    def step(self, stage, done, total, **extra):
        """Avancement d'une etape (read, dupes, online, claude, apply, copies)."""
        self.emit("progress", stage=stage, step=STAGES[stage], done=done, total=total, **extra)

    def stage(self, stage, state, note=""):
        """Fin d'une etape : state = done | skip."""
        self.emit("stage", stage=stage, state=state, note=note)

    def say(self, msg):
        self.emit("log", msg=msg)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    @property
    def root(self):
        return config["music_dir"]

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def root_files(self):
        if not self.root or not os.path.isdir(self.root):
            return []
        return sorted(f for f in os.listdir(self.root)
                      if naming.is_audio(f) and os.path.isfile(self.p(f)) and not f.endswith(".part"))

    def _rename(self, old, new):
        if old == new:
            return new
        if os.path.exists(self.p(new)) and old.lower() != new.lower():
            base, ext = os.path.splitext(new)
            i = 2
            while os.path.exists(self.p(f"{base} ({i}){ext}")):
                i += 1
            new = f"{base} ({i}){ext}"
        tmp = self.p("__platine_tmp__" + new)
        os.replace(self.p(old), tmp)
        os.replace(tmp, self.p(new))
        db.log(self.run_id, "rename", old, new)
        db.rename_track(old, new)
        db.x("UPDATE review SET filename=? WHERE filename=?", (new, old))
        return new

    def _to_doublons(self, name):
        os.makedirs(self.p(DOUBLONS), exist_ok=True)
        dst = name
        while os.path.exists(self.p(DOUBLONS, dst)):
            b, e = os.path.splitext(dst)
            dst = b + " (bis)" + e
        shutil.move(self.p(name), self.p(DOUBLONS, dst))
        db.log(self.run_id, "doublon", name, os.path.join(DOUBLONS, dst))
        if not db.track(name):     # fichier jamais range : on le note quand meme comme doublon de ce dossier
            db.upsert_track({"filename": name, "size": os.path.getsize(self.p(DOUBLONS, dst)), "status": "doublon"})
        db.x("UPDATE tracks SET status='doublon', filename=? WHERE root=? AND filename=?",
             (os.path.join(DOUBLONS, dst), db.root, name))

    # ------------------------------------------------------------- lancement
    def start(self, full=False):
        with self._lock:
            if self.running:
                return False
            self.running, self.stop_flag = True, False
        threading.Thread(target=self._run_safe, args=(full,), daemon=True).start()
        return True

    def stop(self):
        self.stop_flag = True

    def _run_safe(self, full):
        try:
            self._run(full)
        except Exception as e:
            self.say(f"Erreur inattendue : {e}. Ce qui était fait reste fait ; relance le rangement pour finir.")
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.emit("done", ok=False, error=True)
        finally:
            self.running = False

    def _prune(self):
        """Oublie les morceaux de ce dossier dont le fichier a ete supprime (les autres cles ne sont pas touchees)."""
        gone = [r["id"] for r in db.q("SELECT id, filename FROM tracks WHERE root=?", (db.root,))
                if not os.path.exists(self.p(r["filename"]))]
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            db.x(f"DELETE FROM tracks WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        for r in db.q("SELECT id, filename FROM review WHERE root=? AND resolved=0 AND filename<>''", (db.root,)):
            if not os.path.exists(self.p(r["filename"])):
                db.x("UPDATE review SET resolved=1 WHERE id=?", (r["id"],))

    def pending(self):
        """Fichiers nouveaux ou modifies depuis le dernier passage."""
        known = {r["filename"]: r for r in db.q("SELECT filename, size FROM tracks WHERE root=? AND status!='doublon'",
                                                 (db.root,))}
        out = []
        for f in self.root_files():
            k = known.get(f)
            if not k or k["size"] != os.path.getsize(self.p(f)):
                out.append(f)
        return out

    # ------------------------------------------------------------- passage complet
    def _run(self, full):
        if not self.root or not os.path.isdir(self.root):
            self.say("Choisis d'abord le dossier de ta musique dans les Réglages.")
            self.emit("done", ok=False)
            return
        self.run_id = int(time.time())
        # Lance uniquement a la main (bouton) ou par la surveillance pour les fichiers deposes pendant que
        # Platine est ouvert : jamais tout seul au demarrage.
        self._prune()
        files = self.root_files() if full else self.pending()
        self.say(f"{len(files)} morceau(x) à ranger." if files else "Rien de nouveau : tout est déjà rangé ✓")
        self.emit("start", total=len(files), run=self.run_id)
        # anciennes questions « qui chante ? » sur ces fichiers : on les repose seulement si le doute demeure
        for f in files:
            db.x("UPDATE review SET resolved=1 WHERE root=? AND filename=? AND resolved=0 AND kind IN ('name', 'year', 'genre', 'cover')",
                 (db.root, f))
        items = []
        for i, f in enumerate(files, 1):
            if self.stop_flag:
                break
            it = self._read(f)
            if it:
                items.append(it)
            self.step("read", i, len(files), current=f)
        if items and not self.stop_flag:
            items = self._identify(items)
        if not self.stop_flag:
            self.stage("read", "done", f"{len(items)} morceau(x) lisible(s)")
        if items and not self.stop_flag:
            self._dedupe(items)
            live = [it for it in items if not it.get("gone")]
            self._detect_covers(live)
            gone = len(items) - len(live)
            self.stage("dupes", "done", f"{gone} doublon(s) mis de côté" if gone else "Aucun doublon")
            self._flag_compil_art(live)
            self._online(live)
            if not self.stop_flag:
                reused = sum(1 for it in live if it.get("from_lib"))
                self.stage("online", "done", f"{reused} déjà connu(s) de ta bibliothèque : infos reprises sans recherche"
                           if reused else "")
                if reused:
                    self.say(f"{reused} morceau(x) déjà rangé(s) sur une autre clé : année, genre et album repris de la bibliothèque.")
            self._ask_claude(live)
            for i, it in enumerate(live, 1):
                if self.stop_flag:
                    break
                try:
                    self._apply(it)
                except OSError as e:
                    self.say(f"Impossible de traiter « {it['file']} » ({e.strerror or e}). Il sera repris au prochain rangement.")
                self.step("apply", i, len(live), current=it["file"])
            if not self.stop_flag:
                self.stage("apply", "done")
        elif not self.stop_flag:
            for st in ("dupes", "online", "claude", "apply"):
                self.stage(st, "skip", "Rien à faire")
        if not self.stop_flag:
            self.sync_folders()
            db.set_meta("imported", True)
        if self.stop_flag:
            self.say("Rangement arrêté. Ce qui était déjà fait reste fait ; relance quand tu veux pour finir.")
            self.emit("done", ok=False, count=len(items))
            return
        n_rev = db.one("SELECT COUNT(*) AS n FROM review WHERE resolved=0")["n"]
        self.say(f"Terminé ✓ — {len(items)} morceau(x) traité(s), {n_rev} point(s) à vérifier.")
        db.set_meta("last_run", {"ts": time.time(), "count": len(items), "run": self.run_id})
        self.emit("done", ok=True, count=len(items), review=n_rev)

    # ------------------------------------------------------------- 1. lecture
    def _read(self, f, light=False):
        path = self.p(f)
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size < 100_000:
            # memorise le fichier abime (avec sa taille) pour ne pas le reproposer a chaque rangement
            db.upsert_track({"filename": f, "size": size, "status": "abime"})
            if not db.one("SELECT id FROM review WHERE kind='broken' AND filename=? AND root=?", (f, db.root)):
                db.add_review("broken", f, f"« {f} » semble abîmé ou coupé ({size // 1024} Ko).", None,
                              ["Ignorer"], "Le fichier est trop petit pour être un morceau complet.")
            return None
        t = tags.read(path)
        ext = os.path.splitext(f)[1]
        known = lambda a: bool(a) and not (naming.numeric_only(a) and not db.one(
            "SELECT 1 AS k FROM artists WHERE key=?", (naming.artist_key(a),)))     # "102" non, "113" oui
        parts = naming.parse_name(f)
        if parts and naming.JUNK_TITLE.match(parts["title"]) and not parts["artist"]:
            parts = None                                                       # "Track03"
        tag_title = (t["title"] or "").strip()
        tag_parts = naming.parse_name(tag_title + ext) if " - " in tag_title else None   # titre "Artiste - Titre (Annee)"
        tag_artist = naming.clean_artist(t["artist"])
        if parts and naming.title_key(tag_artist) == naming.title_key(parts["title"]):
            tag_artist = ""                                                    # tag "Police" / titre "Police"
        if parts and not known(parts["artist"]):
            parts["artist"] = ""                                               # "102 - Boxe avec les mots"
        if parts and not parts["artist"]:                                      # le nom ne donne que le titre
            parts["artist"] = next((a for a in (tag_artist, (tag_parts or {}).get("artist"))
                                    if known(a) and naming.title_key(a) != naming.title_key(parts["title"])), "")
        if not parts and tag_parts and known(tag_parts["artist"]):
            parts = tag_parts
        if not parts and known(tag_artist) and tag_title and not naming.JUNK_TITLE.match(tag_title):
            parts = naming.parse_name(f"{tag_artist} - {tag_title}{ext}")
        if not parts:
            db.add_review("name", f, f"Impossible de lire l'artiste et le titre de « {f} ».", None, [],
                          "Indique l'artiste et le titre pour que Platine le range.")
            return None
        it = {"file": f, "size": size, "artist": parts["artist"], "title": naming.repair(parts["title"]), "tags": t,
              "extras": parts["extras"], "year": parts["year"], "ext": parts["ext"] or ext.lower(),
              "tag_year": t["year"], "album": t["album"], "art": t["art"], "compil_tag": lookup.is_compil(t["album"]),
              "need": [], "cover": naming.cover_of(parts["extras"])}
        if it["artist"]:
            self._names(it)
        it["extras"] = [e for e in it["extras"] if not re.search(r"cover\)$", e, re.I)]
        it["art_hash"] = tags.art_hash(t["art"])
        it["version"] = naming.version_of(f)
        it["live"] = it["version"].startswith("live") or "live" in it["version"]
        if not it["live"] and t["album"] and re.search(r"\blive\b|en concert|in concert|unplugged", t["album"], re.I):
            it["live"], it["version"] = True, "live"                         # album « Live au Zenith 95 »
            ly = re.search(r"\b((?:19|20)\d\d)\b|\b(\d\d)\s*$", t["album"])
            if ly:
                y = int(ly.group(1) or ly.group(2))
                it["live_year"] = y if y > 1900 else (1900 + y if y > 30 else 2000 + y)
            if not any(re.search(r"live|concert", e, re.I) for e in it["extras"]):
                it["extras"].append("(Live)")
        if not light:
            self.emit("track", file=f, artist=it["artist"], title=it["title"], stage="analyse")
            it.update(quality.analyse(path))
        return it

    def _names(self, it):
        """Mise en forme de l'artiste et du titre (inversion, casse, featuring, orthographe connue)."""
        artist, title = naming.repair(it["artist"]), naming.repair(it["title"])
        if self._swapped(artist, title, it["tags"]):
            artist, title = title, artist
        artist = re.sub(r"\s+(?:ft|feat|featuring)\.?\s+", " Feat. ", artist, flags=re.I)
        artist, title = naming.fix_case(f"{artist} - {title}").split(" - ", 1)   # casse jugee sur le nom entier
        known = db.one("SELECT name FROM artists WHERE key=? AND source!='base'", (naming.artist_key(artist),))
        if known and known["name"] and naming.ascii_low(known["name"]) == naming.ascii_low(artist):
            artist = known["name"]                     # orthographe connue : "MC Solaar", "AC/DC"...
        it["artist"], it["title"] = artist, title

    def _identify(self, items):
        """Morceaux dont on ne connait que le titre : l'artiste est retrouve par Deezer (titre + album), puis Claude."""
        todo = [it for it in items if not it["artist"]]
        if not todo:
            return items
        self.say(f"{len(todo)} morceau(x) sans artiste dans le nom : recherche de l'artiste…")
        hints = {}
        for n, it in enumerate(todo, 1):
            self.step("read", n, len(todo), label="Recherche de l'artiste", current=it["title"])
            artist, extra = lookup.find_artist(it["title"], it["album"])
            if artist:
                it["artist"], it["title"] = artist, extra or it["title"]
            elif extra:
                hints[id(it)] = extra
        here = {naming.artist_key(it["artist"]) for it in items if it["artist"]}
        for it in todo:
            guess = hints.get(id(it))
            if not it["artist"] and guess and (naming.artist_key(guess) in here or
                                               naming.title_key(guess) == naming.title_key(it["album"] or "")):
                it["artist"] = guess              # artiste deja present dans le dossier, ou album a son nom (« NTM »)
        rest = [it for it in todo if not it["artist"]]
        if rest and config["ai_enabled"] and ai.has_key():
            asks = [{"id": n, "artist": "", "title": it["title"], "need": ["artist"],
                     "known": "; ".join(x for x in (f"album : {it['album']}" if it["album"] else "",
                                                     f"nom du fichier : {it['file']}",
                                                     f"Deezer propose : {hints[id(it)]}" if id(it) in hints else "") if x)}
                    for n, it in enumerate(rest)]
            answers = ai.enrich(asks, log=self.say,
                                progress=lambda d, t, label: self.step("read", d, t, label="Claude cherche l'artiste"))
            for n, it in enumerate(rest):
                r = answers.get(n, {})
                if r.get("artist") and r.get("artist_confidence") in ("high", "medium"):
                    it["artist"] = r["artist"]
                    if r.get("title"):
                        it["title"] = r["title"]
        # meme artiste ecrit autrement par Deezer (« Ärsenik ») : on garde l'orthographe des autres fichiers
        searched = {id(it) for it in todo}
        spelled = {naming.artist_key(naming.ascii_low(it["artist"])): it["artist"]
                   for it in items if it["artist"] and id(it) not in searched}
        keep = []
        for it in items:
            if id(it) in searched and it["artist"]:
                it["artist"] = spelled.get(naming.artist_key(naming.ascii_low(it["artist"])), it["artist"])
                self._names(it)
            if it["artist"]:
                keep.append(it)
            else:
                guess = hints.get(id(it))
                db.add_review("name", it["file"], f"Qui chante « {it['title']} » ?",
                              {"artist": guess or "", "title": it["title"]}, [],
                              (f"Album dans le fichier : {it['album']}. " if it["album"] else "") +
                              (f"Peut-être {guess} ?" if guess else "Platine n'a pas trouvé l'artiste."))
        found = len(todo) - (len(items) - len(keep))
        if found:
            self.say(f"Artiste retrouvé pour {found} morceau(x) sur {len(todo)}.")
        return keep

    @staticmethod
    def _swapped(artist, title, t):
        """Nom ecrit "Titre - Artiste" ? On se fie aux tags, sinon aux artistes deja connus."""
        if t["artist"] and t["title"]:
            if naming.same_artist(title, t["artist"]) and naming.title_key(artist) == naming.title_key(t["title"]):
                return True
            if naming.same_artist(artist, t["artist"]):
                return False
        known = lambda a: db.one("SELECT 1 AS k FROM artists WHERE key=?", (naming.artist_key(a),))
        return bool(known(title)) and not known(artist)

    # ------------------------------------------------------------- 2. doublons
    def _dedupe(self, items):
        self.step("dupes", 0, len(items))
        new_files = {it["file"] for it in items}
        # doublons cherches uniquement dans le dossier choisi, jamais avec les autres cles
        lib = [t for t in db.q("SELECT * FROM tracks WHERE root=? AND status='ok'", (db.root,))
               if t["filename"] not in new_files]
        # version jugee sur le nom final (ex. « (Live) » ajoute d'apres l'album), pas sur le nom actuel du fichier
        pool = lib + [{"filename": it["file"], "artist": it["artist"], "title": it["title"], "version": it["version"],
                       "vkey": naming.version_key(naming.build_name(it["artist"], it["title"], it["extras"], None, None, it["ext"])),
                       "_new": it} for it in items]
        groups = {}
        for t in pool:
            key = (naming.title_key(t["title"] or ""), t.get("vkey", naming.version_key(os.path.basename(t["filename"]))))
            groups.setdefault(key, []).append(t)
        for (tk, ver), g in groups.items():
            if len(g) < 2 or not any("_new" in t for t in g):
                continue
            clusters = []
            for t in g:
                part = naming.part_number(os.path.basename(t["filename"]))
                for c in clusters:
                    parts = {naming.part_number(os.path.basename(x["filename"])) for x in c} - {None}
                    if naming.same_artist(t["artist"] or "", c[0]["artist"] or "") and not (part and parts and part not in parts):
                        c.append(t)
                        break
                else:
                    clusters.append([t])
            for c in clusters:
                if len(c) < 2:
                    continue
                for t in c:  # qualite des anciens fichiers, mesuree seulement maintenant
                    if "_new" not in t and t.get("quality") is None and os.path.exists(self.p(t["filename"])):
                        q = quality.analyse(self.p(t["filename"]))
                        t.update(q)
                        db.x("UPDATE tracks SET cutoff=?, bitrate=?, quality=? WHERE root=? AND filename=?",
                             (q["cutoff"], q["bitrate"], q["quality"], db.root, t["filename"]))
                    elif "_new" in t and t["_new"].get("quality") is None:
                        t["_new"].update(quality.analyse(self.p(t["filename"])))
                        t.update({k: t["_new"][k] for k in ("quality", "bitrate", "cutoff")})
                    t.setdefault("size", os.path.getsize(self.p(t["filename"])) if os.path.exists(self.p(t["filename"])) else 0)
                best = max(c, key=quality.score)
                for t in c:
                    if t is best:
                        continue
                    self._to_doublons(t["filename"])
                    if "_new" in t:
                        t["_new"]["gone"] = True
                    self.emit("track", file=t["filename"], artist=t["artist"], title=t["title"], stage="doublon",
                              detail=f"Doublon de moins bonne qualité → {DOUBLONS}")
                self.say(f"Doublon : « {best['artist']} - {best['title']} » — gardé le meilleur.")

    # ------------------------------------------------------------- 3. reprises
    def _detect_covers(self, items):
        lib = db.q("SELECT filename, artist, title, cover_of FROM tracks WHERE status='ok'")
        by_title = {}
        for t in lib:
            by_title.setdefault(naming.title_key(t["title"] or ""), []).append(t)
        for it in items:
            if it.get("cover"):
                continue
            others = [t for t in by_title.get(naming.title_key(it["title"]), [])
                      if t["filename"] != it["file"] and not naming.same_artist(t["artist"] or "", it["artist"])]
            if others:
                it["need"].append("cover")
                it["cover_hint"] = others[0]["artist"]

    # ------------------------------------------------------------- 4. bibliotheque, puis internet
    def _flag_compil_art(self, items):
        """Meme image chez plusieurs artistes = pochette de compilation (« Absolument 80 »...) : a remplacer."""
        artists = {}
        for it in items:
            if it.get("art_hash"):
                artists.setdefault(it["art_hash"], set()).add(naming.artist_key(it["artist"]))
        hashes = list(artists)
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            for r in db.q(f"SELECT art_hash, artist FROM tracks WHERE art_hash IN ({','.join('?' * len(chunk))})", chunk):
                artists[r["art_hash"]].add(naming.artist_key(r["artist"] or ""))
        for it in items:
            if it.get("art_hash") and len(artists[it["art_hash"]]) >= 2:
                it["compil_art"] = True

    def _from_library(self, it):
        """Meme chanson deja rangee ailleurs (autre cle) : on reprend annee, genre, album et reprise, sans rien payer."""
        key = song_key(it["artist"], it["title"], it["file"])
        t = db.one("SELECT * FROM tracks WHERE song_key=? AND status='ok' AND NOT (root=? AND filename=?) "
                   "ORDER BY (year IS NOT NULL) DESC, updated DESC LIMIT 1", (key, db.root, it["file"]))
        if not t:
            return
        it["from_lib"] = True
        if not it["year"] and t["year"]:
            it["year"] = t["year"]
        if t["genre"]:
            it["genre"] = t["genre"]
        if (not it["album"] or lookup.is_compil(it["album"])) and t["album"] and t["album"] != "Single":
            it["album"] = t["album"]
        if not it.get("cover") and t["cover_of"]:
            it["cover"] = t["cover_of"]


    def _online(self, items, light=False):
        for i, it in enumerate(items, 1):
            if self.stop_flag:
                return
            self.step("online", i, len(items), current=f"{it['artist']} - {it['title']}")
            self._from_library(it)
            need_album = not it["album"] or lookup.is_compil(it["album"])
            # pochette absente, ou pochette de compilation (« Absolument 80 »...) : on cherche celle du vrai album
            need_art = not it["art"] or it.get("compil_tag") or it.get("compil_art")
            need_year = not it["year"]
            g = it.get("genre") or artist_genre(it["artist"])
            if g:
                it["genre"] = g
            else:
                it["need"].append("genre")
            if light and not (need_year or need_art):
                continue
            if need_album or need_art or need_year or "genre" in it["need"]:
                dz = lookup.deezer(it["artist"], it["title"], it["year"] or it["tag_year"], it["live"])
                if not dz and "genre" in it["need"]:
                    self._genre_from_deezer(it, {})
                if dz:
                    it["dz"] = dz
                    da = dz.get("artist") or ""
                    if da and da != it["artist"] and naming.ascii_low(da) == naming.ascii_low(it["artist"]) \
                            and da[:1].casefold() == it["artist"][:1].casefold():
                        it["artist"] = da                       # bonne orthographe : « IAM », « Suprême NTM »
                    self._genre_from_deezer(it, dz)
                    if need_album and dz["album"]:
                        it["album"] = dz["album"]
                    if need_art and dz["cover_url"]:
                        art = lookup.download(dz["cover_url"])
                        it["art_new"] = tags.normalize_art(art) if art else None
            if need_year:
                it["mb_year"] = lookup.musicbrainz_year(it["artist"], it["title"], it["live"])
                cands = [y for y in (it.get("mb_year"), (it.get("dz") or {}).get("album_year"), it["tag_year"])
                         if y and 1900 < y <= time.localtime().tm_year]
                if it.get("live_year"):
                    it["year"] = it["live_year"]          # live : l'annee du concert, donnee par l'album
                    continue
                it["year_cands"] = cands
                it["year_guess"] = min(cands) if cands else None
                it["need"].append("year")

    PRECISE = {"rap/hip hop", "rap français", "reggae", "dub", "metal", "punk", "chanson française", "jazz", "blues",
               "latino", "musique africaine", "musique brésilienne", "films/jeux", "films/games", "soundtracks", "ska"}

    def _genre_from_deezer(self, it, dz):
        """Artiste inconnu : on prend le genre Deezer quand il est precis (rap, reggae, metal...), sans rien demander."""
        if "genre" not in it["need"]:
            return
        for name in (dz.get("genres") or []) + (lookup.artist_genres(it["artist"]) if not dz.get("genres") else []):
            if name.lower() not in self.PRECISE:
                continue
            g = DEEZER_GENRES.get(name.lower())
            if g in ("Rap US", "Rap FR"):                 # FR ou US : decide a la fin, artiste par artiste
                it["rap"] = "fr" if g == "Rap FR" else naming.language(it["title"], it.get("album") or "")
                g = "Rap FR"
            if g:
                it["genre"] = g
                it["need"].remove("genre")
                if "rap" not in it:
                    set_artist_genre(it["artist"], g, "deezer")
                return

    def _rap_language(self, items):
        """Rap d'un artiste inconnu : francais ou americain selon la langue de ses titres, sinon celle du dossier."""
        raps = [it for it in items if "rap" in it]
        if not raps:
            return
        fr_all = sum(it["rap"] == "fr" for it in raps)
        en_all = sum(it["rap"] == "en" for it in raps)
        by_artist = {}
        for it in raps:
            by_artist.setdefault(naming.artist_key(it["artist"]), []).append(it)
        for group in by_artist.values():
            fr = sum(it["rap"] == "fr" for it in group)
            en = sum(it["rap"] == "en" for it in group)
            g = "Rap FR" if fr > en or (fr == en and fr_all >= en_all) else "Rap US"
            for it in group:
                it["genre"] = g
            set_artist_genre(group[0]["artist"], g, "deezer")
        for it in items:                                # collaborations du meme artiste restees sans genre
            if "genre" in it["need"] and naming.artist_key(it["artist"]) in by_artist:
                it["genre"] = by_artist[naming.artist_key(it["artist"])][0]["genre"]
                it["need"].remove("genre")

    # ------------------------------------------------------------- 5. Claude
    def _ask_claude(self, items, use_ai=True):
        self._rap_language(items)
        asks = []
        if use_ai and len(items) <= 30:   # petit arrivage : Claude verifie aussi les reprises (quelques centimes)
            for it in items:
                if not it.get("cover") and not it.get("from_lib"):
                    it["need"].append("cover")
        for n, it in enumerate(items):
            if not it["need"]:
                continue
            known = []
            if it.get("year_guess"):
                known.append(f"annee probable {it['year_guess']}")
            if it.get("cover_hint"):
                known.append(f"meme titre aussi joue par {it['cover_hint']}")
            if it.get("dz") and it["dz"].get("genres"):
                known.append("genres Deezer : " + ", ".join(it["dz"]["genres"][:3]))
            asks.append({"id": n, "artist": it["artist"], "title": it["title"] + (" (live)" if it["live"] else ""),
                         "need": list(dict.fromkeys(it["need"])), "known": "; ".join(known), "guess": it.get("year_guess")})
        if not asks:
            self.stage("claude", "skip", "Pas nécessaire : tout a été trouvé gratuitement")
            answers = {}
        elif not (use_ai and config["ai_enabled"] and ai.has_key()):
            self.stage("claude", "skip", "Claude désactivé : les cas difficiles iront dans « À vérifier »")
            answers = {}
        else:
            answers = ai.enrich(asks, log=self.say, progress=lambda d, t, label: self.step("claude", d, t, label=label))
            self.stage("claude", "done", f"{len(asks)} morceau(x) analysé(s)")
        for n, it in enumerate(items):
            r = answers.get(n, {})
            ok = lambda k: r.get(f"{k}_confidence") in ("high", "medium")
            if "year" in it["need"]:
                # la plus ancienne des annees proposees : compilations et reeditions viennent toujours apres
                cy = int(r["year"]) if str(r.get("year") or "").isdigit() and 1900 < int(r["year"]) <= time.localtime().tm_year else None
                cands = [y for y in it.get("year_cands", []) if y]
                if cy and ok("year"):
                    it["year"] = min(cands + [cy])     # meme « sur », Claude se trompe parfois : on garde la plus ancienne
                elif cands:
                    it["year"] = min(cands)
                elif cy:
                    it["year"] = cy
                    if r.get("year_confidence") == "low":
                        self._review_year(it, [cy], r.get("note") or "Année peu sûre.")
                else:
                    self._review_year(it, [], r.get("note") or "Année introuvable.")
            if "genre" in it["need"]:
                g = r.get("genre") if r.get("genre") in GENRES else None
                if g and ok("genre"):
                    it["genre"] = g
                    set_artist_genre(it["artist"], g, "claude")
                else:
                    dzg = [DEEZER_GENRES.get(x.lower()) for x in (it.get("dz") or {}).get("genres", [])]
                    dzg = [x for x in dzg if x]
                    guess = g or (dzg[0] if dzg else None)
                    it["genre"] = guess or "A Trier"
                    db.add_review("genre", it["file"], f"Dans quel genre ranger {it['artist']} ?", guess, GENRES,
                                  r.get("note") or "Genre incertain.")
            if "cover" in it["need"]:
                if r.get("is_cover") and r.get("original_artist") and ok("cover"):
                    it["cover"] = r["original_artist"]
                elif r.get("is_cover") or (not r and it.get("cover_hint")):
                    orig = r.get("original_artist") or it.get("cover_hint")
                    db.add_review("cover", it["file"],
                                  f"« {it['artist']} - {it['title']} » est-il une reprise de {orig} ?", orig,
                                  ["Oui", "Non"], r.get("note") or "Le même titre existe chez un autre artiste.")

    def _review_year(self, it, choices, note):
        choices = [c for c in dict.fromkeys(choices) if c]
        db.add_review("year", it["file"], f"En quelle année est sorti « {it['artist']} - {it['title']} » ?",
                      choices[0] if choices else None, choices, note or "")

    # ------------------------------------------------------------- 6. application
    def _apply(self, it, light=False):
        if it.get("gone"):
            return
        new = naming.build_name(it["artist"], it["title"], it["extras"], it.get("cover"), it.get("year"), it["ext"])
        old = it["file"]
        try:
            new = self._rename(old, new)
        except OSError as e:
            self.say(f"Impossible de renommer « {old} » ({e}).")
            new = old
        stem = os.path.splitext(new)[0]
        artist = naming.title_case(naming.repair(it["artist"]))
        album = it.get("album") if it.get("album") and not lookup.is_compil(it["album"]) else "Single"
        art = it.get("art_new")
        try:
            cur = tags.read(self.p(new)) if light else None
            if not light or cur["title"] != stem or cur["artist"] != artist or (art and not cur["art"]):
                tags.write(self.p(new), stem, artist, album, it.get("year"), it.get("genre"), art)
        except Exception as e:
            self.say(f"Tags non écrits pour « {new} » ({e}).")
        thumb = tags.make_thumb(art or it.get("art"), new)
        db.log(self.run_id, "add", new, new)
        db.upsert_track({"filename": new, "size": os.path.getsize(self.p(new)), "mtime": os.path.getmtime(self.p(new)),
                         "artist": artist, "title": it["title"], "version": it["version"], "cover_of": it.get("cover"),
                         "year": it.get("year"), "album": album, "genre": it.get("genre"),
                         "cutoff": it.get("cutoff"), "bitrate": it.get("bitrate"), "quality": it.get("quality"),
                         "has_art": int(bool(art or it.get("art"))), "thumb": thumb, "status": "ok",
                         "art_hash": tags.art_hash(art) if art else it.get("art_hash")})
        if not light or new != old:
            self.emit("track", file=new, before=old, artist=artist, title=it["title"], year=it.get("year"),
                      genre=it.get("genre"), album=album, cover=it.get("cover"), label=it.get("label"),
                      thumb=thumb, stage="ok")

    # ------------------------------------------------------------- 7. dossiers genre / Cover
    def sync_folders(self):
        if config["usb_dir"]:                     # cle USB choisie : on classe sur la cle, pas dans le dossier de depart
            from .usbkey import KeySync
            rows = db.q("SELECT filename, artist, title, genre, cover_of, year FROM tracks WHERE root=? AND status='ok'",
                        (db.root,))
            KeySync(self, config["usb_dir"]).run(rows)
            return
        self.stage("keytags", "skip", "Pas de clé USB choisie")
        if not (config["genre_folders"] or config["cover_folder"]):
            self.stage("copies", "skip", "Désactivé dans les Réglages")
            return
        rows = db.q("SELECT filename, genre, cover_of, size FROM tracks WHERE root=? AND status='ok'", (db.root,))
        want = {}
        for r in rows:
            if not os.path.exists(self.p(r["filename"])):
                continue
            if config["genre_folders"] and r["genre"]:
                want.setdefault(r["genre"], set()).add(r["filename"])
            if config["cover_folder"] and r["cover_of"]:
                want.setdefault(COVER_DIR, set()).add(r["filename"])
        todo = []
        for folder, names in want.items():
            d = self.p(folder)
            have = set(os.listdir(d)) if os.path.isdir(d) else set()
            todo += [(n, folder) for n in sorted(names - have)]
            for stale in sorted(have - names):           # copies devenues obsoletes (renommees, recassees)
                if naming.is_audio(stale) and os.path.exists(os.path.join(d, stale)):
                    os.remove(os.path.join(d, stale))
        need = sum(os.path.getsize(self.p(n)) for n, _ in todo if os.path.exists(self.p(n)))
        free = shutil.disk_usage(self.root).free
        if need > free - 200 * 1024 * 1024:
            self.say(f"Pas assez de place pour les copies par genre ({need / 1e9:.1f} Go nécessaires, "
                     f"{free / 1e9:.1f} Go libres). Copies ignorées.")
            db.add_review("space", "", "Pas assez de place sur le disque pour les dossiers de genre.", None, ["OK"],
                          f"Il faudrait {need / 1e9:.1f} Go, il reste {free / 1e9:.1f} Go. Désactive les copies "
                          "par genre dans les Réglages ou utilise un disque plus grand.")
            self.stage("copies", "skip", "Pas assez de place sur le disque")
            return
        for i, (n, folder) in enumerate(todo, 1):
            if self.stop_flag:
                return
            dst = self.p(folder, n)
            try:   # le fichier a pu etre renomme entre-temps (correction faite pendant le rangement)
                os.makedirs(self.p(folder), exist_ok=True)
                shutil.copy2(self.p(n), dst + ".part")
                os.replace(dst + ".part", dst)
                db.log(self.run_id, "copy", n, os.path.join(folder, n))
            except OSError as e:
                self.say(f"Copie impossible de « {n} » dans {folder} ({e.strerror or e}). Ce sera refait au prochain rangement.")
                try:
                    os.remove(dst + ".part")
                except OSError:
                    pass
            if i % 5 == 0 or i == len(todo):
                self.step("copies", i, len(todo), current=f"{folder} / {n}")
        self.stage("copies", "done", f"{len(todo)} copie(s)" if todo else "Déjà à jour")

    # ------------------------------------------------------------- annulation
    def last_run(self):
        """Ce que l'annulation va defaire (dernier rangement du dossier choisi)."""
        run = db.one("SELECT MAX(run) AS r FROM journal WHERE undone=0 AND root=?", (db.root,))
        if not run or not run["r"]:
            return None
        rows = db.q("SELECT action, COUNT(*) AS n FROM journal WHERE run=? AND undone=0 AND root=? GROUP BY action",
                    (run["r"], db.root))
        n = {r["action"]: r["n"] for r in rows}
        return {"run": run["r"], "rename": n.get("rename", 0), "doublon": n.get("doublon", 0), "copy": n.get("copy", 0)}

    def start_undo(self):
        with self._lock:
            if self.running:
                return False
            self.running, self.stop_flag = True, False
        threading.Thread(target=self._undo_safe, daemon=True).start()
        return True

    def _undo_safe(self):
        try:
            self._undo()
        except Exception as e:
            self.say(f"Erreur pendant l'annulation : {e}")
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.emit("done", ok=False, error=True, undo=True)
        finally:
            self.running = False

    def _undo(self):
        info = self.last_run()
        if not info:
            self.emit("start", undo=True, total=0)
            self.say("Rien à annuler.")
            self.emit("done", ok=True, count=0, undo=True)
            return
        rows = db.q("SELECT * FROM journal WHERE run=? AND undone=0 AND root=? ORDER BY id DESC", (info["run"], db.root))
        self.emit("start", undo=True, total=len(rows), when=info["run"])
        stages = {"copy": "undo_copy", "doublon": "undo_dup", "rename": "undo_rename"}
        total = {k: sum(1 for r in rows if r["action"] == k) for k in stages}
        done = dict.fromkeys(stages, 0)
        n = 0
        # les morceaux de ce rangement redeviennent "a ranger" : on les retire du registre
        for r in (r for r in rows if r["action"] == "add"):
            db.x("DELETE FROM tracks WHERE root=? AND filename=? AND status='ok'", (db.root, r["dst"]))
            db.x("UPDATE journal SET undone=1 WHERE id=?", (r["id"],))
        # d'abord les copies par genre, puis les doublons remis en place, enfin les noms d'origine
        for action in ("copy", "doublon", "rename"):
            if not total[action]:
                self.stage(stages[action], "skip", "Rien à défaire")
                continue
            for r in (r for r in rows if r["action"] == action):
                try:
                    if action == "rename" and os.path.exists(self.p(r["dst"])):
                        os.replace(self.p(r["dst"]), self.p(r["src"]))
                        db.rename_track(r["dst"], r["src"])
                        db.x("DELETE FROM tracks WHERE root=? AND filename=? AND status='ok'", (db.root, r["src"]))
                    elif action == "doublon" and os.path.exists(self.p(r["dst"])):
                        shutil.move(self.p(r["dst"]), self.p(r["src"]))
                        db.x("DELETE FROM tracks WHERE root=? AND filename=?", (db.root, r["dst"]))
                    elif action == "copy" and os.path.exists(self.p(r["dst"])):
                        os.remove(self.p(r["dst"]))
                    db.x("UPDATE journal SET undone=1 WHERE id=?", (r["id"],))
                    n += 1
                except OSError as e:
                    self.say(f"Impossible de défaire « {r['dst']} » ({e.strerror or e}).")
                done[action] += 1
                self.step(stages[action], done[action], total[action], current=os.path.basename(r["dst"]))
            self.stage(stages[action], "done", f"{done[action]} fait(s)")
        self.say(f"Annulation terminée : {n} opération(s) défaite(s).")
        self.emit("done", ok=True, count=n, undo=True)

    # ------------------------------------------------------------- correction manuelle
    def edit_track(self, filename, artist, title, year, genre, cover):
        t = db.track(filename)
        if not t or not os.path.exists(self.p(filename)):
            return None
        parts = naming.split_name(filename) or {"extras": [], "ext": os.path.splitext(filename)[1]}
        extras = [e for e in parts["extras"] if not re.search(r"cover\)$", e, re.I)]
        self.run_id = int(time.time())
        new = naming.build_name(artist, title, extras, cover or None, int(year) if year else None, parts["ext"])
        new = self._rename(filename, new)
        if genre and genre in GENRES:
            set_artist_genre(artist, genre, "utilisateur")
            db.x("UPDATE tracks SET genre=? WHERE artist=?", (genre, t["artist"]))
        db.x("UPDATE tracks SET artist=?, title=?, year=?, genre=?, cover_of=?, song_key=? WHERE root=? AND filename=?",
             (artist, title, int(year) if year else None, genre or t["genre"], cover or None,
              song_key(artist, title, new), db.root, new))
        tags.write(self.p(new), os.path.splitext(new)[0], artist, t["album"] or "Single",
                   int(year) if year else None, genre or t["genre"])
        threading.Thread(target=self.sync_folders, daemon=True).start()
        return new


engine = Engine()
