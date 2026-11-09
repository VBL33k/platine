/* Platine - interface */
const GENRE_COLORS = {
  "Rock Classique": "#e0a458", "Rock Alternatif & Indie": "#8fb8a6", "Grunge": "#9c8f6a", "Hard Rock": "#d9734e",
  "Metal": "#9aa1ab", "Punk": "#ef6f8f", "Hardcore & Fusion": "#c7735f", "Rap FR": "#7fa7e6", "Rap US": "#a990e0",
  "Reggae & Dub": "#7dc07a", "Ska": "#e9d36a", "Electro": "#5fd0d6", "Pop": "#f29fc4", "Annees 80": "#e58ae0",
  "Chanson Francaise": "#e8c39a", "Rock Francais": "#d6a07a", "Soul & Funk": "#f0a35a", "Disco": "#f6c87a",
  "Jazz": "#c9b28a", "Blues & Rock'n'Roll 50s": "#6fa6c9", "Musique Bresilienne & Latine": "#9fd28c",
  "Musique Du Monde": "#d7b06a", "Bandes Originales": "#b9a6c9", "A Trier": "#7c6f84",
};
/* noms de dossiers (sans accents) -> noms affiches */
const GENRE_NAMES = { "Annees 80": "Années 80", "Chanson Francaise": "Chanson française", "Rock Francais": "Rock français",
  "Musique Bresilienne & Latine": "Musique brésilienne & latine", "Musique Du Monde": "Musique du monde", "A Trier": "À trier" };
const gname = (g) => GENRE_NAMES[g] || g;
const gcolor = (g) => GENRE_COLORS[g] || "#7c6f84";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n) => Number(n || 0).toLocaleString("fr-FR");
const eur = (n) => Number(n || 0).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
const plural = (n, one, many) => `${fmt(n)} ${n > 1 ? many : one}`;
const stem = (f) => String(f || "").replace(/\.[^.]+$/, "");
let api, state = null, libOffset = 0, libGenre = "", feedCount = 0, runState = "idle";

/* ---------- navigation ---------- */
function show(view) {
  $$(".nav").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("on", v.id === view));
  if (view === "review") loadReviews();
  if (view === "library") { libOffset = 0; loadLibrary(); }
  $("main").scrollTop = 0;
}
$$(".nav").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$$("[data-go]").forEach((b) => b.addEventListener("click", () => show(b.dataset.go)));
$("#claudeCard").addEventListener("click", () => { show("settings"); setTimeout(() => $("#apiKey").scrollIntoView({ block: "center" }), 50); });

function toast(msg, bad = false) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("bad", bad); t.classList.add("on");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("on"), 2800);
}

/* ---------- fenetre de confirmation (remplace confirm()) ---------- */
function ask({ title, text, yes = "Oui", no = "Non, garder", danger = false }) {
  return new Promise((resolve) => {
    const m = $("#confirmModal");
    $("#confirmTitle").textContent = title; $("#confirmText").textContent = text;
    const y = $("#confirmYes"), n = $("#confirmNo");
    y.textContent = yes; n.textContent = no;
    y.className = "btn " + (danger ? "solid-danger" : "primary");
    const close = (v) => { m.hidden = true; y.onclick = n.onclick = m.onclick = null; document.removeEventListener("keydown", key); resolve(v); };
    const key = (e) => { if (e.key === "Escape") close(false); };
    y.onclick = () => close(true); n.onclick = () => close(false);
    m.onclick = (e) => { if (e.target === m) close(false); };
    document.addEventListener("keydown", key);
    m.hidden = false; n.focus();
  });
}

/* ---------- afficheur autoradio ---------- */
function lcd(text) {
  const el = $("#lcd");
  const t = (text || "PLATINE").toUpperCase();
  if (el.textContent === t) return;
  el.textContent = t;
  const long = t.length > 34;
  el.classList.toggle("scroll", long);
  el.parentElement.classList.toggle("long", long);
  el.style.setProperty("--marquee", Math.max(8, t.length * 0.32) + "s");
}

function art(el, url, title, genre) {
  if (url) { el.style.backgroundImage = `url("${url}")`; el.classList.remove("noart"); el.textContent = ""; el.style.backgroundColor = ""; }
  else { el.style.backgroundImage = ""; el.style.backgroundColor = gcolor(genre); el.classList.add("noart"); el.textContent = title || ""; }
}
/* vignette : la pochette, sinon une icone de base teintee par le genre */
function thumb(el, url, genre, kind) {
  el.classList.remove("ph", "broken", "space");
  if (url) { el.style.backgroundImage = `url("${url}")`; return; }
  el.style.backgroundImage = "";
  el.style.setProperty("--c", genre ? gcolor(genre) : "#7c6f84");
  el.classList.add("ph");
  if (kind) el.classList.add(kind);
}
function setAmbient(url) {
  const a = $("#ambient");
  if (url) { a.style.backgroundImage = `url("${url}")`; a.classList.add("on"); } else a.classList.remove("on");
}

/* ---------- etat general ---------- */
function setBusy(busy) {
  $("#vinyl").classList.toggle("spin", busy);
  $("#brandDisc").classList.toggle("spin", busy);
  $(".tonearm").classList.toggle("down", busy);
  $("#led").classList.toggle("on", busy);
  $("#runDot").hidden = !busy;
  $("#stopBtn").hidden = !busy;
  $("#fullBtn").disabled = busy || !state.folder_ok;
  ["#goBtn", "#goBtn2"].forEach((s) => {
    const b = $(s);
    b.disabled = busy || !state.folder_ok;
    b.innerHTML = busy ? `<span class="spinner"></span>Rangement en cours` : "Ranger maintenant";
  });
}

