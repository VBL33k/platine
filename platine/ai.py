"""Claude pour les cas difficiles (annee, genre, reprise), avec plafond de depense mensuel.

Strategie economique :
  1. un appel groupe (jusqu'a 25 morceaux) sans recherche web, effort bas ;
  2. pour ce qui reste incertain, un 2e appel avec recherche web (2 recherches max) si le budget le permet.
Les reponses peu sures ne sont jamais appliquees : elles vont dans "A verifier".
"""
import datetime
import json
import re

from .config import GENRES, config
from .db import db

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None

SERVICE = "Platine"

# $ par million de tokens (entree, sortie, ecriture cache, lecture cache)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
WEB_SEARCH_USD = 0.01  # par recherche
MODEL_FAST = "claude-haiku-5-5"
WEB_MAX = 20            # morceaux verifies sur le web au plus, par rangement
MODEL_WEB = "claude-sonnet-5-5"


def get_key():
    if keyring:
        try:
            k = keyring.get_password(SERVICE, "anthropic")
            if k:
                return k
        except Exception:
            pass
    return config["api_key"]


def set_key(k):
    k = (k or "").strip()
    if keyring:
        try:
            if k:
                keyring.set_password(SERVICE, "anthropic", k)
            else:
                keyring.delete_password(SERVICE, "anthropic")
            config.update(api_key="")
            return
        except Exception:
            pass
    config.update(api_key=k)


def has_key():
    return bool(get_key())


def make_client(key=None, timeout=180.0, workspace=None):
    import anthropic

    ws = (workspace if workspace is not None else config["ai_workspace"] or "").strip()
    headers = {"anthropic-workspace-id": ws} if ws else None
    return anthropic.Anthropic(api_key=(key or get_key()).strip(), timeout=timeout, max_retries=2,
                               default_headers=headers)


WORKSPACE_MSG = ("Cette clé n'est rattachée à aucun espace de travail. Crée la clé depuis un workspace "
                 "de la console Anthropic, ou indique l'ID du workspace (wrkspc_…) ci-dessous.")


def _is_workspace_error(e):
    return "workspace" in str(getattr(e, "message", e)).lower()


def month():
    return datetime.date.today().strftime("%Y-%m")


def budget_status():
    spent_usd = db.month_spend_usd(month())
    rate = config["eur_per_usd"] or 0.93
    limit_eur = float(config["ai_budget_eur"] or 0)
    spent_eur = spent_usd * rate
    return {"spent_eur": round(spent_eur, 3), "limit_eur": limit_eur,
            "left_eur": round(max(0.0, limit_eur - spent_eur), 3), "month": month()}


def _cost_usd(model, usage):
    pi, po, pcw, pcr = PRICES.get(model, PRICES["claude-opus-5-5"])
    u = usage
    cost = (getattr(u, "input_tokens", 0) or 0) * pi + (getattr(u, "output_tokens", 0) or 0) * po
    cost += (getattr(u, "cache_creation_input_tokens", 0) or 0) * pcw + (getattr(u, "cache_read_input_tokens", 0) or 0) * pcr
    cost /= 1e6
    stu = getattr(u, "server_tool_use", None)
    searches = getattr(stu, "web_search_requests", 0) if stu else 0
    return cost + (searches or 0) * WEB_SEARCH_USD


def _can_spend(est_usd):
    b = budget_status()
    return config["ai_enabled"] and has_key() and b["left_eur"] >= est_usd * (config["eur_per_usd"] or 0.93)


