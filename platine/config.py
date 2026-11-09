"""Reglages de l'application, stockes en JSON dans %APPDATA%\\Platine."""
import json
import os
import sys
import threading

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Platine")
CACHE_DIR = os.path.join(APP_DIR, "cache")
THUMB_DIR = os.path.join(CACHE_DIR, "pochettes")
COVER_DL_DIR = os.path.join(CACHE_DIR, "telechargements")
DB_PATH = os.path.join(APP_DIR, "platine.db")
CONFIG_PATH = os.path.join(APP_DIR, "reglages.json")
LOG_PATH = os.path.join(APP_DIR, "platine.log")

for d in (APP_DIR, CACHE_DIR, THUMB_DIR, COVER_DL_DIR):
    os.makedirs(d, exist_ok=True)


def resource_path(*parts):
    """Chemin vers un fichier embarque (fonctionne aussi une fois compile par PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


GENRES = [
    "Rock Classique", "Rock Alternatif & Indie", "Grunge", "Hard Rock", "Metal", "Punk",
    "Hardcore & Fusion", "Rap FR", "Rap US", "Reggae & Dub", "Ska", "Electro", "Pop",
    "Annees 80", "Chanson Francaise", "Rock Francais", "Soul & Funk", "Disco", "Jazz",
    "Blues & Rock'n'Roll 50s", "Musique Bresilienne & Latine", "Musique Du Monde", "Bandes Originales",
]

DEFAULTS = {
    "music_dir": "",                # dossier VRAC (ou dossier de depart quand une cle USB est choisie)
    "usb_dir": "",                  # cle USB deja organisee ou classer les morceaux (vide = pas de cle)
    "genre_folders": True,          # copier chaque son dans le dossier de son genre
    "cover_folder": True,           # copier les reprises dans un dossier Cover
    "watch": True,                  # surveiller le dossier pendant que l'appli est ouverte
    "autostart": False,             # lancer au demarrage de Windows
    "ai_enabled": True,
    "api_key": "",
    "ai_workspace": "",             # ID de workspace Anthropic (seulement pour les cles non rattachees)
    "ai_model": "claude-opus-5-5",
    "ai_budget_eur": 1.0,           # plafond mensuel
    "eur_per_usd": 0.93,
    "onboarded": False,
}

_lock = threading.Lock()


class Config:
    def __init__(self):
        self.data = dict(DEFAULTS)
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, ValueError):
                pass

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def update(self, **kw):
        with _lock:
            self.data.update({k: v for k, v in kw.items() if k in DEFAULTS})
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)

    def public(self):
        d = dict(self.data)
        k = d.pop("api_key", "")
        d["api_key_set"] = bool(k)
        d["api_key_hint"] = ("••••" + k[-4:]) if k else ""
        return d


config = Config()
