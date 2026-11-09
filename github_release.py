"""Publication d'une version sur GitHub sans l'outil `gh` : utilise la connexion GitHub deja enregistree par git.

    python github_release.py 1.1.0 "Notes de version"
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(ROOT, "dist", "Installer Platine.exe")


def token():
    """Jeton GitHub du gestionnaire d'identifiants de git (jamais affiche ni ecrit)."""
    env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, env=env, timeout=30).stdout
    m = re.search(r"^password=(.+)$", out, re.M)
    if not m:
        sys.exit("Pas de connexion GitHub enregistree sur ce PC (git credential).")
    return m.group(1).strip()


def api(method, url, tok, data=None, headers=None, raw=None):
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "Platine-publier",
         "X-GitHub-Api-Version": "2022-11-28", **(headers or {})}
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub a refuse ({e.code}) : {e.read().decode(errors='replace')[:300]}")


README = """# Platine

Rangement automatique de bibliothèque musicale (noms, tags, pochettes, genres, doublons).

Téléchargez la dernière version dans **Releases** : `Installer.Platine.exe`.
Une fois installé, Platine se met à jour tout seul.
"""


def ensure_not_empty(repo, tok):
    """GitHub refuse les versions sur un depot vide : on y depose un README la premiere fois."""
    import base64
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/README.md",
                                 headers={"Authorization": f"Bearer {tok}", "User-Agent": "Platine-publier"})
    try:
        urllib.request.urlopen(req, timeout=30)
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return
    api("PUT", f"https://api.github.com/repos/{repo}/contents/README.md", tok,
        {"message": "Platine", "content": base64.b64encode(README.encode()).decode()})


def publish(repo, version, notes):
    tok = token()
    ensure_not_empty(repo, tok)
    rel = api("POST", f"https://api.github.com/repos/{repo}/releases", tok,
              {"tag_name": f"v{version}", "name": f"Platine {version}", "body": notes, "draft": False, "prerelease": False})
    with open(EXE, "rb") as f:
        data = f.read()
    upload = rel["upload_url"].split("{")[0] + "?name=" + urllib.request.quote("Installer.Platine.exe")
    asset = api("POST", upload, tok, raw=data, headers={"Content-Type": "application/vnd.microsoft.portable-executable"})
    return rel["html_url"], asset["browser_download_url"], asset["size"]


if __name__ == "__main__":
    src = open(os.path.join(ROOT, "platine", "__init__.py"), encoding="utf-8").read()
    repo = re.search(r'UPDATE_REPO = "([^"]*)"', src).group(1)
    version = sys.argv[1] if len(sys.argv) > 1 else re.search(r'__version__ = "([^"]+)"', src).group(1)
    notes = sys.argv[2] if len(sys.argv) > 2 else "Améliorations et corrections."
    page, url, size = publish(repo, version, notes)
    print("Publie :", page)
    print("Telechargement direct :", url, f"({size // 1024 // 1024} Mo)")
