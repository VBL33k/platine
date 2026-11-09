"""Installeur / desinstalleur de Platine (fenetre simple, aux couleurs de l'application)."""
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog
import winreg
import zipfile

APP = "Platine"
VERSION = "1.2.0"
INSTALL_DIR = os.path.join(os.environ["LOCALAPPDATA"], "Programs", APP)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Platine"
BG, SURF, TXT, MUTED, ACC = "#17121c", "#211a27", "#efe6d8", "#a99bb1", "#f2b33c"
WEBVIEW2 = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
TEST_DIR = sys.argv[sys.argv.index("--test") + 1] if "--test" in sys.argv else None


def res(*p):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), *p)


def ps(cmd):
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                         capture_output=True, text=True, creationflags=0x08000000)
    return out.stdout.strip()


def folder(name):
    return ps(f"[Environment]::GetFolderPath('{name}')")


def shortcut(lnk, target, args="", icon=None):
    q = lambda s: s.replace("'", "''")
    ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');$s.TargetPath='{q(target)}';"
       f"$s.Arguments='{q(args)}';$s.WorkingDirectory='{q(os.path.dirname(target))}';"
       f"$s.IconLocation='{q(icon or target)}';$s.Save()")


def webview2_ok():
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, WEBVIEW2), (winreg.HKEY_CURRENT_USER, WEBVIEW2.replace("WOW6432Node\\", ""))):
        try:
            with winreg.OpenKey(hive, key) as k:
                v, _ = winreg.QueryValueEx(k, "pv")
                if v and v != "0.0.0.0":
                    return True
        except OSError:
            pass
    return False


