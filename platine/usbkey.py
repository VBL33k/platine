"""Classement sur une cle USB deja organisee (dossiers de genre en MAJUSCULES, FRANCAIS\\<GENRE>, VRAC,
xxxRECENTS, xxxNOUVEAUTES).

Chaque morceau du dossier de depart, une fois renomme et tague, est copie sur la cle :
  - dans son dossier de genre (celui ou l'artiste est deja range sur la cle, sinon d'apres son genre et son pays),
  - dans COVER aussi si c'est une reprise,
  - dans VRAC,
  - dans xxxRECENTS s'il est sorti en 2020 ou apres,
  - dans xxxNOUVEAUTES (les ajouts recents, a vider soi-meme).
Un morceau deja present sur la cle (meme artiste, meme titre, meme version) n'est pas recopie.
Ensuite le titre ecrit dans chaque fichier de la cle est verifie : il doit etre le nom du fichier.
Rien n'est jamais supprime sur la cle.
"""
import os
import re
import shutil
from collections import Counter

from . import ai, lookup, naming, tags
from .db import db, root_id, song_key

VRAC = "VRAC"
FR = "FRANCAIS"
SPECIAL = {"vrac", "francais", "doublons"}
FR_COUNTRIES = {"FR", "BE", "MC", "LU", "RE", "GP", "MQ", "GF", "YT", "NC", "PF", "PM", "SN", "CI", "CM", "ML", "BF",
                "CD", "CG", "GA", "BJ", "TG", "NE", "GN", "HT", "DZ", "MA", "TN", "MG", "TD", "CF", "DJ", "KM"}
# genre Platine -> dossier de la cle (international, francais)
GENRE_TO_KEY = {
    "Rock Classique": ("ROCK CLASSIQUE", "ROCK"), "Rock Alternatif & Indie": ("ROCK ALTERNATIF & INDIE", None),
    "Grunge": ("GRUNGE", None), "Hard Rock": ("HARD ROCK", None), "Metal": ("METAL", None), "Punk": ("PUNK US", "PUNK"),
    "Hardcore & Fusion": ("HARDCORE", None), "Rap FR": ("RAP US", "RAP"), "Rap US": ("RAP US", "RAP"),
    "Reggae & Dub": ("REGGAE & DUB", None), "Ska": ("SKA", None), "Electro": ("ELECTRO", None), "Pop": ("POP", None),
    "Annees 80": ("ANNEES 80", None), "Chanson Francaise": ("POP", "CHANSON"), "Rock Francais": ("ROCK CLASSIQUE", "ROCK"),
    "Soul & Funk": ("SOUL", None), "Disco": ("DISCO", None), "Jazz": ("JAZZ", None),
    "Blues & Rock'n'Roll 50s": ("ROCK N'ROLL 50'S", None), "Musique Bresilienne & Latine": ("MUSIQUE BRESILIENNE & LATINE", None),
    "Musique Du Monde": ("MUSIQUE DU MONDE", None), "Bandes Originales": ("BANDES ORIGINALES", None),
}
FR_GENRES = {"Rap FR", "Chanson Francaise", "Rock Francais"}


def find_root(path):
    """Le dossier de la cle qui contient VRAC (la racine choisie, ou un de ses sous-dossiers). -> chemin ou None"""
    if not path or not os.path.isdir(path):
        return None
    if os.path.isdir(os.path.join(path, VRAC)):
        return path
    subs = [os.path.join(path, d) for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d, VRAC))] if os.path.isdir(path) else []
    return subs[0] if len(subs) == 1 else None


def _special(root, prefix, default):
    """xxxRECENTS / xxxNOUVEAUTES : on reprend le nom exact present sur la cle."""
    for d in os.listdir(root):
        if d.lower().startswith(prefix) and os.path.isdir(os.path.join(root, d)):
            return d
    return default


