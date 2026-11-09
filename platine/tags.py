"""Lecture / ecriture des tags (ID3v2.3 compatible autoradio) et pochettes."""
import base64
import hashlib
import io
import os

import mutagen
from mutagen.asf import ASF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TIT2, TPE1, TYER
from mutagen.oggvorbis import OggVorbis

from .config import THUMB_DIR

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def read(path):
    """-> dict(artist, title, album, year, genre, art: bytes|None)"""
    out = {"artist": "", "title": "", "album": "", "year": None, "genre": "", "art": None}
    try:
        e = mutagen.File(path, easy=True)
        if e is not None:
            g = lambda k: (e.get(k) or [""])[0]
            out.update(artist=g("artist"), title=g("title"), album=g("album"), genre=g("genre"))
            d = str(g("date"))[:4]
            out["year"] = int(d) if d.isdigit() else None
        m = mutagen.File(path)
        if m is not None and m.tags is not None:
            if hasattr(m.tags, "getall"):
                pics = m.tags.getall("APIC")
                if pics:
                    out["art"] = pics[0].data
            elif getattr(m, "pictures", None):
                out["art"] = m.pictures[0].data
            elif "covr" in m.tags:
                out["art"] = bytes(m.tags["covr"][0])
    except Exception:
        pass
    return out


def write(path, title, artist, album, year, genre=None, art=None):
    ext = os.path.splitext(path)[1].lower()
    os.chmod(path, 0o666)
    if ext == ".mp3":
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        except Exception:
            mutagen.id3.delete(path)
            t = ID3()
        for fid, val in (("TIT2", title), ("TPE1", artist), ("TALB", album), ("TCON", genre)):
            t.delall(fid)
            if val:
                t.add({"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TCON": TCON}[fid](encoding=1, text=val))
        t.delall("TDRC")
        if art:
            t.delall("APIC")
            t.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="Cover", data=art))
        t.update_to_v23()
        t.delall("TYER")
        if year:
            t.add(TYER(encoding=1, text=str(year)))
        t.save(path, v2_version=3, v1=2)
    elif ext in (".flac", ".ogg"):
        a = FLAC(path) if ext == ".flac" else OggVorbis(path)
        a["title"], a["artist"], a["album"] = title, artist, album
        if year:
            a["date"] = str(year)
        if genre:
            a["genre"] = genre
        if art:
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", art
            if ext == ".flac":
                a.clear_pictures()
                a.add_picture(pic)
            else:
                a["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        a.save()
    elif ext == ".wma":
        a = ASF(path)
        a["Title"], a["Author"], a["WM/AlbumTitle"] = [title], [artist], [album]
        if year:
            a["WM/Year"] = [str(year)]
        if genre:
            a["WM/Genre"] = [genre]
        a.save()
    elif ext == ".m4a":
        a = mutagen.File(path)
        a["\xa9nam"], a["\xa9ART"], a["\xa9alb"] = [title], [artist], [album]
        if year:
            a["\xa9day"] = [str(year)]
        a.save()


def make_thumb(art, key):
    """Enregistre une miniature 300px de la pochette pour l'interface. -> nom de fichier ou None."""
    if not art or Image is None:
        return None
    name = hashlib.md5(key.encode("utf-8")).hexdigest() + ".jpg"
    path = os.path.join(THUMB_DIR, name)
    if not os.path.exists(path):
        try:
            im = Image.open(io.BytesIO(art)).convert("RGB")
            im.thumbnail((300, 300))
            im.save(path, "JPEG", quality=85)
        except Exception:
            return None
    return name


def normalize_art(data, size=500):
    """Pochette en JPEG 500x500 max (les autoradios n'aiment pas les grosses images)."""
    if not data or Image is None:
        return data
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return None


def art_hash(data):
    """Empreinte visuelle d'une pochette (8x8 niveaux de gris) : deux images quasi identiques ont la meme."""
    if not data:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("L").resize((8, 8), Image.LANCZOS)
        px = list(im.getdata())
        avg = sum(px) / len(px)
        return "%016x" % sum(1 << i for i, v in enumerate(px) if v > avg)
    except Exception:
        return None


def read_title(path):
    """Titre ecrit dans le fichier (lecture rapide, sans la pochette). -> (titre, artiste)"""
    try:
        if path.lower().endswith(".mp3"):
            try:
                t = ID3(path)
            except ID3NoHeaderError:
                return "", ""
            g = lambda k: str(t[k].text[0]) if k in t and t[k].text else ""
            return g("TIT2"), g("TPE1")
        if path.lower().endswith(".wma"):
            a = ASF(path)
            g = lambda k: str(a[k][0]) if a.get(k) else ""
            return g("Title"), g("Author")
        if path.lower().endswith(".wav"):
            return None, None
        e = mutagen.File(path, easy=True)
        if e is None:
            return None, None
        return (e.get("title") or [""])[0], (e.get("artist") or [""])[0]
    except Exception:
        return None, None


def set_title(path, title, artist=None):
    """Ecrit seulement le titre (et l'artiste s'il manque), sans toucher au reste des infos."""
    os.chmod(path, 0o666)
    if path.lower().endswith(".mp3"):
        try:
            t = ID3(path)
        except ID3NoHeaderError:
            t = ID3()
        t.setall("TIT2", [TIT2(encoding=1, text=title)])
        if artist and not t.getall("TPE1"):
            t.add(TPE1(encoding=1, text=artist))
        t.update_to_v23()
        t.save(path, v2_version=3)
        return
    if path.lower().endswith(".wma"):
        a = ASF(path)
        a["Title"] = [title]
        if artist and not a.get("Author"):
            a["Author"] = [artist]
        a.save()
        return
    e = mutagen.File(path, easy=True)
    if e is None:
        raise ValueError("format non reconnu")
    if e.tags is None:
        e.add_tags()
    e["title"] = title
    if artist and not e.get("artist"):
        e["artist"] = artist
    e.save()