async function refresh() {
  state = await api.get_state();
  const c = state.config, busy = state.running;
  setBusy(busy);
  const folder = $("#folderBtn");
  folder.classList.toggle("empty", !c.music_dir);
  $("#folderPath").textContent = c.music_dir || "Choisir le dossier de la musique";
  $(".folder-act", folder).textContent = c.music_dir ? "Changer" : "Choisir";
  $("#setFolder").textContent = c.music_dir || "Aucun dossier choisi";
  const usb = $("#usbBtn");
  usb.classList.toggle("empty", !c.usb_dir);
  $("#usbPath").textContent = !c.usb_dir ? "Aucune : rangement dans le dossier lui-même"
    : state.usb_ok ? c.usb_dir : c.usb_dir + " (clé non branchée)";
  $(".folder-act", usb).textContent = c.usb_dir ? "Changer" : "Choisir";
  $("#setUsb").textContent = c.usb_dir ? c.usb_dir + (state.usb_ok ? "" : " (non branchée)") + ". Chaque morceau rangé y est copié dans son genre, VRAC, xxxRECENTS (2020 et après) et xxxNOUVEAUTES."
    : "Aucune : les morceaux sont rangés dans le dossier de la musique lui-même.";
  $("#clearUsb").hidden = !c.usb_dir;
  const copies = SORT_STAGES.find((s) => s.k === "copies");
  copies.name = c.usb_dir ? "Classement sur la clé" : "Copies par genre";
  copies.desc = c.usb_dir ? "Genre, VRAC, xxxRECENTS et xxxNOUVEAUTES" : "Rock, Punk, Rap FR, Cover…";
  $("#openBtn").hidden = !state.folder_ok;
  const badge = $("#badge"); badge.hidden = !state.review; badge.textContent = state.review;

  // accueil
  const title = $("#homeTitle"), lede = $("#homeLede"), sum = $("#summary");
  if (!state.folder_ok) {
    title.textContent = c.music_dir ? "Le dossier est introuvable." : "Ta musique, rangée toute seule.";
    lede.textContent = c.music_dir
      ? "Branche la clé USB ou le disque qui contient ta musique, ou choisis un autre dossier."
      : "Indique le dossier où sont tes musiques en vrac. Platine corrige les noms, complète les infos, retrouve les pochettes et classe chaque morceau par genre.";
    sum.innerHTML = "";
  } else if (busy) {
    title.textContent = "Je range ta musique…";
    lede.textContent = "Tu peux continuer à utiliser ton ordinateur. Rien n'est jamais supprimé : les doublons sont seulement mis de côté.";
    const p = state.progress;
    sum.innerHTML = p.total ? `${esc(p.step)} : <strong>${fmt(p.done)}</strong> sur ${fmt(p.total)}.` : esc(p.step || "Préparation…");
  } else {
    const waiting = state.pending;
    if (!state.imported) {
      title.textContent = "Prêt pour le premier rangement.";
      lede.textContent = waiting
        ? `Platine a trouvé ${plural(waiting, "morceau", "morceaux")} dans ton dossier. Rien ne bouge tant que tu n'as pas cliqué sur « Ranger maintenant ».`
        : "Le dossier est vide pour l'instant : dépose des musiques dedans puis lance le rangement.";
    } else if (waiting) {
      title.textContent = "Du nouveau à ranger.";
      lede.textContent = `${plural(waiting, "morceau attend", "morceaux attendent")} dans ton dossier. Clique sur « Ranger maintenant » quand tu veux.`;
    } else {
      title.textContent = "Tout est rangé.";
      lede.textContent = "Dépose de nouvelles musiques dans ton dossier puis clique sur « Ranger maintenant ». Platine ne range jamais rien tout seul.";
    }
    const parts = [];
    if (state.count) parts.push(`<strong>${fmt(state.count)}</strong> morceaux classés dans <strong>${state.genres.length}</strong> genres`);
    if (state.covers) parts.push(`<strong>${fmt(state.covers)}</strong> reprises repérées`);
    if (state.doublons) parts.push(`<strong>${fmt(state.doublons)}</strong> doublons mis de côté`);
    let s = parts.length ? parts.join(", ") + "." : "";
    if (state.review) s += ` <button class="btn text" onclick="show('review')">${plural(state.review, "point à vérifier", "points à vérifier")}</button>`;
    sum.innerHTML = s;
  }
  const last = state.recent[0];
  $("#discLabel").style.backgroundImage = last && last.thumb ? `url("${last.thumb}")` : "";
  if (!busy) { lcd(last ? stem(last.filename) : (state.folder_ok ? "PRÊT" : "PLATINE")); setAmbient(last && last.thumb); }
  $("#lcdSrc").textContent = busy ? "RUN" : "USB";

  // derniers arrives
  const shelf = $("#recent");
  shelf.innerHTML = "";
  shelf.classList.toggle("is-empty", !state.recent.length);
  if (state.recent.length) state.recent.forEach((r) => shelf.appendChild(sleeve(r)));
  else shelf.innerHTML = `<div class="crate-empty"><div class="stack"><i></i><i></i><i></i></div><div><strong>Le bac est encore vide</strong>Les morceaux apparaissent ici dès qu'ils sont rangés, avec leur pochette.</div></div>`;

  // budget Claude
  const b = state.budget, pct = b.limit_eur ? Math.min(100, (b.spent_eur / b.limit_eur) * 100) : 0;
  const cls = pct >= 100 ? "full" : pct >= 75 ? "warn" : "";
  const card = $("#claudeCard");
  card.classList.toggle("off", !state.ai_ready);
  $("#ccAmount").textContent = state.ai_ready ? `${eur(b.spent_eur)} / ${eur(b.limit_eur)}` : (c.api_key_set ? "En pause" : "Pas de clé");
  $("#ccFill").style.width = (state.ai_ready ? pct : 0) + "%"; $("#ccFill").className = cls;
  $("#ccHint").textContent = !state.ai_ready ? (c.api_key_set ? "Activé dans les Réglages." : "Ajoute une clé dans les Réglages.")
    : pct >= 100 ? "Plafond atteint : Claude reprend le mois prochain." : `Il reste ${eur(b.left_eur)} ce mois-ci.`;
  $("#spendText").textContent = `${eur(b.spent_eur)} utilisés sur ${eur(b.limit_eur)}`;
  $("#spendFill").style.width = pct + "%"; $("#spendFill").className = cls;
  const list = $("#spendList");
  const rate = b.spent_eur && state.spend ? null : null;
  list.innerHTML = (state.spend || []).length
    ? state.spend.map((x) => `<li><span>${new Date(x.ts * 1000).toLocaleString("fr-FR", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}, ${esc(x.note || "appel à Claude")}</span><b>${eur(x.usd * (c.eur_per_usd || 0.93))}</b></li>`).join("")
    : `<li class="none">Aucun appel à Claude ce mois-ci.</li>`;

  // reglages
  $$(".toggle").forEach((t) => t.setAttribute("aria-checked", String(!!c[t.dataset.k])));
  $("#budget").textContent = eur(c.ai_budget_eur);
  $("#keyState").textContent = c.api_key_set ? `Clé enregistrée (${c.api_key_hint}), gardée dans le coffre-fort de Windows.` : "Aucune clé : Claude n'est pas utilisé.";
  if (c.ai_workspace) { $("#wsRow").hidden = false; if (document.activeElement !== $("#apiWs")) $("#apiWs").value = c.ai_workspace; }
  drawUpdate();
  $("#verTitle").textContent = `Platine ${state.version}`;
  if (!state.update_enabled) $("#verText").textContent = "Les mises à jour automatiques ne sont pas encore configurées.";
  else if (state.update) $("#verText").textContent = `La version ${state.update.version} est disponible.`;
  $("#checkUpd").hidden = !state.update_enabled;
  $("#about").textContent = `Platine ${state.version}. ${plural(state.no_art, "morceau", "morceaux")} encore sans pochette. Rien n'est jamais supprimé : les doublons vont dans le dossier « Doublons ».`;
}

