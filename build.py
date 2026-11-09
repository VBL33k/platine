"""Fabrique Platine.exe puis l'installeur "Installer Platine.exe".

    .venv\\Scripts\\python build.py
Resultat : dist\\Installer Platine.exe  (un seul fichier a copier sur le PC de destination)
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DIST, BUILD = os.path.join(ROOT, "dist"), os.path.join(ROOT, "build")


def run(args):
    print(">", " ".join(args))
    subprocess.check_call(args, cwd=ROOT)


def sync_version():
    """Reporte platine.__version__ dans l'installeur (affiche dans « Programmes et fonctionnalites »)."""
    import re
    ver = re.search(r'__version__ = "([^"]+)"', open(os.path.join(ROOT, "platine", "__init__.py"), encoding="utf-8").read()).group(1)
    p = os.path.join(ROOT, "installer.py")
    s = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(re.sub(r'^VERSION = "[^"]*"', f'VERSION = "{ver}"', s, flags=re.M))
    return ver


def main():
    print("Version", sync_version())
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    sep = os.pathsep
    # 1) l'application
    run([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Platine",
         "--icon", "assets/platine.ico",
         "--add-data", f"web{sep}web", "--add-data", f"assets{sep}assets", "--add-data", f"data{sep}data",
         "--collect-all", "webview", "--hidden-import", "keyring.backends.Windows",
         "Platine.pyw"])
    mock = os.path.join(DIST, "Platine", "_internal", "web", "mock.js")
    if os.path.exists(mock):
        os.remove(mock)
    # 2) archive de l'application, embarquee dans l'installeur
    payload = os.path.join(BUILD, "platine_app.zip")
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        base = os.path.join(DIST, "Platine")
        for r, _, files in os.walk(base):
            for f in files:
                full = os.path.join(r, f)
                z.write(full, os.path.relpath(full, base))
    # 3) l'installeur
    run([PY, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "Installer Platine",
         "--icon", "assets/platine.ico", "--add-data", f"{payload}{sep}.", "--add-data", f"assets{sep}assets",
         "installer.py"])
    print("\nOK ->", os.path.join(DIST, "Installer Platine.exe"))


if __name__ == "__main__":
    main()
