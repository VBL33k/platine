"""Publie une nouvelle version de Platine : chez Papa, Platine proposera tout seul de se mettre a jour.

    .venv\\Scripts\\python publier.py 1.2.0 "Ce qui change, en une phrase"

1. change le numero de version (platine/__init__.py),
2. fabrique l'installeur (build.py),
3. le publie sur GitHub (page « Releases » du depot UPDATE_REPO) avec l'outil `gh`.
Sans `gh`, le script explique comment le publier a la main depuis le site GitHub.
"""
import os
import re
import shutil
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
INIT = os.path.join(ROOT, "platine", "__init__.py")
EXE = os.path.join(ROOT, "dist", "Installer Platine.exe")


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit('Utilisation : python publier.py 1.2.0 "Ce qui change"')
    version, notes = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "Améliorations et corrections.")
    src = open(INIT, encoding="utf-8").read()
    repo = re.search(r'UPDATE_REPO = "([^"]*)"', src).group(1)
    current = re.search(r'__version__ = "([^"]+)"', src).group(1)
    if not repo:
        sys.exit("UPDATE_REPO est vide dans platine/__init__.py : indique d'abord ton depot GitHub (proprietaire/depot).")
    if tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
        sys.exit(f"La version {version} doit etre plus grande que l'actuelle ({current}).")
    open(INIT, "w", encoding="utf-8").write(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', src))
    print(f"Version {current} -> {version}")
    subprocess.check_call([sys.executable, os.path.join(ROOT, "build.py")], cwd=ROOT)

    gh = shutil.which("gh")
    if gh:
        subprocess.check_call([gh, "release", "create", f"v{version}", EXE, "--repo", repo,
                               "--title", f"Platine {version}", "--notes", notes])
        print(f"\nPublie ✓  Les Platine installes proposeront la mise a jour dans les heures qui viennent "
              f"(ou tout de suite avec Reglages > Rechercher une mise a jour).")
    else:
        try:                                   # connexion GitHub deja enregistree par git sur ce PC
            from github_release import publish
            page, _, _ = publish(repo, version, notes)
            print(f"\nPublie ✓  {page}\nLes Platine installes proposeront la mise a jour dans les heures qui viennent.")
            return
        except SystemExit as e:
            print(f"\nPublication automatique impossible ({e}).")
        print("\nPublication a la main (2 minutes) :")
        print(f"  1. Sur la page qui s'ouvre, mets « v{version} » comme tag et « Platine {version} » comme titre.")
        print(f"  2. Description : {notes}")
        print(f"  3. Glisse le fichier {EXE} dans la zone « Attach binaries », puis « Publish release ».")
        webbrowser.open(f"https://github.com/{repo}/releases/new?tag=v{version}&title=Platine%20{version}")


if __name__ == "__main__":
    main()