function sleeve(r) {
  const b = document.createElement("button");
  b.className = "sleeve"; b.type = "button";
  b.title = stem(r.filename);
  b.innerHTML = `<div class="art"></div><span class="t">${esc(r.title)}</span><span class="a">${esc(r.artist)}${r.year ? ", " + r.year : ""}</span>`;
  art($(".art", b), r.thumb, r.title, r.genre);
  if (r.cover_of) $(".art", b).insertAdjacentHTML("beforeend", `<span class="tagc">Reprise</span>`);
  b.addEventListener("click", () => openEdit(r));
  return b;
}

/* ---------- evenements du moteur ---------- */
async function pump() {
  try {
    const evs = await api.get_events();
    for (const e of evs) handle(e);
  } catch (_) { /* fenetre en cours de fermeture */ }
}
function feedPush(li) {
  const feed = $("#feed");
  feed.prepend(li); feedCount++;
  while (feed.children.length > 400) feed.lastChild.remove();
  $("#feedEmpty").hidden = true;
}
/* ---------- deroule du rangement ---------- */
const SORT_STAGES = [
  { k: "read", name: "Lecture des fichiers", desc: "Qualité audio, noms et tags de chaque morceau", w: 34 },
  { k: "dupes", name: "Doublons et reprises", desc: "Compare avec toute ta bibliothèque", w: 3 },
  { k: "online", name: "Infos en ligne", desc: "Album, pochette et année (Deezer, MusicBrainz)", w: 30 },
  { k: "claude", name: "Claude, cas difficiles", desc: "Seulement pour ce qui n'a pas été trouvé", w: 8 },
  { k: "apply", name: "Renommage et tags", desc: "Nouveau nom, infos et pochette dans le fichier", w: 17 },
  { k: "copies", name: "Copies par genre", desc: "Rock, Punk, Rap FR, Cover…", w: 8 },
  { k: "keytags", name: "Titres sur la clé", desc: "Le titre de chaque morceau de la clé est vérifié", w: 6 },
];
const UNDO_STAGES = [
  { k: "undo_copy", name: "Copies par genre retirées", desc: "Les copies faites dans Rock, Punk, Cover…", w: 3 },
  { k: "undo_dup", name: "Doublons remis en place", desc: "Retour du dossier « Doublons » vers le dossier principal", w: 3 },
  { k: "undo_rename", name: "Noms d'origine remis", desc: "Chaque fichier reprend le nom qu'il avait avant", w: 4 },
];
let STAGES = SORT_STAGES, run = null, runTick = null;
const CHECK = `<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>`;
const SKIP = `<svg viewBox="0 0 24 24"><path d="M6 12h12"/></svg>`;
function dur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), h = Math.floor(m / 60);
  if (h) return `${h} h ${String(m % 60).padStart(2, "0")}`;
  return `${m} min${sec % 60 && m < 10 ? " " + String(sec % 60).padStart(2, "0") : ""}`;
}
function runReset(ts, stages = SORT_STAGES) {
  STAGES = stages;
  run = { t0: ts, end: null, result: null, st: {} };
  STAGES.forEach((s) => { run.st[s.k] = { state: "todo", done: 0, total: 0, t0: null, end: null, note: "", label: "", current: "" }; });
  const ol = $("#stages"); ol.innerHTML = "";
  STAGES.forEach((s, i) => {
    const li = document.createElement("li"); li.className = "stage"; li.dataset.k = s.k;
    li.innerHTML = `<span class="dot">${i + 1}</span><div style="min-width:0"><div class="stage-name">${s.name}</div><div class="stage-sub">${s.desc}</div></div>
      <div class="stage-side"><div class="stage-nums"><b></b><span></span></div><div class="bar sm"><i></i></div></div>`;
    ol.appendChild(li);
  });
  $("#run").hidden = false; $("#run").classList.remove("done", "stopped");
  clearInterval(runTick); runTick = setInterval(drawRun, 1000);
}
function stageMove(k, ts) {
  // une etape qui demarre cloture les precedentes restees ouvertes
  const idx = STAGES.findIndex((s) => s.k === k);
  STAGES.slice(0, idx).forEach((s) => {
    const x = run.st[s.k];
    if (x.state === "run") { x.state = "done"; x.end = x.end || ts; }
    else if (x.state === "todo") x.state = "skip";
  });
  const x = run.st[k];
  if (x.state === "todo") { x.state = "run"; x.t0 = ts; }
}
function drawRun() {
  if (!run) return;
  const now = run.end || Date.now() / 1000;
  let sumW = 0, got = 0;
  STAGES.forEach((s, n) => {
    const x = run.st[s.k], li = $(`.stage[data-k="${s.k}"]`);
    sumW += s.w;
    const frac = x.state === "done" || x.state === "skip" ? 1 : x.total ? x.done / x.total : 0;
    got += s.w * frac;
    li.className = "stage" + (x.state === "todo" ? "" : " is-" + x.state);
    $(".dot", li).innerHTML = x.state === "done" ? CHECK : x.state === "skip" ? SKIP : n + 1;
    let sub = s.desc;
    if (x.state === "run") sub = (x.label ? x.label + " : " : "") + (x.current || s.desc);
    else if (x.note) sub = x.note;
    $(".stage-sub", li).textContent = sub;
    $(".stage-side .bar i", li).style.width = (x.state === "skip" ? 0 : frac * 100) + "%";
    let left = "", right = "";
    if (x.state === "run" && x.total) {
      left = `${fmt(x.done)} / ${fmt(x.total)}`;
      const el = now - x.t0;
      right = x.done >= 2 && el > 3 ? "encore ~" + dur((el / x.done) * (x.total - x.done)) : "calcul…";
    } else if (x.state === "run") left = "En cours";
    else if (x.state === "done") { left = x.total ? fmt(x.total) : "Fait"; right = x.t0 ? dur((x.end || now) - x.t0) : ""; }
    else if (x.state === "skip") left = "Passée";
    else left = "En attente";
    $(".stage-nums b", li).textContent = left; $(".stage-nums span", li).textContent = right;
  });
  const p = run.result === "ok" ? 1 : got / sumW, el = now - run.t0;
  $("#runPct").textContent = Math.floor(p * 100) + " %";
  $("#runFill").style.width = p * 100 + "%";
  let meta = `Commencé il y a ${dur(el)}`;
  if (run.result === "ok") meta = `Terminé en ${dur(el)}`;
  else if (run.result) meta = `Arrêté au bout de ${dur(el)}`;
  else if (p > 0.04 && el > 8) meta += `, il reste environ ${dur((el * (1 - p)) / p)}`;
  $("#runMeta").textContent = meta;
}

