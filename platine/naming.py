"""Lecture et mise en forme des noms de fichiers.

Format cible : "Artiste - Titre (Sous-titre) (Original Cover) (Version) (Annee).ext"
"""
import difflib
import re
import unicodedata

AUDIO_EXT = (".mp3", ".flac", ".ogg", ".m4a", ".wma", ".wav")

VERSION_WORDS = r"live|remix|mix|edit|version|acoustic|acoustique|unplugged|remaster(?:ed)?|radio|single|demo|instrumental|mono|stereo|extended|original|bonus|explicit|clean|album|session|bbc|reprise|dub|rmx|concert|public"
ELISION = {"l", "d", "j", "m", "t", "s", "n", "c", "qu", "jusqu", "lorsqu", "puisqu", "presqu"}
CONTRACTIONS = {"s", "t", "m", "d", "ll", "re", "ve", "n", "em", "cha", "til"}


def is_audio(name):
    return name.lower().endswith(AUDIO_EXT)


def ascii_low(s):
    s = s.replace("´", "'").replace("’", "'").replace("`", "'")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------- nettoyage

def repair(s):
    """Repare les caracteres casses (encodage) et normalise les espaces/apostrophes."""
    if re.search("[ÂÃ][-¿]", s):          # "PlacÃ©bo" : UTF-8 lu comme du Latin-1
        try:
            s = s.encode("cp1252", "ignore").decode("utf-8")
        except UnicodeError:
            pass
    s = unicodedata.normalize("NFC", s)
    s = "".join(c for c in s if not (0x80 <= ord(c) <= 0x9F))
    s = s.replace("`", "'").replace("´", "'").replace("–", "-").replace("—", "-")
    s = s.replace("+++++", "")
    return re.sub(r"\s+", " ", s).strip()


def _fix_word(w):
    parts = re.split(r"(['’])", w)
    out = []
    for i, p in enumerate(parts):
        if i % 2 == 1 or not p:
            out.append(p)
            continue
        if i == 0:
            out.append(p[0].upper() + p[1:] if p[0].islower() else p)
            continue
        prev = parts[i - 2].lower() if i >= 2 else ""
        if p.lower() in CONTRACTIONS and prev not in ELISION and re.search(r"[a-z]$", prev, re.I):
            out.append(p.lower())
        else:
            out.append(p[0].upper() + p[1:] if p[0].islower() else p)
    return "".join(out)


def _weird(w):
    """Mot aux majuscules melangees au hasard ("SAlEM", "heR") : pas un nom stylise (KoRn, McCartney, DeBarge)."""
    w = re.sub(r"^(mc|mac|de|la|le|o')", "", w, flags=re.I)
    return len(re.findall(r"[a-zà-ÿ][A-ZÀ-Þ]", w)) + len(re.findall(r"[A-ZÀ-Þ]{2}[a-zà-ÿ]", w)) >= 1


def fix_case(s):
    """Remet en forme un nom crie en MAJUSCULES ou ecrit au hasard, sinon le laisse tel quel."""
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return s
    upper = sum(c.isupper() for c in letters) / len(letters)
    words = re.findall(r"[^\W\d_]{3,}", s)
    weird = sum(_weird(w) for w in words)
    if (upper > 0.75 and len(letters) > 6) or (weird >= 2 and weird / len(words) > 0.3):
        s = s.lower()
    s = title_case(s)
    s = re.sub(r"^(Mc|Dj)(?= )|(?<= )Dj(?= )", lambda m: m.group(0).upper(), s)   # "MC Solaar", "DJ Shadow"
    return s.replace("Quelqu'Un", "Quelqu'un")


def title_case(s):
    """Majuscule a chaque mot, sigles et noms stylises conserves (ACDC, KoRn, R.E.M.)."""
    s = re.sub(r"[^\W\d_]+(?:['’][^\W\d_]+)*", lambda m: _fix_word(m.group(0)), s)
    return s.replace("Quelqu'Un", "Quelqu'un")


# ---------------------------------------------------------------- parsing

