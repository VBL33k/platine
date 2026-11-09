"""Mesure de la vraie qualite audio : coupure des aigus (kHz) + bitrate.

Un MP3 issu d'un 128 kbps coupe vers 16 kHz meme s'il est re-encode en 320 kbps.
"""
import numpy as np

try:
    import miniaudio
except ImportError:  # pragma: no cover
    miniaudio = None
import mutagen

LOSSLESS = (".flac", ".wav")


def cutoff_khz(path):
    if miniaudio is None:
        return None
    try:
        with open(path, "rb") as f:
            dec = miniaudio.decode(f.read(), output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=44100)
    except Exception:
        return None
    x = np.frombuffer(dec.samples, dtype=np.float32)
    mid = len(x) // 2
    x = x[max(0, mid - 44100 * 20): mid + 44100 * 20]
    n = 4096
    frames = len(x) // n
    if frames < 10:
        return None
    w = np.hanning(n)
    spec = np.zeros(n // 2 + 1)
    for i in range(frames):
        spec += np.abs(np.fft.rfft(x[i * n:(i + 1) * n] * w)) ** 2
    db = 10 * np.log10(spec / frames + 1e-20)
    fr = np.fft.rfftfreq(n, 1 / 44100)
    ref = np.median(db[(fr > 1000) & (fr < 4000)])
    above = np.where(db > ref - 50)[0]
    return round(float(fr[above[-1]] / 1000), 1) if len(above) else None


def analyse(path):
    """-> dict(cutoff, bitrate, quality 0-100, label)"""
    bitrate = 0
    try:
        m = mutagen.File(path)
        if m is not None and m.info:
            bitrate = int(getattr(m.info, "bitrate", 0) or 0) // 1000
    except Exception:
        pass
    c = cutoff_khz(path)
    lossless = path.lower().endswith(LOSSLESS)
    if lossless:
        q = 100
    elif c:
        q = int(max(0, min(99, (c - 13) / (20 - 13) * 90 + min(bitrate, 320) / 320 * 9)))
    else:
        q = int(min(bitrate, 320) / 320 * 80)
    if lossless:
        label = "Sans perte"
    elif c and c >= 19.5:
        label = "Excellente"
    elif c and c >= 18.5:
        label = "Bonne"
    elif c and c >= 16.5:
        label = "Moyenne"
    elif c:
        label = "Faible" if bitrate < 250 else "Faux 320"
    else:
        label = "Inconnue"
    return {"cutoff": c, "bitrate": bitrate, "quality": q, "label": label}


def score(t):
    """Cle de tri pour garder le meilleur fichier."""
    return (t.get("quality") or 0, t.get("bitrate") or 0, t.get("size") or 0)