function handle(e) {
  if (e.kind === "start") {
    $("#feed").innerHTML = ""; feedCount = 0; runState = "run";
    runReset(e.ts, e.undo ? UNDO_STAGES : SORT_STAGES);
    run.undo = !!e.undo;
    if (e.undo) {
      $("#runTitle").textContent = e.total ? `Annulation de ${plural(e.total, "opération", "opérations")}` : "Rien à annuler";
      $("#workSub").textContent = "Le dossier est remis exactement comme avant le dernier rangement.";
      $("#feedTitle").hidden = true;
    } else {
      $("#runTitle").textContent = e.total ? `Rangement de ${plural(e.total, "morceau", "morceaux")}` : "Recherche des nouveautés";
      $("#workSub").textContent = "Chaque étape s'allume à son tour. Rien n'est jamais supprimé.";
      $("#feedTitle").hidden = false;
    }
    if (state) { state.running = true; setBusy(true); }
    drawRun();
  }
  if (e.kind === "update_available") { refresh(); }
  if (e.kind === "update") {
    const bar = $("#ubBar"), btn = $("#ubBtn");
    $("#updateBar").hidden = false; btn.disabled = true; $("#ubClose").hidden = true; bar.hidden = false;
    if (e.step === "download") {
      const pct = e.total ? Math.floor((e.done / e.total) * 100) : 0;
      $("#ubTitle").textContent = `Téléchargement de la version ${state.update ? state.update.version : ""}… ${pct} %`;
      $("#ubSub").textContent = "Platine se fermera et se rouvrira tout seul.";
      $("#ubFill").style.width = pct + "%"; btn.innerHTML = `<span class="spinner"></span>Téléchargement`;
    } else if (e.step === "install") {
      $("#ubTitle").textContent = "Installation… Platine va se fermer quelques secondes puis se rouvrir.";
      $("#ubFill").style.width = "100%"; btn.innerHTML = `<span class="spinner"></span>Installation`;
    } else if (e.step === "error") {
      $("#ubTitle").textContent = "La mise à jour n'a pas pu se faire";
      $("#ubSub").textContent = (e.msg || "") + " Réessaie plus tard, ta version actuelle continue de marcher.";
      bar.hidden = true; btn.disabled = false; btn.textContent = "Réessayer"; $("#ubClose").hidden = false;
    }
  }
  if (e.kind === "new_files") {
    toast(`${plural(e.count, "nouvelle musique", "nouvelles musiques")} dans le dossier : clique sur « Ranger maintenant » quand tu veux`);
    refresh();
  }
  if (e.kind === "progress" && e.stage) {
    if (!run) runReset(e.ts);
    stageMove(e.stage, e.ts);
    const x = run.st[e.stage];
    x.done = e.done; x.total = e.total; x.label = e.label || ""; x.current = e.current ? stem(e.current) : "";
    if (x.state !== "run") { x.state = "run"; x.t0 = x.t0 || e.ts; }
    clearTimeout(handle._d); handle._d = setTimeout(drawRun, 60);
  }
  if (e.kind === "stage" && run) {
    stageMove(e.stage, e.ts);
    const x = run.st[e.stage];
    x.state = e.state; x.note = e.note || ""; x.end = e.ts;
    if (e.state === "done" && x.total) x.done = x.total;
    drawRun();
  }
  if (e.kind === "track" && (e.stage === "ok" || e.stage === "doublon")) {
    const li = document.createElement("li");
    const chips = [];
    const dup = e.stage === "doublon";
    if (dup) { li.className = "dup"; chips.push(`<span class="chip dup">Doublon mis de côté</span>`); }
    if (e.year) chips.push(`<span class="chip year">${e.year}</span>`);
    if (e.genre) chips.push(`<span class="chip"><i style="background:${gcolor(e.genre)}"></i>${esc(gname(e.genre))}</span>`);
    if (e.cover) chips.push(`<span class="chip">Reprise de ${esc(e.cover)}</span>`);
    if (e.label && /Faible|Faux/.test(e.label)) chips.push(`<span class="chip bad">${esc(e.label)}</span>`);
    const after = stem(e.file).replace(/^Doublons[\\/]/, "");
    const before = e.before && e.before !== e.file ? stem(e.before) : "";
    li.innerHTML = `<div class="thumb"></div><div class="names"><div class="after">${esc(after)}</div>${before ? `<div class="before">${esc(before)}</div>` : ""}</div><div class="chips">${chips.join("")}</div>`;
    thumb($(".thumb", li), e.thumb, e.genre);
    feedPush(li);
    if (!dup) {
      lcd(after);
      if (e.thumb) { $("#discLabel").style.backgroundImage = `url("${e.thumb}")`; setAmbient(e.thumb); }
    }
  }
  if (e.kind === "done") {
    runState = "idle";
    if (run) {
      run.end = e.ts; run.result = e.ok ? "ok" : e.error ? "error" : "stopped";
      if (e.ok) STAGES.forEach((s) => { const x = run.st[s.k]; if (x.state === "run") { x.state = "done"; x.end = e.ts; } else if (x.state === "todo") x.state = "skip"; });
      $("#run").classList.add(e.ok ? "done" : "stopped");
      if (run.undo) $("#runTitle").textContent = e.ok ? (e.count ? `Annulation terminée : ${plural(e.count, "opération défaite", "opérations défaites")}` : "Rien à annuler") : "L'annulation a rencontré une erreur";
      else $("#runTitle").textContent = e.ok ? (e.count ? plural(e.count, "morceau traité", "morceaux traités") : "Tout était déjà rangé")
        : e.error ? "Interrompu par une erreur : relance « Ranger maintenant » pour finir" : "Rangement arrêté";
      clearInterval(runTick); drawRun();
    }
    if (e.undo) toast(e.ok ? "Dernier rangement annulé" : "L'annulation a rencontré une erreur", !e.ok);
    else if (e.ok) toast(e.count ? plural(e.count, "morceau traité", "morceaux traités") : "Tout était déjà rangé");
    else toast(e.error ? "Le rangement a rencontré une erreur" : "Rangement arrêté", !!e.error);
    refresh();
  }
}