AUDIO_TAIL = re.compile(r"(\.(?:mp3|wma|wav|flac|m4a|ogg|aac))+$", re.I)


def clean_stem(stem):
    """Nettoie un nom de fichier sans extension : numeros de piste, underscores, extensions en double..."""
    stem = AUDIO_TAIL.sub("", repair(stem))                           # "titre.wma" (double extension)
    stem = re.sub(r"\s*_+-_+\s*", " - ", stem)                         # "RENAUD_-_Titre"
    scene = " " not in stem and "-" in stem                            # "01-mafia_k1_fry-pour_ceux-mvp"
    if stem.count("_") >= 2 and stem.count("_") >= stem.count(" "):   # "001)_iam_-_demain_c'est_loin"
        stem = re.sub(r"\s*_\s*", " ", stem)
    else:
        stem = re.sub(r"\s*_\s+", " - ", stem)
    stem = repair(stem)
    # numeros de piste : "001) ", "07.Titre", "08. Artiste", "04 Artiste", "02 - Titre", "01-artiste"
    # (jamais "113 - Titre", "50 Cent", "16 Horsepower" : pas de zero devant, pas de parenthese, pas de point)
    stem = re.sub(r"^\d{1,3}\s*[)\]]\s*", "", stem)
    stem = re.sub(r"^\d{1,3}\.\s*(?=\D)", "", stem)
    stem = re.sub(r"^0\d\s*-?\s*(?=\D)", "", stem)
    stem = re.sub(r"^\d{1,3}\s*[-.]\s+(?=\D.* - )", "", stem)    # "1 - Artiste - Titre"
    stem = re.sub(r"(\S)- (\S)", r"\1 - \2", stem)                 # "Artiste- Titre"
    if scene and " - " not in stem:                                  # format "scene" : artiste-titre-groupe
        bits = [b.strip() for b in stem.split("-") if b.strip()]
        if len(bits) >= 3 and re.fullmatch(r"[a-z0-9]{2,5}", bits[-1]):
            bits = bits[:-1]                                         # "...-mvp" : nom du groupe de diffusion
        if len(bits) >= 2:
            stem = bits[0] + " - " + " ".join(bits[1:])
    stem = re.sub(r"\[((?:19|20)\d\d)\]", r"(\1)", stem)
    stem = re.sub(r"\s*[\(\[](?:official\s+)?(?:music\s+|lyrics?\s+)?(?:video|audio|clip|lyrics?|hq|hd|4k|"
                  r"clip officiel|vid[eé]o officielle|visualizer)[\)\]]", "", stem, flags=re.I)
    stem = re.sub(r"\s+by\s+\S+.*\(.*crew\)$", "", stem, flags=re.I)   # "... by marseillais (inrap.ru crew)"
    stem = re.sub(r"\s+-\s+copie$", "", stem, flags=re.I)               # "- Copie" de Windows
    return re.sub(r"\s+", " ", stem).strip()


def parse_name(filename):
    """-> dict(artist, title, extras[], year, ext). artist peut etre "" quand le nom ne contient que le titre."""
    stem, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
    stem = clean_stem(stem)
    year = None
    m = re.search(r"\s*\(((?:19|20)\d\d)(?:\s*&\s*(?:19|20)\d\d)?\)\s*$", stem)
    if m:
        year, stem = int(m.group(1)), stem[: m.start()].strip()
    artist, title = stem.split(" - ", 1) if " - " in stem else ("", stem)
    title = re.sub(r"^0\d\s+(?=\D)", "", title.strip())               # "Supreme NTM - 01 Pour un nouveau massacre"
    extras = re.findall(r"\s*([\(\[][^\)\]]*[\)\]])", title)
    core = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title).strip()
    lead = re.match(r"^\s*(\([^)]*\))\s*", title)                 # "(Sittin' On) The Dock..."
    if lead:
        extras = [e for e in extras if e != lead.group(1)]
        core = lead.group(1) + " " + core
    if not core:
        return None
    return {"artist": artist.strip(), "title": core, "extras": [e.strip() for e in extras], "year": year,
            "ext": "." + ext.lower() if ext else ""}


