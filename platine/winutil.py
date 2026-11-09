"""Petits outils Windows : raccourcis, demarrage automatique, ouverture de dossier."""
import os
import subprocess
import sys

STARTUP = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")


def _ps(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def make_shortcut(lnk, target, icon=None, args="", workdir=None):
    q = lambda s: s.replace("'", "''")
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
              f"$s.TargetPath='{q(target)}';$s.Arguments='{q(args)}';"
              f"$s.WorkingDirectory='{q(workdir or os.path.dirname(target))}';"
              + (f"$s.IconLocation='{q(icon)}';" if icon else "") + "$s.Save()")
    _ps(script)


def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else None


def set_autostart(on):
    lnk = os.path.join(STARTUP, "Platine.lnk")
    exe = exe_path()
    if on and exe:
        make_shortcut(lnk, exe, exe, "--minimized")
    elif not on and os.path.exists(lnk):
        os.remove(lnk)


def open_path(p):
    if p and os.path.exists(p):
        os.startfile(p)
