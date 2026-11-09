"""Mises a jour automatiques : les nouvelles versions sont publiees sur GitHub (page « Releases »).

Platine regarde la derniere version publiee, la telecharge sur demande et lance l'installeur en mode
« --update » : il remplace l'application au meme endroit puis la relance. Bibliotheque, reglages et cle
API ne sont jamais touches (ils sont dans %APPDATA% et dans le coffre-fort de Windows).
"""
import json
import os
import re
import subprocess
import tempfile
import urllib.request

from . import UPDATE_REPO, __version__

UA = {"User-Agent": f"Platine/{__version__}", "Accept": "application/vnd.github+json"}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def check():
    """-> dict(version, notes, url, size) si une version plus recente existe, sinon None (jamais d'erreur)."""
    if not UPDATE_REPO or "/" not in UPDATE_REPO:
        return None
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            rel = json.load(r)
    except Exception:
        return None
    version = (rel.get("tag_name") or "").lstrip("vV")
    if _ver(version) <= _ver(__version__):
        return None
    exes = [a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")]
    asset = next((a for a in exes if "platine" in a["name"].lower()), exes[0] if exes else None)
    if not asset:
        return None
    return {"version": version, "notes": (rel.get("body") or "").strip()[:600],
            "url": asset["browser_download_url"], "size": asset.get("size") or 0}


def download(info, progress=lambda done, total: None):
    """Telecharge l'installeur dans un dossier temporaire et verifie que c'est bien un programme complet."""
    folder = os.path.join(tempfile.gettempdir(), "Platine-maj")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Installer Platine {info['version']}.exe")
    req = urllib.request.Request(info["url"], headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress(done, total)
    with open(path + ".part", "rb") as f:
        if f.read(2) != b"MZ" or (total and os.path.getsize(path + ".part") != total):
            os.remove(path + ".part")
            raise IOError("Le téléchargement est incomplet. Réessaie plus tard.")
    os.replace(path + ".part", path)
    return path


def install(path):
    """Lance l'installeur en mode mise a jour ; il ferme Platine, le remplace et le relance."""
    subprocess.Popen([path, "--update"], close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