def split_name(filename):
    """-> dict(artist, title, extras[], year, ext) ou None si l'artiste n'est pas dans le nom."""
    p = parse_name(filename)
    return p if p and p["artist"] else None


def clean_artist(a):
    """Artiste lu dans les tags : retire les numeros de piste colles ("07.Tout Simplement Noir", "04 Sniper")."""
    a = repair(a or "")
    a = re.sub(r"^\d{1,3}\s*[)\].]\s*(?=\D)|^0\d\s+(?=\D)", "", a).strip()
    return a.strip(" .-_")


JUNK_TITLE = re.compile(r"^(track|piste|audio|unknown|inconnu|sans titre|untitled|titre)?\s*\d*$", re.I)


FR_WORDS = {"le", "la", "les", "un", "une", "des", "du", "de", "et", "pas", "pour", "mon", "ma", "mes", "ton", "ta", "tes",
            "sur", "dans", "qui", "que", "quoi", "est", "sont", "nous", "vous", "je", "tu", "il", "elle", "on", "ils",
            "au", "aux", "avec", "sans", "comme", "c'est", "j'ai", "rue", "vie", "monde", "coeur", "jour", "nuit",
            "demain", "toi", "moi", "fils", "frere", "freres", "quand", "tout", "rien"}


def is_french(*texts):
    """Titre/album en francais ? (pour separer Rap FR / Rap US quand l'artiste est inconnu)"""
    words = re.findall(r"[\w']+", ascii_low(" ".join(t for t in texts if t)))
    if not words:
        return False
    hits = sum(w in FR_WORDS or w.startswith(("l'", "d'", "j'", "qu'", "c'", "n'", "s'", "m'", "t'")) for w in words)
    accents = bool(re.search(r"[éèêàùçôîû]", " ".join(t for t in texts if t).lower()))
    return hits >= 1 and (hits / len(words) >= 0.2 or accents)


EN_WORDS = {"the", "you", "my", "your", "me", "it", "is", "of", "in", "on", "and", "love", "i'm", "don't", "can't", "what",
            "this", "that", "we", "they", "get", "got", "up", "down", "money", "life", "street", "game", "girl", "baby",
            "night", "time", "real", "back", "all", "no", "yeah", "gonna", "wanna", "be", "to", "a", "for", "with"}


def language(*texts):
    """'fr', 'en' ou None si on ne peut pas trancher (titre d'un seul mot, nom propre...)."""
    raw = " ".join(t for t in texts if t)
    words = re.findall(r"[\w']+", ascii_low(raw))
    fr = sum(w in FR_WORDS or w.startswith(("l'", "d'", "j'", "qu'", "c'", "n'", "s'", "m'", "t'")) for w in words)
    fr += 2 * bool(re.search(r"[éèêàùçôîû]", raw.lower()))
    en = sum(w in EN_WORDS for w in words)
    return "fr" if fr > en else "en" if en > fr else None


def numeric_only(a):
    """"02", "102" : numero de piste pris pour un artiste (a verifier contre les artistes connus, cf. "113")."""
    return bool(re.fullmatch(r"\d{1,3}", (a or "").strip()))


def part_number(filename):
    """"Titre (1)" / "Titre (2)" : parties differentes d'un meme morceau, jamais des doublons entre elles."""
    m = re.search(r"\((\d)\)(?:\s*\((?:19|20)\d\d\))?\.\w+$", filename)
    return m.group(1) if m else None


def cover_of(extras):
    for e in extras:
        m = re.match(r"^\((.+?) Cover\)$", e, re.I)
        if m:
            return m.group(1).strip()
    return None


