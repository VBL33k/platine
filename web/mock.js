/* Donnees factices pour previsualiser l'interface dans un navigateur (index.html?apercu). */
(function () {
  const g = ["Chanson Francaise", "Rock Alternatif & Indie", "Punk", "Rock Classique", "Annees 80", "Metal", "Reggae & Dub", "Rap FR"];
  const tracks = [
    ["Renaud", "Mistral Gagnant", 1985, "Chanson Francaise"], ["Nirvana", "In Bloom", 1991, "Grunge"],
    ["Bérurier Noir", "Salut A Toi", 1985, "Punk"], ["The Cure", "Just Like Heaven", 1987, "Rock Alternatif & Indie"],
    ["Bob Marley & The Wailers", "Natural Mystic", 1977, "Reggae & Dub"], ["Georges Brassens", "Le Gorille", 1952, "Chanson Francaise"],
    ["Pixies", "Where Is My Mind", 1988, "Rock Alternatif & Indie"], ["IAM", "Petit Frère", 1997, "Rap FR"],
    ["Metallica", "One", 1988, "Metal"], ["Depeche Mode", "Enjoy The Silence", 1990, "Annees 80"],
    ["Johnny Cash", "Hurt", 2002, "Blues & Rock'n'Roll 50s"], ["Daft Punk", "One More Time", 2000, "Electro"],
  ].map(([artist, title, year, genre], i) => ({ filename: `${artist} - ${title} (${year}).mp3`, artist, title, year, genre, thumb: null, cover_of: i === 10 ? "Nine Inch Nails" : null }));
  let running = false, step = 0;
  window.MockApi = {
    get_state: async () => ({
      version: "1.0.0", config: { music_dir: "H:\\USB - VRAC", genre_folders: true, cover_folder: true, watch: true, autostart: false, ai_enabled: true, ai_budget_eur: 1, api_key_set: true, api_key_hint: "••••x9Qa", usb_dir: location.search.includes("cle") ? "I:\\USB - VRAC" : "" },
      count: 5063, genres: g.map((x, i) => ({ genre: x, n: 700 - i * 70 })), no_art: 594, covers: 428, doublons: 352, review: 3,
      running, progress: { step: "Recherche en ligne (album, pochette, année)", done: step, total: 14 }, folder_ok: true, usb_ok: true, pending: running ? 0 : 14,
      update: location.search.includes("maj") ? { version: "1.2.0", notes: "Meilleure lecture des noms de fichiers.", size: 48000000 } : null,
      updating: false, update_enabled: true,
      budget: { spent_eur: 0.04, limit_eur: 1, left_eur: 0.96 }, ai_ready: true, imported: !location.search.includes("vide"),
      spend: [{ ts: Date.now() / 1000 - 3600, usd: 0.043, note: "1 morceau, recherche web" }, { ts: Date.now() / 1000 - 86400, usd: 0.008, note: "12 morceaux" }], recent: location.search.includes("vide") ? [] : tracks, genre_list: g.concat(["Grunge", "Electro", "Blues & Rock'n'Roll 50s"]),
    }),
    get_events: async () => {
      if (!running) return [];
      const ts = Date.now() / 1000, N = 14, out = [];
      step++;
      if (step === 1) out.push({ kind: "start", total: N, ts });
      const t = tracks[step % tracks.length];
      if (step <= N) out.push({ kind: "progress", stage: "read", done: step, total: N, current: t.filename, ts });
      else if (step === N + 1) out.push({ kind: "stage", stage: "read", state: "done", note: "14 morceaux lisibles", ts },
        { kind: "progress", stage: "dupes", done: 0, total: 1, ts }, { kind: "stage", stage: "dupes", state: "done", note: "1 doublon mis de côté", ts },
        { kind: "track", stage: "doublon", file: "Doublons/" + tracks[3].filename, artist: "x", title: "y", ts });
      else if (step <= 2 * N + 1) out.push({ kind: "progress", stage: "online", done: step - N - 1, total: N, current: t.artist + " - " + t.title, ts });
      else if (step === 2 * N + 2) out.push({ kind: "stage", stage: "online", state: "done", ts }, { kind: "progress", stage: "claude", done: 0, total: 3, label: "Premier tri", ts });
      else if (step === 2 * N + 4) out.push({ kind: "stage", stage: "claude", state: "done", note: "3 morceaux analysés", ts });
      else if (step > 2 * N + 4 && step <= 3 * N + 4) {
        const k = step - 2 * N - 4, r = tracks[k % tracks.length];
        out.push({ kind: "progress", stage: "apply", done: k, total: N, current: r.filename, ts },
          { kind: "track", stage: "ok", file: r.filename, before: r.filename.toLowerCase().replace(/ \(\d+\)/, ""), year: r.year, genre: r.genre, label: k === 3 ? "Faux 320" : "Excellente", cover: r.cover_of, ts });
      } else if (step > 3 * N + 4 && step <= 3 * N + 8) out.push({ kind: "progress", stage: "copies", done: step - 3 * N - 4, total: 4, current: "Punk / " + t.filename, ts });
      else if (step > 3 * N + 8) { running = false; out.push({ kind: "stage", stage: "copies", state: "done", note: "4 copies", ts }, { kind: "done", ok: true, count: N, ts }); }
      return out;
    },
    start: async () => { running = true; step = 0; return true; }, stop: async () => { running = false; },
    get_reviews: async () => [
      { id: 1, kind: "year", question: "En quelle année est sorti « Ad Hoc-1 - Hurlements » ?", options: [1983, 1985], detail: "MusicBrainz et Claude ne sont pas d'accord." },
      { id: 2, kind: "cover", question: "« The Little Rabbits - Karen » est-il une reprise de The Go-Betweens ?", suggestion: "The Go-Betweens", options: ["Oui", "Non"], detail: "Le même titre existe chez un autre artiste." },
      { id: 4, kind: "broken", question: "« Yodelice - Sunday With A Flu.mp3 » semble abîmé ou coupé (39 Ko).", options: [], detail: "Le fichier est trop petit pour être un morceau complet." },
      { id: 3, kind: "genre", question: "Dans quel genre ranger Onrtoup ?", suggestion: "Rap FR", options: g, detail: "Genre incertain." },
    ],
    resolve_review: async () => true,
    check_update: async () => null, start_update: async () => true,
    get_library: async () => ({ total: tracks.length, rows: tracks }),
    get_albums: async () => ({ total: 6, rows: [
      { k: "nevermind", album: "Nevermind", n: 4, n_artists: 1, artist: "Nirvana", year: 1991, genre: "Grunge" },
      { k: "mistral gagnant", album: "Mistral Gagnant", n: 7, n_artists: 1, artist: "Renaud", year: 1985, genre: "Chanson Francaise" },
      { k: "concrete jungle", album: "Catch A Fire", n: 3, n_artists: 1, artist: "Bob Marley & The Wailers", year: 1973, genre: "Reggae & Dub" },
      { k: "disintegration", album: "Disintegration", n: 2, n_artists: 1, artist: "The Cure", year: 1989, genre: "Rock Alternatif & Indie" },
      { k: "best of punk", album: "Ouvert La Nuit", n: 5, n_artists: 1, artist: "Les Wampas", year: 1988, year_max: 1991, genre: "Punk" },
      { k: "", album: "", n: 214, n_artists: 120, artist: "", year: 1952, year_max: 2024, genre: "A Trier" }] }),
    get_album: async () => tracks.slice(0, 6),
    pick_folder: async () => "D:\\Musique a ranger", pick_usb: async () => ({ path: "I:\\USB - VRAC" }), clear_usb: async () => true,
    open_folder: async () => {}, undo_last: async () => 0,
    save_settings: async () => ({}), set_api_key: async () => ({ ok: true, msg: "Clé valide" }), edit_track: async () => "ok",
  };
  // raccourcis d'apercu : ?apercu&view=settings, &edit, &confirm, &run
  window.addEventListener("load", () => setTimeout(() => {
    const q = new URLSearchParams(location.search);
    if (q.get("view")) show(q.get("view"));
    if (q.has("run")) go();
    if (q.has("fin")) { go(); }
    if (q.has("edit")) openEdit(tracks[10]);
    if (q.has("album")) { show("library"); openAlbum({ k: "nevermind", album: "Nevermind", n: 6, n_artists: 1, artist: "Nirvana", year: 1991, genre: "Grunge" }); }
    if (q.has("confirm")) document.querySelector("#undoBtn").click();
  }, 400));
})();