class KeyIndex:
    """Ce qu'il y a deja sur la cle : dossiers, artistes par dossier, chansons presentes."""

    def __init__(self, root):
        self.root = root
        self.recents = _special(root, "xxxrecent", "xxxRECENTS")
        self.nouveautes = _special(root, "xxxnouveaut", "xxxNOUVEAUTES")
        self.folders = []                  # dossiers de genre, chemins relatifs (« PUNK UK », « FRANCAIS\\ROCK »)
        self.artist_dirs = {}              # cle d'artiste -> Counter(dossier)
        self.in_vrac, self.in_genre = set(), set()
        self.names_vrac = set()
        for d in sorted(os.listdir(root)):
            if not os.path.isdir(os.path.join(root, d)) or d.lower().startswith("xxx") or d.lower() in SPECIAL:
                continue
            self.folders.append(d)
        fr = os.path.join(root, FR)
        if os.path.isdir(fr):
            self.folders += [os.path.join(FR, d) for d in sorted(os.listdir(fr)) if os.path.isdir(os.path.join(fr, d))]
        for folder in self.folders:
            cover = folder.upper().endswith("COVER")
            for f in os.listdir(os.path.join(root, folder)):
                if not naming.is_audio(f):
                    continue
                k = self._song(f)
                if k:
                    self.in_genre.add(k)
                p = naming.split_name(f)
                if p and not cover:
                    self.artist_dirs.setdefault(naming.artist_key(p["artist"]), Counter())[folder] += 1
        vrac = os.path.join(root, VRAC)
        for f in os.listdir(vrac) if os.path.isdir(vrac) else []:
            if naming.is_audio(f):
                self.names_vrac.add(f.lower())
                k = self._song(f)
                if k:
                    self.in_vrac.add(k)

    @staticmethod
    def _song(filename):
        p = naming.split_name(filename)
        return song_key(p["artist"], p["title"], filename) if p else None

    def artist_folder(self, artist):
        c = self.artist_dirs.get(naming.artist_key(artist))
        return c.most_common(1)[0][0] if c else None

    def has(self, folder):
        return folder in self.folders


def _year(filename, year):
    m = re.search(r"\(((?:19|20)\d\d)\)\.\w+$", filename)
    return int(m.group(1)) if m else year


