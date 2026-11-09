"""Recherches gratuites en ligne : album, pochette, annee (Deezer + MusicBrainz)."""
import difflib
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

UA = {"User-Agent": "Platine/1.0 (rangement musical personnel)"}
COMPIL = re.compile(
    r"best of|greatest|hits|top \d|\btop\b|ann[ée]es|collection|essential|classics|songs|oldies|compil|\bnrj\b|\bnow\b|"
    r"various|\bva\b|pitchfork|nme|1001|anthology|anthologie|\b(19|20)?[0-9]0'?s\b|60s|70s|80s|90s|playlist|mix ?tape|"
    r"radio|the very best|definitive|ultimate|gold|platinum|\bbox\b|coffret|indispensables|tubes|rap fran[cç]ais|hip.?hop fran|old school|classiks?|plus grands|"
    r"plus belles|incontournables|essentiels?|tribute|hommage|d[ée]dicace|repose en paix|sampler|\b\d\d ?- ?\d\d\b|this is it",
    re.I)

_lock = threading.Lock()
_last = [0.0]
_album_cache = {}


def is_compil(name):
    return bool(name) and bool(COMPIL.search(name))


def _n(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("the ", ""))


def _sim(a, b):
    a, b = _n(a), _n(b)
    return bool(a and b) and (a == b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() > 0.8)


def _get(url, pause=0.12):
    for attempt in range(3):
        with _lock:
            wait = pause - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                d = json.load(r)
            if isinstance(d, dict) and d.get("error", {}).get("code") == 4:
                time.sleep(2)
                continue
            return d
        except Exception:
            time.sleep(1 + attempt)
    return {}


def clean_album(a):
    a = re.sub(r"\s*[\(\[][^\)\]]*(remaster|deluxe|expanded|edition|anniversary|bonus|version)[^\)\]]*[\)\]]", "", a or "", flags=re.I)
    a = re.sub(r"\s*-\s*(remaster(ed)?|deluxe|expanded).*$", "", a, flags=re.I)
    a = re.sub(r"\s+(super\s+)?deluxe(\s+edition)?$", "", a.strip(), flags=re.I)
    return a.strip()


def deezer(artist, title, year=None, live=False):
    """-> dict(album, album_year, cover_url, artist_genre_hint) ou None"""
    artist = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", artist, flags=re.I)[0]
    d = _get("https://api.deezer.com/search?q=" + urllib.parse.quote(f"{artist} {title}") + "&limit=25")
    data = d.get("data", []) if isinstance(d, dict) else []
    best = None
    seen = set()
    for r in data:
        if not _sim(artist, r["artist"]["name"]) or not _sim(title, r.get("title_short") or r["title"]):
            continue
        if re.search(r"live|concert|session|demo|mix|version|edit|acoustic|instrumental|karaoke", r["title"], re.I) and not live:
            continue
        al = r["album"]
        if is_compil(al["title"]) or al["id"] in seen or len(seen) >= 10:
            continue
        seen.add(al["id"])
        info = _album_cache.get(al["id"]) or _get(f"https://api.deezer.com/album/{al['id']}")
        _album_cache[al["id"]] = info
        if not isinstance(info, dict) or info.get("error") or info.get("record_type") == "compile" or is_compil(info.get("title", "")):
            continue
        is_live_al = bool(re.search(r"\blive\b|en concert|in concert|unplugged", info.get("title", ""), re.I))
        if is_live_al and not live:
            continue
        ry = (info.get("release_date") or "0000")[:4]
        ry = int(ry) if ry.isdigit() and ry != "0000" else None
        s = 0.0
        if live and not is_live_al:
            s += 40
        if info.get("record_type") in ("single", "ep"):
            s += 8
        if ry and year:
            s += (ry - year) * 0.3 if ry >= year else ((year - ry) if year - ry <= 2 else (year - ry) * 3 + 15)
        else:
            s += 30
        genres = [g["name"] for g in (info.get("genres") or {}).get("data", [])]
        if best is None or s < best[0]:
            best = (s, clean_album(info.get("title", "")), ry, info.get("cover_big"), genres, r["artist"]["name"])
    if not best:
        return _art_only(artist, title, data, live)
    return {"album": best[1], "album_year": best[2], "cover_url": best[3], "genres": best[4], "artist": best[5]}


