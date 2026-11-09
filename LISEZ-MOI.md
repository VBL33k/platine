# Platine

Logiciel Windows qui range automatiquement une bibliothèque musicale « en vrac » :
noms propres, années, albums, pochettes, doublons, reprises, genres, tags compatibles autoradio.

## Installer sur le PC de destination

1. Copier **`dist\Installer Platine.exe`** sur le PC (clé USB, mail…).
2. Double-cliquer dessus, puis **Installer**. L'icône **Platine** apparaît sur le bureau.
   - Installation dans `%LOCALAPPDATA%\Programs\Platine` (pas besoin d'être administrateur).
   - Désinstallation : menu Démarrer → *Désinstaller Platine*, ou *Paramètres → Applications*.
3. Si un message parle de **WebView2** : installer ce composant Microsoft gratuit
   (https://go.microsoft.com/fwlink/p/?LinkId=2124703). Il est déjà présent sur Windows 10/11 à jour.

## Premier lancement

1. **Accueil → Choisir…** : sélectionner le dossier où sont les musiques en vrac (ex. `H:\USB - VRAC`).
2. **Ranger maintenant**. Le premier passage lit toute la bibliothèque ; les fois suivantes,
   seuls les nouveaux morceaux sont traités.
3. **Réglages → Claude** : coller la clé API (voir plus bas). Sans clé, tout fonctionne quand même,
   les cas difficiles vont simplement dans « À vérifier ».

Ensuite il suffit de **déposer de nouvelles musiques dans le dossier** : Platine les range tout seul
(option « Ranger automatiquement », active par défaut, quand le logiciel est ouvert).

## Ce que fait un rangement

| Étape | Détail |
|---|---|
| Nom | `Artiste - Titre (Sous-titre) (Original Cover) (Version) (Année).mp3`, majuscules, apostrophes et caractères cassés réparés |
| Qualité | analyse des aigus (kHz) : repère les faux 320 kbps |
| Doublons | même artiste + même titre + même version : garde le meilleur, l'autre va dans `Doublons\` (jamais supprimé) |
| Reprises | même titre chez un autre artiste : Claude identifie l'original → `(Artiste Cover)` |
| Année / album / pochette | MusicBrainz + Deezer (gratuits), Claude en dernier recours |
| Genre | 1 803 artistes déjà connus + Claude pour les nouveaux (retenu ensuite) |
| Tags | ID3v2.3 (titre = nom du fichier, artiste, album, année, genre, pochette 500×500) |
| Copies | chaque morceau copié dans son dossier de genre, les reprises aussi dans `Cover\` |

**Annuler le dernier rangement** (écran Rangement) remet les noms et les fichiers comme avant.

## Clé Claude et plafond de 1 €/mois

1. Créer un compte sur https://console.anthropic.com , ajouter quelques euros de crédit.
2. *API Keys → Create Key*, copier la clé (`sk-ant-…`) dans **Réglages → Clé API**.
3. Le plafond de **1 €/mois** est réglé dans Platine : au-delà, Claude n'est plus appelé jusqu'au mois suivant.
   Par sécurité, fixer aussi une limite de dépense dans la console Anthropic (*Settings → Limits*).

La clé est stockée dans le coffre-fort Windows (Gestionnaire d'identification), pas en clair.
Platine regroupe les questions (jusqu'à 25 morceaux par appel) et n'utilise la recherche web que pour
les cas encore incertains : 1 €/mois couvre largement des ajouts réguliers.

## Fichiers de l'application

- Réglages, base, cache des pochettes, journal : `%APPDATA%\Platine\`
- Journal détaillé : `%APPDATA%\Platine\platine.log`

## Développement

```
.venv\Scripts\python Platine.pyw          # lancer depuis les sources
.venv\Scripts\python Platine.pyw --debug  # avec les outils de développement web
.venv\Scripts\python build.py             # fabriquer dist\Installer Platine.exe
```

Aperçu de l'interface dans un navigateur, avec des données d'exemple : `web/index.html?apercu`
(via un petit serveur local, ex. `python -m http.server -d web`).

Structure : `platine/` (moteur : `pipeline.py`, `naming.py`, `quality.py`, `tags.py`, `lookup.py`,
`ai.py`, `db.py`), `web/` (interface), `installer.py`, `build.py`.