class KeySync:
    def __init__(self, engine, key_path):
        self.e = engine
        self.root = find_root(key_path)

    # ---------------------------------------------------------------- choix du dossier
    def _decide(self, idx, rows):
        """-> {artist_key: dossier} pour les artistes de ces morceaux."""
        out, todo = {}, {}
        for r in rows:
            k = naming.artist_key(r["artist"] or "")
            if k in out:
                continue
            if k not in todo:
                known = idx.artist_folder(r["artist"])
                if known:
                    out[k] = known
                    continue
                todo[k] = {"artist": r["artist"], "genre": r["genre"], "titles": []}
            todo[k]["titles"].append(r["title"] or "")
        for v in todo.values():                          # autres titres du meme artiste
            v["titles"] = list(dict.fromkeys(v["titles"]))
        if not todo:
            return out
        self.e.say(f"{len(todo)} artiste(s) pas encore sur la clé : choix de leur dossier…")
        for v in todo.values():
            v["lang"] = naming.language(*v["titles"])
        items = [{"id": n, "artist": v["artist"], "titles": v["titles"], "genre": v["genre"], "lang": v["lang"]}
                 for n, v in enumerate(todo.values())]
        answers = ai.key_folders(items, idx.folders, log=self.e.say)
        for n, (k, v) in enumerate(todo.items()):
            r = answers.get(n)
            folder = r["folder"] if r and (r.get("confidence") in ("high", "medium") or
                                           (v["lang"] == "fr" and r["folder"].startswith(FR + os.sep))) else None
            if folder and v["lang"] == "fr" and not folder.startswith(FR + os.sep) and r.get("confidence") != "high":
                fr = os.path.join(FR, os.path.basename(folder))      # titres en francais, artiste inconnu
                folder = fr if idx.has(fr) else folder
            out[k] = folder or self._by_genre(idx, v["artist"], v["genre"], v["titles"])
        return out

    def _by_genre(self, idx, artist, genre, titles):
        """Sans Claude : dossier d'apres le genre Platine et le pays de l'artiste (MusicBrainz)."""
        if not genre or genre not in GENRE_TO_KEY:
            return None
        intl, fr_sub = GENRE_TO_KEY[genre]
        country = lookup.artist_country(naming.repair(artist).split(" Feat. ")[0])
        french = genre in FR_GENRES or country in FR_COUNTRIES or \
            (country in ("CA", "CH", None) and naming.language(*titles) == "fr")
        if french:
            return os.path.join(FR, fr_sub or intl)
        if genre == "Punk":
            return "PUNK UK" if country == "GB" else "PUNK US" if country in ("US", None) else "PUNK (OTHERS)"
        return intl

    # ---------------------------------------------------------------- copies
    def run(self, rows):
        """rows : morceaux du dossier de depart (filename, artist, title, genre, cover_of, year)."""
        e = self.e
        if not self.root:
            e.say("Clé USB introuvable : branche-la (ou vérifie qu'elle contient un dossier VRAC). "
                  "Les morceaux y seront copiés au prochain rangement.")
            e.stage("copies", "skip", "Clé USB introuvable")
            e.stage("keytags", "skip", "Clé USB introuvable")
            return
        idx = KeyIndex(self.root)
        rows = [r for r in rows if os.path.exists(e.p(r["filename"]))]
        for r in rows:
            r["_song"] = KeyIndex._song(r["filename"])
            r["_new"] = r["_song"] not in idx.in_vrac and r["filename"].lower() not in idx.names_vrac
        new = [r for r in rows if r["_new"]]
        # deja dans VRAC mais dans aucun dossier de genre (genre choisi apres coup dans « A verifier »)
        no_genre = [r for r in rows if not r["_new"] and r["genre"] and r["genre"] != "A Trier"
                    and r["_song"] not in idx.in_genre]
        dest = self._decide(idx, [r for r in new + no_genre if r["genre"] != "A Trier"]) if new or no_genre else {}
        plan = []
        for r in new + no_genre:
            f = r["filename"]
            dirs = []
            folder = dest.get(naming.artist_key(r["artist"] or ""))
            if folder:
                dirs.append(folder)
            elif r["_new"]:
                db.add_review("genre", f, f"Dans quel genre ranger {r['artist']} ?", r["genre"], [],
                              "Pas trouvé de dossier sur la clé : le morceau n'est que dans VRAC et les nouveautés.")
            if r["cover_of"]:
                dirs.append(os.path.join(FR, "COVER") if folder and folder.startswith(FR + os.sep) and idx.has(os.path.join(FR, "COVER"))
                            else "COVER")
            if r["_new"]:
                dirs.append(VRAC)
                y = _year(f, r["year"])
                if y and y >= 2020:
                    dirs.append(idx.recents)
                dirs.append(idx.nouveautes)
            plan += [(f, d) for d in dirs if not os.path.exists(os.path.join(self.root, d, f))]
        skipped = len(rows) - len(new) - len(no_genre)
        need = sum(os.path.getsize(e.p(f)) for f, _ in plan)
        free = shutil.disk_usage(self.root).free
        if need > free - 100 * 1024 * 1024:
            e.say(f"Pas assez de place sur la clé ({need / 1e9:.1f} Go nécessaires, {free / 1e9:.1f} Go libres). Rien n'a été copié.")
            e.stage("copies", "skip", "Pas assez de place sur la clé")
        else:
            for i, (f, d) in enumerate(plan, 1):
                if e.stop_flag:
                    return
                dst = os.path.join(self.root, d, f)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(e.p(f), dst + ".part")
                    os.replace(dst + ".part", dst)
                    db.log(e.run_id, "copy", f, dst)
                except OSError as err:
                    e.say(f"Copie impossible de « {f} » dans {d} ({err.strerror or err}). Ce sera refait au prochain rangement.")
                    try:
                        os.remove(dst + ".part")
                    except OSError:
                        pass
                if i % 5 == 0 or i == len(plan):
                    e.step("copies", i, len(plan), current=f"{d} / {f}")
            n_new = len(new)
            note = f"{n_new} nouveau(x) morceau(x) sur la clé" if n_new else "Rien de nouveau pour la clé"
            if skipped:
                note += f", {skipped} déjà présent(s)"
            e.stage("copies", "done", note)
            if n_new:
                e.say(f"{n_new} morceau(x) ajouté(s) sur la clé (aussi dans « {idx.nouveautes} »).")
        self.fix_titles()

    # ---------------------------------------------------------------- titres
    def fix_titles(self):
        """Le titre ecrit dans chaque fichier de la cle doit etre son nom complet (« Artiste - Titre (Annee) »)."""
        e = self.e
        files = []
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if d.lower() != "doublons"]
            files += [os.path.join(dirpath, n) for n in names if naming.is_audio(n)]
        cache_key = "key_titles:" + root_id(self.root)
        seen = db.get_meta(cache_key, {}) or {}
        fixed, failed, keep = 0, 0, {}
        for i, path in enumerate(files, 1):
            if e.stop_flag:
                break
            rel = os.path.relpath(path, self.root)
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            if seen.get(rel) != sig:
                stem = os.path.splitext(os.path.basename(path))[0]
                title, artist = tags.read_title(path)
                if title is not None and title != stem:
                    p = naming.split_name(os.path.basename(path))
                    try:
                        tags.set_title(path, stem, p["artist"] if p else None)
                        fixed += 1
                        st = os.stat(path)
                        sig = f"{st.st_size}:{int(st.st_mtime)}"
                    except Exception:
                        failed += 1
                        sig = None
            if sig:
                keep[rel] = sig
            if i % 50 == 0 or i == len(files):
                e.step("keytags", i, len(files), current=rel)
        db.set_meta(cache_key, keep)
        note = f"{fixed} titre(s) corrigé(s)" if fixed else "Tous les titres sont bons"
        if failed:
            note += f", {failed} fichier(s) illisible(s)"
        e.stage("keytags", "done", note)
        if fixed:
            e.say(f"Titre corrigé dans {fixed} fichier(s) de la clé.")