SYSTEM = f"""Tu es l'assistant de rangement musical d'une application nommee Platine. Tu aides a completer
les informations de morceaux de musique d'une collection personnelle (rock, punk, chanson francaise, reggae, rap...).

Pour chaque morceau tu donnes, quand on te le demande :
- year : l'annee de sortie de CETTE version (une reprise ou un live a sa propre annee ; pour un remaster, l'annee
  de sortie d'origine ; jamais l'annee d'une compilation).
- genre : un seul genre, choisi STRICTEMENT dans cette liste : {", ".join(GENRES)}.
  Le genre est celui de l'artiste qui joue le morceau (une reprise va dans le genre de celui qui la joue).
- is_cover / original_artist : si le morceau est une reprise, l'artiste qui a cree la chanson (le vrai original,
  meme s'il n'est pas dans la collection). Un artiste qui rejoue sa propre chanson n'est PAS une reprise.
  Un standard tres ancien sans original clair (Summertime, Blue Moon...) : is_cover=false.

- artist / title : quand l'artiste est inconnu ("?"), l'artiste qui interprete ce titre, deduit du titre, de l'album
  et du nom de fichier fournis (ex. titre "Pour un nouveau massacre", album "1993... J'appuie sur la gachette" ->
  Supreme NTM). Redonne aussi le titre correctement ecrit. Confiance dans artist_confidence.

Pour chaque valeur, donne une confiance : "high" (tu en es sur), "medium" ou "low".
N'invente jamais : si tu ne sais pas, mets null et "low". Ecris des notes courtes en francais.

Reponds UNIQUEMENT par un objet JSON de la forme :
{{"items": [{{"id": 1, "year": 1975, "year_confidence": "high", "genre": "Rock Classique", "genre_confidence": "high",
  "is_cover": false, "original_artist": null, "cover_confidence": "high", "note": ""}}]}}"""


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _call(items, web):
    import anthropic

    # Tri rapide par Haiku (quelques centimes pour des centaines de morceaux), verification sur le web par
    # Sonnet seulement pour ce dont Haiku n'est pas sur.
    model = MODEL_WEB if web else MODEL_FAST
    client = make_client()
    lines = []
    for it in items:
        need = ", ".join(it["need"])
        extra = f" — deja connu : {it['known']}" if it.get("known") else ""
        lines.append(f'{it["id"]}. Artiste : "{it["artist"] or "?"}" | Titre : "{it["title"]}"{extra} | A trouver : {need}')
    user = ("Complete ces morceaux :\n" + "\n".join(lines) +
            ("\n\nUtilise la recherche web pour verifier ce dont tu n'es pas sur." if web else ""))
    kwargs = dict(
        model=model,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium" if web else "low"},
    )
    if web:
        kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": min(10, len(items))}]
    total = 0.0
    messages = kwargs["messages"]
    for _ in range(3):  # gestion de pause_turn
        resp = client.messages.create(**kwargs)
        total += _cost_usd(model, resp.usage)
        if resp.stop_reason == "pause_turn":
            messages = messages + [{"role": "assistant", "content": resp.content}]
            kwargs["messages"] = messages
            continue
        break
    db.add_spend(month(), total, f"{len(items)} morceaux{' + web' if web else ''}")
    if resp.stop_reason == "refusal":
        return {}
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _parse_json(text) or {}
    return {int(x["id"]): x for x in data.get("items", []) if isinstance(x, dict) and "id" in x}


def enrich(items, log=lambda m: None, progress=lambda done, total, label: None):
    """items: liste de dict(id, artist, title, need[...], known). -> {id: reponse}"""
    if not items or not (config["ai_enabled"] and has_key()):
        return {}
    import anthropic

    out = {}
    try:
        progress(0, len(items), "Premier tri")
        for i in range(0, len(items), 25):
            chunk = items[i:i + 25]
            if not _can_spend(0.01):
                log("Budget IA du mois atteint : le reste ira dans « À vérifier ».")
                break
            log(f"Claude analyse {len(chunk)} morceaux…")
            out.update(_call(chunk, web=False))
            progress(min(i + 25, len(items)), len(items), "Premier tri")
        unsure = []
        for it in items:
            r = out.get(it["id"])
            if r is None:
                continue
            # le web coute (1 centime la recherche + lecture des pages) : seulement quand on n'a vraiment rien
            weak = [n for n in it["need"] if r.get(f"{n}_confidence") != "high"
                    and not (n == "year" and (it.get("guess") or r.get("year")))      # une annee existe deja
                    and not (n == "genre" and r.get("genre") and r.get("genre_confidence") == "medium")
                    and (n != "cover" or (r.get("is_cover") and r.get("cover_confidence") == "low"))]
            if weak:
                unsure.append(it)
        if len(unsure) > WEB_MAX:
            log(f"{len(unsure)} morceaux douteux : vérification web limitée aux {WEB_MAX} premiers (le reste ira dans « À vérifier »).")
            unsure = unsure[:WEB_MAX]
        for i in range(0, len(unsure), 10):
            chunk = unsure[i:i + 10]
            if not _can_spend(0.15):
                break
            log(f"Claude vérifie {len(chunk)} morceaux sur le web…")
            progress(i, len(unsure), "Vérification sur le web")
            res = _call(chunk, web=True)
            for k, v in res.items():
                out[k] = v
            progress(min(i + 10, len(unsure)), len(unsure), "Vérification sur le web")
    except anthropic.AuthenticationError:
        log("Clé API Claude refusée : vérifie-la dans les Réglages.")
    except anthropic.APIConnectionError:
        log("Pas de connexion internet : Claude n'a pas pu être consulté.")
    except anthropic.APIStatusError as e:
        log(WORKSPACE_MSG if _is_workspace_error(e) else f"Claude indisponible ({e.status_code}).")
    return out