/* ---------- a verifier ---------- */
function genrePicker(box, current, suggested, onPick) {
  box.innerHTML = "";
  state.genre_list.forEach((g) => {
    const b = document.createElement("button");
    b.type = "button"; b.style.setProperty("--c", gcolor(g));
    b.className = (g === current ? "sel " : "") + (g === suggested ? "sug" : "");
    b.innerHTML = `<i style="background:${gcolor(g)}"></i>${esc(gname(g))}`;
    b.addEventListener("click", () => { $$("button", box).forEach((x) => x.classList.remove("sel")); b.classList.add("sel"); box.dataset.value = g; onPick && onPick(g); });
    box.appendChild(b);
  });
  box.dataset.value = current || "";
}

async function loadReviews() {
  if (!state) await refresh();
  const list = await api.get_reviews();
  const box = $("#questions");
  if (!list.length) {
    box.innerHTML = `<div class="done-state"><div class="check"><svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg></div><strong>Rien à vérifier</strong>Platine a tout rangé sans hésiter.</div>`;
    return;
  }
  box.innerHTML = "";
  for (const r of list) {
    const d = document.createElement("article"); d.className = "q";
    let opts = "";
    if (r.kind === "year") {
      opts = r.options.map((y, i) => `<button class="choice ${i === 0 ? "main" : ""}" data-v="${y}">${y}</button>`).join("") +
        `<input class="grow" inputmode="numeric" maxlength="4" placeholder="Autre année" data-in style="max-width:140px"><button class="btn ghost" data-input>Valider</button>`;
    } else if (r.kind === "genre") {
      opts = `<div class="genre-pick" data-genres></div>`;
    } else if (r.kind === "cover") {
      opts = `<button class="choice main" data-v="Oui">Oui, reprise de ${esc(r.suggestion)}</button><button class="choice" data-v="Non">Non, c'est l'original</button>` +
        `<input class="grow" placeholder="Reprise d'un autre artiste…" data-in><button class="btn ghost" data-input>Valider</button>`;
    } else if (r.kind === "name") {
      const sg = r.suggestion && typeof r.suggestion === "object" ? r.suggestion : {};
      opts = `<input class="grow" placeholder="Artiste" data-a value="${esc(sg.artist || "")}"><input class="grow" placeholder="Titre" data-t value="${esc(sg.title || "")}"><button class="btn primary" data-name>Valider</button>`;
    } else if (r.kind === "broken") {
      opts = `<button class="btn ghost" data-open>Ouvrir le dossier</button>`;
    }
    opts += `<button class="btn text skip" data-skip>${r.kind === "broken" ? "C'est noté" : "Passer"}</button>`;
    d.innerHTML = `<div class="thumb"></div><div><h3>${esc(r.question)}</h3><p>${esc(r.detail || "")}</p><div class="opts">${opts}</div></div>`;
    thumb($(".thumb", d), r.thumb, null, r.kind === "broken" ? "broken" : r.kind === "space" ? "space" : "");
    let answered = false;
    const done = (v) => {
      if (answered) return;                   // un seul clic compte
      answered = true;
      d.style.pointerEvents = "none";
      d.style.height = d.offsetHeight + "px";
      requestAnimationFrame(() => d.classList.add("leaving"));
      setTimeout(() => { d.remove(); if (!$("#questions").children.length) loadReviews(); }, 320);
      const badge = $("#badge"), n = Math.max(0, (Number(badge.textContent) || 1) - 1);
      badge.textContent = n; badge.hidden = !n;
      toast(v === null ? "Question passée" : "C'est noté");
      api.resolve_review(r.id, v).then((ok) => { if (!ok) toast("Impossible d'appliquer cette réponse", true); refresh(); })
        .catch(() => toast("Impossible d'appliquer cette réponse", true));
    };
    $$("[data-v]", d).forEach((b) => b.addEventListener("click", () => done(b.dataset.v)));
    $$("[data-input]", d).forEach((b) => b.addEventListener("click", () => { const v = $("[data-in]", d).value.trim(); if (v) done(v); }));
    $$("[data-in]", d).forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter" && i.value.trim()) done(i.value.trim()); }));
    const gp = $("[data-genres]", d);
    if (gp) genrePicker(gp, null, r.suggestion, (g) => done(g));
    $$("[data-name]", d).forEach((b) => b.addEventListener("click", () => {
      const a = $("[data-a]", d).value.trim(), t = $("[data-t]", d).value.trim(); if (a && t) done({ artist: a, title: t });
    }));
    $$("[data-open]", d).forEach((b) => b.addEventListener("click", () => api.open_folder("")));
    $("[data-skip]", d).addEventListener("click", () => done(null));
    box.appendChild(d);
  }
}