def previous_location():
    """Dossier d'une installation precedente (mise a jour au meme endroit), sinon None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            v, _ = winreg.QueryValueEx(k, "InstallLocation")
            return v if v and os.path.isdir(v) else None
    except OSError:
        return None


def is_platine_dir(d):
    """Garde-fou : on ne supprime jamais un dossier qui ne contient pas Platine."""
    return bool(d) and os.path.isfile(os.path.join(d, "Platine.exe")) and len(os.path.normpath(d)) > 3


def kill_running():
    subprocess.run(["taskkill", "/F", "/IM", "Platine.exe"], capture_output=True, creationflags=0x08000000)


def install(progress, target=None):
    target = TEST_DIR or target or INSTALL_DIR
    kill_running()
    old = previous_location()
    if not TEST_DIR and old and os.path.normcase(os.path.normpath(old)) != os.path.normcase(os.path.normpath(target)) \
            and is_platine_dir(old):
        shutil.rmtree(old, ignore_errors=True)       # l'ancienne installation a ete deplacee
    if os.path.isdir(os.path.join(target, "_internal")):
        shutil.rmtree(os.path.join(target, "_internal"), ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(res("platine_app.zip")) as z:
        names = z.namelist()
        for i, n in enumerate(names, 1):
            z.extract(n, target)
            if i % 20 == 0 or i == len(names):
                progress(i / len(names) * 0.85)
    exe = os.path.join(target, "Platine.exe")
    me = sys.executable if getattr(sys, "frozen", False) else None
    if me:
        shutil.copy2(me, os.path.join(target, "Desinstaller Platine.exe"))
    if TEST_DIR:
        progress(1.0)
        return exe
    desktop, programs = folder("Desktop"), os.path.join(folder("Programs"), APP)
    os.makedirs(programs, exist_ok=True)
    shortcut(os.path.join(desktop, "Platine.lnk"), exe)
    shortcut(os.path.join(programs, "Platine.lnk"), exe)
    shortcut(os.path.join(programs, "Désinstaller Platine.lnk"), os.path.join(target, "Desinstaller Platine.exe"), "--uninstall", exe)
    progress(0.95)
    size_kb = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(target) for f in fs) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", APP), ("DisplayVersion", VERSION), ("Publisher", "Platine"),
                          ("DisplayIcon", exe), ("InstallLocation", target),
                          ("UninstallString", f'"{os.path.join(target, "Desinstaller Platine.exe")}" --uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
    progress(1.0)
    return exe


def uninstall():
    kill_running()
    for lnk in (os.path.join(folder("Desktop"), "Platine.lnk"), os.path.join(folder("Startup"), "Platine.lnk")):
        if os.path.exists(lnk):
            os.remove(lnk)
    shutil.rmtree(os.path.join(folder("Programs"), APP), ignore_errors=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    # le dossier d'installation (et ce desinstalleur) est supprime apres la fermeture
    here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None
    target = here if is_platine_dir(here) else (previous_location() or INSTALL_DIR)
    if is_platine_dir(target):
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{target}"', shell=True, creationflags=0x08000000)


class Ui:
    def __init__(self, mode):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.mode = mode
        self.r = tk.Tk()
        self.r.title({"install": "Installer Platine", "update": "Mise à jour de Platine"}.get(mode, "Désinstaller Platine"))
        self.r.configure(bg=BG)
        self.r.geometry("580x500")
        self.r.resizable(False, False)
        try:
            self.r.iconbitmap(res("assets", "platine.ico"))
        except Exception:
            pass
        self.logo = tk.PhotoImage(file=res("assets", "platine.png")).subsample(8, 8)
        tk.Label(self.r, image=self.logo, bg=BG).pack(pady=(34, 10))
        tk.Label(self.r, text="Platine", bg=BG, fg=TXT, font=("Bahnschrift SemiBold", 26)).pack()
        msg = {"install": "Ta musique, rangée toute seule.\nPlatine va s'installer et une icône apparaîtra sur le bureau.",
               "update": "Installation de la nouvelle version…\nTa bibliothèque et tes réglages sont conservés."}.get(
            mode, "Platine va être retiré de cet ordinateur.\nTa musique n'est pas touchée.")
        self.info = tk.Label(self.r, text=msg, bg=BG, fg=MUTED, font=("Segoe UI", 11), justify="center")
        self.info.pack(pady=(8, 14))
        self.target = previous_location() or INSTALL_DIR
        self.where = tk.Frame(self.r, bg=SURF, padx=14, pady=10)
        if mode == "install" and not TEST_DIR:
            tk.Label(self.where, text="Dossier d'installation", bg=SURF, fg=MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
            self.path = tk.Label(self.where, text=self.short(self.target), bg=SURF, fg=TXT, font=("Segoe UI Semibold", 10),
                                 anchor="w", width=44)
            self.path.grid(row=1, column=0, sticky="w")
            tk.Button(self.where, text="Changer…", command=self.pick, bg=SURF, fg=ACC, activebackground=SURF,
                      activeforeground="#ffc356", relief="flat", bd=0, font=("Segoe UI Semibold", 10),
                      cursor="hand2").grid(row=0, column=1, rowspan=2, padx=(10, 0))
            self.where.pack(pady=(0, 18))
        self.bar = tk.Canvas(self.r, width=380, height=6, bg=SURF, highlightthickness=0)
        self.fill = self.bar.create_rectangle(0, 0, 0, 6, fill=ACC, width=0)
        self.btn = tk.Button(self.r, text="Installer" if mode == "install" else "Désinstaller", command=self.go,
                             bg=ACC, fg="#1b1305", activebackground="#ffc356", relief="flat", bd=0,
                             font=("Bahnschrift SemiBold", 14), padx=34, pady=10, cursor="hand2")
        self.btn.pack()
        self.exe = None
        if mode == "update":
            self.btn.pack_forget()
            self.r.after(400, self.go)
        if mode == "install" and not webview2_ok():
            self.info.config(text="Il manque un composant Microsoft (WebView2).\nInstalle-le depuis "
                                  "go.microsoft.com/fwlink/p/?LinkId=2124703 puis relance cet installeur.", fg="#e5583f")

    @staticmethod
    def short(path, n=52):
        return path if len(path) <= n else path[:16] + "…" + path[-(n - 17):]

    def pick(self):
        d = filedialog.askdirectory(parent=self.r, title="Où installer Platine ?",
                                    initialdir=os.path.dirname(self.target) if os.path.isdir(os.path.dirname(self.target)) else "C:\\")
        if not d:
            return
        d = os.path.normpath(d)
        if os.path.basename(d).lower() != APP.lower():
            d = os.path.join(d, APP)          # toujours dans un sous-dossier « Platine », jamais en vrac
        self.target = d
        self.path.config(text=self.short(d))

    def progress(self, v):
        self.r.after(0, lambda: self.bar.coords(self.fill, 0, 0, 380 * v, 6))

    def go(self):
        if self.exe:
            subprocess.Popen([self.exe], cwd=os.path.dirname(self.exe))
            self.r.destroy()
            return
        self.btn.pack_forget()
        self.where.pack_forget()
        self.bar.pack(pady=6)
        self.info.config(text={"install": "Installation en cours…", "update": "Installation de la nouvelle version…"}.get(
            self.mode, "Désinstallation…"), fg=MUTED)
        threading.Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            if self.mode in ("install", "update"):
                self.exe = install(self.progress, self.target)
                self.r.after(0, self.relaunch if self.mode == "update" else self.finished)
            else:
                uninstall()
                self.r.after(0, lambda: (self.info.config(text="Platine a été désinstallé."), self.r.after(1800, self.r.destroy)))
        except PermissionError:
            self.r.after(0, self.denied)
        except Exception as e:
            self.r.after(0, lambda: self.info.config(text=f"Erreur : {e}", fg="#e5583f"))

    def denied(self):
        self.bar.pack_forget()
        self.info.config(text="Windows refuse d'écrire dans ce dossier.\nChoisis-en un autre (ton dossier personnel, un autre disque…).",
                         fg="#e5583f")
        self.where.pack(pady=(0, 18))
        self.btn.pack()

    def relaunch(self):
        self.bar.pack_forget()
        self.info.config(text="C'est à jour ✓\nPlatine se rouvre…", fg=TXT)
        subprocess.Popen([self.exe], cwd=os.path.dirname(self.exe))
        self.r.after(1500, self.r.destroy)

    def finished(self):
        self.bar.pack_forget()
        self.info.config(text="C'est installé ✓\nTu trouveras Platine sur ton bureau.", fg=TXT)
        self.btn.config(text="Lancer Platine")
        self.btn.pack()

    def run(self):
        self.r.mainloop()


if __name__ == "__main__":
    Ui("uninstall" if "--uninstall" in sys.argv else "update" if "--update" in sys.argv else "install").run()