def test_key(k, workspace=""):
    import anthropic

    try:
        make_client(k, timeout=20.0, workspace=workspace).models.retrieve(MODEL_FAST)
        return True, "Clé valide ✓"
    except anthropic.AuthenticationError:
        return False, "Clé refusée : vérifie qu'elle est complète (sk-ant-…)."
    except anthropic.PermissionDeniedError:
        return False, "Cette clé n'a pas accès à Claude. Vérifie ses droits dans la console Anthropic."
    except anthropic.NotFoundError:
        return False, "Le modèle Claude demandé n'est pas disponible pour ce compte."
    except anthropic.BadRequestError as e:
        if _is_workspace_error(e):
            return False, WORKSPACE_MSG
        return False, f"Requête refusée : {getattr(e, 'message', e)}"
    except anthropic.APIConnectionError:
        return False, "Pas de connexion internet."
    except Exception as e:
        return False, f"Erreur : {e}"


def key_folders(items, folders, log=lambda m: None):
    """Artistes absents de la cle USB : Claude choisit leur dossier parmi ceux de la cle.

    items : [{"id", "artist", "titles", "genre"}] -> {id: {"folder", "confidence", "note"}}
    """
    if not items or not (config["ai_enabled"] and has_key()):
        return {}
    import anthropic

    system = ("Tu ranges des morceaux de musique sur une cle USB deja organisee en dossiers. Pour chaque artiste, "
              "choisis UN dossier, copie EXACTEMENT depuis cette liste :\n" + "\n".join(folders) +
              "\n\nRegles : les artistes francais ou francophones (France, Belgique, Quebec, Suisse romande, Afrique "
              "francophone, DOM-TOM) vont dans un dossier qui commence par « FRANCAIS\\ » ; les autres jamais. Punk : PUNK UK "
              "pour les groupes anglais, PUNK US pour les americains, PUNK (OTHERS) pour les autres pays. Le dossier "
              "depend de l'artiste, pas du titre. Donne une confiance high, medium ou low ; n'invente jamais.\n"
              'Reponds UNIQUEMENT en JSON : {"items": [{"id": 1, "folder": "PUNK UK", "confidence": "high", "note": ""}]}')
    out = {}
    try:
        for i in range(0, len(items), 40):
            chunk = items[i:i + 40]
            if not _can_spend(0.01):
                log("Budget IA du mois atteint : dossier choisi d'après le genre seulement.")
                break
            lines = [f'{it["id"]}. Artiste : "{it["artist"]}" | Titres : {", ".join(it["titles"][:3])}'
                     + (f' | Genre probable : {it["genre"]}' if it.get("genre") else "")
                     + (" | Titres en francais" if it.get("lang") == "fr" else "") for it in chunk]
            resp = make_client().messages.create(
                model=MODEL_FAST, max_tokens=4000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": "Range ces artistes :\n" + "\n".join(lines)}])
            db.add_spend(month(), _cost_usd(MODEL_FAST, resp.usage), f"{len(chunk)} artistes (dossiers de la clé)")
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            for x in (_parse_json(text) or {}).get("items", []):
                if isinstance(x, dict) and "id" in x and x.get("folder") in folders:
                    out[int(x["id"])] = x
    except anthropic.AuthenticationError:
        log("Clé API Claude refusée : vérifie-la dans les Réglages.")
    except anthropic.APIConnectionError:
        log("Pas de connexion internet : Claude n'a pas pu être consulté.")
    except anthropic.APIStatusError as e:
        log(WORKSPACE_MSG if _is_workspace_error(e) else f"Claude indisponible ({e.status_code}).")
    return out