/* ---------- bibliotheque ---------- */
function drawBins() {
  const bins = $("#bins");
  const all = [{ genre: "", n: state.lib_total }].concat(state.lib_genres);
  bins.innerHTML = "";
  all.forEach((g) => {
    const b = document.createElement("button");
    b.className = "bin" + (g.genre === libGenre ? " on" : "");
    b.innerHTML = `${g.genre ? `<i style="background:${gcolor(g.genre)}"></i>` : ""}${esc(g.genre ? gname(g.genre) : "Tout")} <small>${fmt(g.n)}</small>`;
    b.addEventListener("click", () => { libGenre = g.genre; libOffset = 0; loadLibrary(); });
    bins.appendChild(b);
  });
}
let libMode = "albums";
function albumSleeve(a) {
  const b = document.createElement("button");
  b.className = "sleeve album"; b.type = "button";
  const name = a.k ? a.album : "Sans album";
  const who = !a.k ? "Morceaux isolés" : a.n_artists > 1 ? "Artistes variés" : a.artist;
  const years = a.year ? (a.year_max && a.year_max !== a.year ? `${a.year}–${a.year_max}` : a.year) : "";
  b.title = name;
  b.innerHTML = `<div class="art"></div><span class="t">${esc(name)}</span><span class="a">${esc(who)}${years ? ", " + years : ""}</span><span class="n">${plural(a.n, "morceau", "morceaux")}</span>`;
  art($(".art", b), a.thumb, name, a.genre);
  b.addEventListener("click", () => openAlbum(a));
  return b;
}
async function openAlbum(a) {
  const m = $("#albumModal");
  $("#albumTitle").textContent = a.k ? a.album : "Sans album";
  thumb($("#albumCover"), a.thumb, a.genre);
  $("#albumSub").textContent = (!a.k ? "Morceaux dont l'album est inconnu" : a.n_artists > 1 ? "Artistes variés" : a.artist) + ", " + plural(a.n, "morceau", "morceaux");
  const list = $("#albumTracks"); list.innerHTML = "";
  m.hidden = false;
  const rows = await api.get_album(a.k, libGenre);
  // en-tete calcule sur les morceaux reellement affiches
  const artists = [...new Set(rows.map((r) => r.artist))];
  $("#albumSub").textContent = (!a.k ? "Morceaux dont l'album est inconnu" : artists.length > 1 ? "Artistes variés" : artists[0] || "") + ", " + plural(rows.length, "morceau", "morceaux");
  const withArt = a.k ? rows.find((r) => r.thumb) : null;
  thumb($("#albumCover"), withArt ? withArt.thumb : null, rows[0] && rows[0].genre);
  rows.forEach((r) => {
    const li = document.createElement("li");
    li.innerHTML = `<div class="thumb"></div><div style="min-width:0"><div class="tt">${esc(r.title)}${r.cover_of ? ` <span class="chip">Reprise</span>` : ""}</div><div class="ta">${esc(r.artist)}</div></div><span class="ty">${r.year || ""}</span>`;
    thumb($(".thumb", li), r.thumb, r.genre);
    li.addEventListener("click", () => { m.hidden = true; openEdit(r); });
    list.appendChild(li);
  });
}
$$("#albumModal [data-close]").forEach((b) => b.addEventListener("click", () => { $("#albumModal").hidden = true; }));
$("#albumModal").addEventListener("click", (e) => { if (e.target.id === "albumModal") e.target.hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#albumModal").hidden = true; });
$$(".seg button").forEach((b) => b.addEventListener("click", () => {
  libMode = b.dataset.mode; $$(".seg button").forEach((x) => x.classList.toggle("on", x === b));
  libOffset = 0; loadLibrary();
}));

async function loadLibrary(append = false) {
  if (!state) await refresh();
  drawBins();
  const q = $("#search").value.trim();
  const albums = libMode === "albums";
  const res = albums ? await api.get_albums(q, libGenre, libOffset, 60) : await api.get_library(q, libGenre, libOffset, 60);
  const box = $("#sleeves");
  if (!append) box.innerHTML = "";
  if (!res.total) box.innerHTML = `<p class="none">${q ? `Aucun morceau ne correspond à « ${esc(q)} ».` : "La bibliothèque est vide pour l'instant : lance un rangement depuis l'accueil."}</p>`;
  res.rows.forEach((r) => box.appendChild(albums ? albumSleeve(r) : sleeve(r)));
  libOffset += res.rows.length;
  $("#moreBtn").hidden = libOffset >= res.total;
  const where = libGenre ? " en " + gname(libGenre) : "";
  $("#libSub").textContent = res.total ? (albums ? `${plural(res.total, "album", "albums")}${where}, toutes clés confondues. Clique sur un album pour voir ses morceaux.`
    : `${plural(res.total, "morceau", "morceaux")}${where}, toutes clés confondues. Clique sur une pochette pour corriger un morceau.`) : "";
}
let searchT;
$("#search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(() => { libOffset = 0; loadLibrary(); }, 250); });
$("#moreBtn").addEventListener("click", () => loadLibrary(true));

/* ---------- edition ---------- */
let editing = null;
function openEdit(r) {
  if (r.here === false) { toast("Ce morceau est sur une autre clé : choisis son dossier dans les Réglages pour le modifier"); return; }
  editing = r;
  $("#editName").textContent = stem(r.filename);
  $("#fArtist").value = r.artist || ""; $("#fTitle").value = r.title || ""; $("#fYear").value = r.year || ""; $("#fCover").value = r.cover_of || "";
  genrePicker($("#fGenre"), r.genre);
  thumb($("#editCover"), r.thumb, r.genre);
  $("#editModal").hidden = false; $("#fArtist").focus();
}
function closeEdit() { $("#editModal").hidden = true; editing = null; }
$$("#editModal [data-close]").forEach((b) => b.addEventListener("click", closeEdit));
$("#editModal").addEventListener("click", (e) => { if (e.target.id === "editModal") closeEdit(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#editModal").hidden) closeEdit(); });
$("#editSave").addEventListener("click", async () => {
  if (!editing) return;
  const y = $("#fYear").value.trim();
  if (y && !/^(19|20)\d\d$/.test(y)) { toast("L'année doit avoir 4 chiffres, par exemple 1985", true); $("#fYear").focus(); return; }
  const res = await api.edit_track(editing.filename, { artist: $("#fArtist").value.trim(), title: $("#fTitle").value.trim(), year: y, genre: $("#fGenre").dataset.value, cover: $("#fCover").value.trim() });
  closeEdit();
  toast(res ? "Morceau corrigé" : "Impossible de modifier ce morceau", !res);
  refresh(); if ($("#library").classList.contains("on")) { libOffset = 0; loadLibrary(); }
});

/* ---------- actions ---------- */
async function pick() { const p = await api.pick_folder(); if (p) { toast("Dossier enregistré"); await refresh(); } }
$("#folderBtn").addEventListener("click", pick);
async function pickUsb() {
  const r = await api.pick_usb();
  if (!r) return;
  if (r.error) { toast(r.error, true); return; }
  toast("Clé USB enregistrée"); await refresh();
}
$("#usbBtn").addEventListener("click", pickUsb);
$("#pickUsb2").addEventListener("click", pickUsb);
$("#clearUsb").addEventListener("click", async () => {
  const yes = await ask({ title: "Retirer la clé USB ?", text: "Platine rangera de nouveau dans le dossier de la musique lui-même. Rien n'est effacé sur la clé.", yes: "Retirer", no: "Annuler" });
  if (yes) { await api.clear_usb(); toast("Clé USB retirée"); refresh(); }
});
$("#pickFolder2").addEventListener("click", pick);
async function go() {
  const ok = await api.start(false);
  if (ok) { show("work"); refresh(); } else toast("Un rangement est déjà en cours");
}
$("#goBtn").addEventListener("click", go);
$("#goBtn2").addEventListener("click", go);
$("#stopBtn").addEventListener("click", async () => {
  const yes = await ask({ title: "Arrêter le rangement ?", text: "Ce qui est déjà fait reste fait. Tu pourras relancer plus tard pour finir.", yes: "Arrêter", no: "Continuer", danger: true });
  if (!yes) return;
  await api.stop(); $("#runTitle").textContent = "Arrêt en cours…"; $("#stopBtn").disabled = true; setTimeout(() => { $("#stopBtn").disabled = false; }, 4000);
});
$("#openBtn").addEventListener("click", () => api.open_folder(""));
$("#fullBtn").addEventListener("click", async () => {
  if (state && state.running) { toast("Un rangement est déjà en cours"); return; }
  const yes = await ask({ title: "Tout revérifier ?", text: "Platine repasse sur tous les morceaux du dossier, même ceux déjà rangés : noms, doublons, infos et copies par genre. Utile si des fichiers ont été modifiés à la main. Claude peut être appelé pour les cas difficiles (quelques centimes).", yes: "Tout revérifier", no: "Annuler" });
  if (!yes) return;
  const ok = await api.start(true);
  if (ok) { show("work"); refresh(); }
});
$("#undoBtn").addEventListener("click", async () => {
  if (state && state.running) { toast("Attends la fin du rangement en cours"); return; }
  const info = await api.last_run_info();
  if (!info) { toast("Rien à annuler dans ce dossier"); return; }
  const when = new Date(info.run * 1000).toLocaleString("fr-FR", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const what = [];
  if (info.rename) what.push(plural(info.rename, "nom remis comme avant", "noms remis comme avant"));
  if (info.doublon) what.push(plural(info.doublon, "doublon remis dans le dossier", "doublons remis dans le dossier"));
  if (info.copy) what.push(plural(info.copy, "copie par genre retirée", "copies par genre retirées"));
  const yes = await ask({ title: "Annuler le rangement du " + when + " ?", text: (what.join(", ") || "Aucune opération") + ". Les infos ajoutées dans les fichiers (tags, pochettes) restent.", yes: "Oui, annuler", no: "Non, garder" });
  if (!yes) return;
  const ok = await api.undo_last();
  if (ok) { show("work"); refresh(); } else toast("Un rangement est en cours");
});
$$(".toggle").forEach((t) => t.addEventListener("click", async () => {
  const v = t.getAttribute("aria-checked") !== "true";
  t.setAttribute("aria-checked", String(v));
  await api.save_settings({ [t.dataset.k]: v }); toast(v ? "Activé" : "Désactivé"); refresh();
}));
let budgetT;
$$(".stepper button").forEach((b) => b.addEventListener("click", () => {
  const cur = state.config.ai_budget_eur || 0;
  const v = Math.max(0, Math.min(20, Math.round((cur + Number(b.dataset.step)) * 2) / 2));
  state.config.ai_budget_eur = v; $("#budget").textContent = eur(v);
  clearTimeout(budgetT); budgetT = setTimeout(async () => { await api.save_settings({ ai_budget_eur: v }); toast(`Plafond : ${eur(v)} par mois`); refresh(); }, 500);
}));
$("#resetBtn").addEventListener("click", async () => {
  if (state && state.running) { toast("Attends la fin du rangement en cours"); return; }
  const yes = await ask({ title: "Remettre Platine à zéro ?", text: "La bibliothèque, les réglages, l'historique des rangements et les questions seront effacés : ce sera comme une installation neuve. Tes musiques et ta clé API ne sont pas touchées. Platine va redémarrer.", yes: "Tout remettre à zéro", no: "Annuler", danger: true });
  if (!yes) return;
  toast("Remise à zéro, Platine redémarre…");
  await api.reset_all();
});
$("#eyeBtn").addEventListener("click", () => { const i = $("#apiKey"); i.type = i.type === "password" ? "text" : "password"; });
async function saveKey() {
  const k = $("#apiKey").value, ws = $("#apiWs").value, btn = $("#saveKey"), err = $("#keyErr");
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>Vérification`; err.hidden = true;
  let r;
  try { r = await api.set_api_key(k, ws); } catch (e) { r = { ok: false, msg: "Erreur inattendue : " + e }; }
  btn.disabled = false; btn.textContent = "Enregistrer";
  err.textContent = r.msg; err.classList.toggle("good", !!r.ok); err.hidden = false;
  if (r.ok) $("#apiKey").value = "";
  else if (r.workspace) { $("#wsRow").hidden = false; $("#apiWs").focus(); }
  refresh();
}
$("#saveKey").addEventListener("click", saveKey);
$("#apiKey").addEventListener("keydown", (e) => { if (e.key === "Enter") saveKey(); });

/* ---------- demarrage ---------- */
async function boot() {
  api = window.pywebview ? window.pywebview.api : window.MockApi;
  await refresh();
  setInterval(pump, 700);
  setInterval(() => { if (state && (state.running || document.hasFocus())) refresh(); }, 4000);
}
if (window.pywebview) boot(); else window.addEventListener("pywebviewready", boot);
if (location.search.includes("apercu")) {
  const s = document.createElement("script"); s.src = "mock.js"; s.onload = () => { if (!api) boot(); }; document.head.appendChild(s);
}

/* ---------- mises a jour ---------- */
let updLater = null;
function drawUpdate() {
  const u = state.update, bar = $("#updateBar");
  if (state.updating) return;                                 // l'affichage suit la progression
  bar.hidden = !u || updLater === u.version;
  if (!u) return;
  $("#ubTitle").textContent = `Platine ${u.version} est disponible (tu as la ${state.version})`;
  $("#ubSub").textContent = (u.notes ? u.notes.split("\n")[0] + " " : "") + "Tes musiques, ta bibliothèque et tes réglages sont conservés.";
  $("#ubBtn").disabled = false; $("#ubBtn").textContent = "Mettre à jour"; $("#ubBar").hidden = true; $("#ubClose").hidden = false;
}
$("#ubBtn").addEventListener("click", async () => {
  const r = await api.start_update();
  if (r === "busy") toast("Attends la fin du rangement en cours, puis relance la mise à jour");
});
$("#ubClose").addEventListener("click", () => { updLater = state.update && state.update.version; $("#updateBar").hidden = true; });
$("#checkUpd").addEventListener("click", async () => {
  const b = $("#checkUpd"); b.disabled = true; b.innerHTML = `<span class="spinner"></span>Recherche`;
  const u = await api.check_update();
  b.disabled = false; b.textContent = "Rechercher une mise à jour";
  updLater = null;
  if (u) { toast(`La version ${u.version} est disponible`); await refresh(); $("main").scrollTop = 0; }
  else toast("Tu as déjà la dernière version");
});