def _art_only(artist, title, data, live):
    """Dernier recours : aucun album sur pour ce titre (que des « Mix », « Version »...), mais la pochette
    d'un single ou d'un album du meme artiste vaut mieux que rien. L'album, lui, n'est pas modifie."""
    for r in data:
        if not _sim(artist, r["artist"]["name"]):
            continue
        t = r.get("title_short") or r["title"]
        if not (_sim(title, t) or _n(title) in _n(r["title"]) or (_n(t) and _n(t) in _n(title))):
            continue
        if re.search(r"karaoke|instrumental|demo|tribute|cover|re-?recorded", r["title"], re.I):
            continue
        if bool(re.search(r"\blive\b", r["title"] + " " + r["album"]["title"], re.I)) != bool(live):
            continue
        if is_compil(r["album"]["title"]) or not r["album"].get("cover_big"):
            continue
        return {"album": None, "album_year": None, "cover_url": r["album"]["cover_big"], "genres": [], "artist": r["artist"]["name"]}
    return None


def find_artist(title, album=None):
    """Titre seul (+ album des tags) -> (artiste, titre exact) si Deezer est formel, sinon (None, indice)."""
    q = f"{title} {album}" if album and not is_compil(album) else title
    d = _get("https://api.deezer.com/search?q=" + urllib.parse.quote(q) + "&limit=25")
    data = d.get("data", []) if isinstance(d, dict) else []
    same = [r for r in data if _sim(title, r.get("title_short") or r["title"])]
    exact = [r for r in same if _n(title) == _n(r.get("title_short") or r["title"])]
    if album and not is_compil(album):
        for r in exact:
            if _n(album) == _n(r["album"]["title"]) or (len(_n(album)) >= 6 and _n(album) in _n(r["album"]["title"])):
                return r["artist"]["name"], r.get("title_short") or r["title"]
    artists = {r["artist"]["name"] for r in exact[:8]}
    if len(artists) == 1 and len(exact) >= 2:
        name = artists.pop()
        return name, exact[0].get("title_short") or exact[0]["title"]
    return None, (same[0]["artist"]["name"] if same else None)


_artist_cache = {}


def artist_genres(artist):
    """Genres Deezer d'un artiste (via ses albums), quand le morceau lui-meme n'a rien donne."""
    k = _n(artist)
    if k in _artist_cache:
        return _artist_cache[k]
    d = _get("https://api.deezer.com/search?q=" + urllib.parse.quote(artist) + "&limit=10")
    out = []
    for r in (d.get("data", []) if isinstance(d, dict) else []):
        if not _sim(artist, r["artist"]["name"]):
            continue
        info = _album_cache.get(r["album"]["id"]) or _get(f"https://api.deezer.com/album/{r['album']['id']}")
        _album_cache[r["album"]["id"]] = info
        out = [g["name"] for g in ((info or {}).get("genres") or {}).get("data", [])] if isinstance(info, dict) else []
        if out:
            break
    if not out:
        first = re.split(r"\s+(?:&|et|and|feat\.?|ft\.?|featuring|x|vs\.?)\s+|,", artist, flags=re.I)[0].strip()
        if first and first != artist:
            out = artist_genres(first)                  # « Kery James & Demon One & Rohff » -> Kery James
    _artist_cache[k] = out
    return out


def musicbrainz_year(artist, title, live=False):
    q = f'recording:"{title}" AND artist:"{artist}"'
    d = _get("https://musicbrainz.org/ws/2/recording?" + urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 50}), pause=1.1)
    years = []
    for r in (d or {}).get("recordings", []):
        art = " ".join(c.get("name", "") for c in r.get("artist-credit", []) if isinstance(c, dict))
        if not _sim(artist, art) or not _sim(title, r.get("title", "")):
            continue
        if ("live" in (r.get("disambiguation") or "").lower()) != live and live:
            continue
        dt = r.get("first-release-date") or ""
        if re.match(r"\d{4}", dt):
            years.append(int(dt[:4]))
    return min(years) if years else None


def download(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            data = r.read()
        return data if data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG" else None
    except Exception:
        return None


_country_cache = {}


def artist_country(artist):
    """Pays de l'artiste selon MusicBrainz (« FR », « GB », « US »…), ou None si incertain."""
    k = _n(artist)
    if k in _country_cache:
        return _country_cache[k]
    d = _get("https://musicbrainz.org/ws/2/artist?" + urllib.parse.urlencode(
        {"query": f'artist:"{artist}"', "fmt": "json", "limit": 3}), pause=1.1)
    out = None
    for a in (d or {}).get("artists", []):
        if int(a.get("score") or 0) >= 90 and _n(a.get("name", "")) == k:
            out = a.get("country") or None
            break
    _country_cache[k] = out
    return out