def version_of(name):
    s = ascii_low(name)
    tags = set()
    if re.search(r"\b(live|concert|en public|lyceum|bercy|zenith|olympia|kroq)\b", s): tags.add("live")
    if re.search(r"\b(acoustic|acoustique|unplugged)\b", s): tags.add("acoustique")
    if re.search(r"\b(remix|rmx|retake)\b|\bmix\)|\bdub\b", s): tags.add("remix")
    if re.search(r"\bdemo\b", s): tags.add("demo")
    if re.search(r"\binstrumental\b", s): tags.add("instrumental")
    if re.search(r"\bextend", s): tags.add("extended")
    return "+".join(sorted(tags)) or "studio"


IGNORABLE = re.compile(r"^(\d{4}|\d|bis|copie|remaster(ed)?( \d{4})?|\d{4} remaster(ed)?|single version|radio edit|"
                       r"album version|explicit|clean|original mix|mono|stereo|feat .*|with .*|.* cover)$")


def version_key(filename):
    """Signature de version stricte : deux fichiers ne sont des doublons que si elle est identique."""
    p = split_name(filename)
    if not p:
        return ""
    keys = []
    for e in p["extras"]:
        k = re.sub(r"[^a-z0-9 ]+", " ", ascii_low(e)).strip()
        k = re.sub(r"\s+", " ", k)
        if k and not IGNORABLE.match(k):
            keys.append(k)
    return "|".join(sorted(keys))


def build_name(artist, title, extras=(), cover=None, year=None, ext=".mp3"):
    """Construit le nom final. Les sous-titres restent colles au titre, puis Cover, puis la version."""
    ver_re = re.compile(VERSION_WORDS + r"|feat|with |\d{4}", re.I)
    subs = [e for e in extras if not ver_re.search(e) and not re.search(r"cover\)$", e, re.I)]
    vers = [e for e in extras if ver_re.search(e) and not re.search(r"cover\)$", e, re.I)]
    parts = [f"{title_case(repair(artist))} - {title_case(repair(title))}"] + [title_case(s) for s in subs]
    if cover:
        parts.append(f"({cover} Cover)")
    parts += [title_case(v) for v in vers]
    if year:
        parts.append(f"({year})")
    name = re.sub(r"\s+", " ", " ".join(parts)).strip()
    name = re.sub(r'[<>:"/\\|?*]', "", name)        # caracteres interdits sous Windows
    return name + ext.lower()


# ---------------------------------------------------------------- comparaison

STOP = {"the", "and", "feat", "featuring", "with", "les", "band", "orchestra", "experience"}


def artist_key(a):
    a = ascii_low(a)
    a = re.split(r"\s+(?:feat\.?|ft\.?|featuring|vs\.?|x|with)\s+|\s*&\s*|\s+and\s+|\s+et\s+|,", a)[0]
    a = re.sub(r"^the\s+", "", a.strip())
    return re.sub(r"[^a-z0-9]", "", a)


def artist_tokens(a):
    return {w for w in re.split(r"[^a-z0-9]+", ascii_low(a)) if len(w) >= 3 and w not in STOP}


def title_key(t):
    t = ascii_low(t)
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    t = re.sub(r"\b(feat\.?|ft\.?|featuring)\b.*$", " ", t)
    t2 = re.sub(r"\b(" + VERSION_WORDS + r")\b", " ", t)
    if re.sub(r"[^a-z0-9]", "", t2):
        t = t2
    t = t.replace("&", " and ").replace("'", "")
    t = re.sub(r"^the ", "", re.sub(r"[^a-z0-9]+", " ", t).strip())
    return t


def same_title(t1, t2):
    if t1 == t2:
        return True
    if re.findall(r"\d+", t1) != re.findall(r"\d+", t2) or min(len(t1), len(t2)) < 5:
        return False
    return difflib.SequenceMatcher(None, t1, t2).ratio() >= 0.9


def same_artist(a1, a2):
    k1, k2 = artist_key(a1), artist_key(a2)
    if k1 == k2:
        return True
    if min(len(k1), len(k2)) >= 6 and difflib.SequenceMatcher(None, k1, k2).ratio() >= 0.88:
        return True
    t1, t2 = artist_tokens(a1), artist_tokens(a2)
    return bool(t1 and t2) and len(t1 & t2) / len(t1 | t2) >= 0.5
