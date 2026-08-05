// ============================================================
//  Fléchettes — logique front (thème esport premium)
// ============================================================

const CLOCK_SEQ = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,"bull"];
const CRICKET_TARGETS = [15,16,17,18,19,20];

let players = [];
let savedPlayers = [];
let selectedMode = "501";
let currentMultiplier = 1;
let _clockLiveTarget = 1;

// ============================================================
//  Moteur d'animations (GIF sur scores spéciaux)
//  Manifeste : événement -> liste de fichiers (tiré au hasard).
//  Ajouter une anim = déposer le fichier dans /static/animations/
//  et ajouter une ligne dans manifest.json.
// ============================================================
const ANIM_DEFAULT_MS = 5000;   // durée d'affichage par défaut (ms)
let ANIM_MANIFEST = {};
const _animQueue = [];
let _animTimer = null;

(async () => {
  try { ANIM_MANIFEST = await (await fetch("/static/animations/manifest.json")).json(); }
  catch (e) { ANIM_MANIFEST = {}; }
})();

// Un événement peut être soit une liste de fichiers (durée par défaut),
// soit un objet { files: [...], duration: ms } pour régler la durée.
function animConfig(eventType) {
  const v = ANIM_MANIFEST[eventType];
  if (!v) return null;
  if (Array.isArray(v)) return { files: v, duration: ANIM_DEFAULT_MS };
  return { files: v.files || [], duration: v.duration || ANIM_DEFAULT_MS };
}

function triggerAnimation(eventType) {
  const cfg = animConfig(eventType);
  if (!cfg || !cfg.files.length) return;
  const file = cfg.files[Math.floor(Math.random() * cfg.files.length)];
  _animQueue.push({ file, duration: cfg.duration });
  if (!_animTimer) playNextAnimation();
}

function playNextAnimation() {
  const overlay = document.getElementById("anim-overlay");
  if (!_animQueue.length) { overlay.classList.remove("show"); overlay.innerHTML = ""; _animTimer = null; return; }
  const { file, duration } = _animQueue.shift();
  // élément créé à la demande puis retiré → mémoire stable même avec beaucoup de GIF
  overlay.innerHTML = `<img class="anim-media" src="/static/animations/${encodeURIComponent(file)}" alt="">`;
  overlay.classList.add("show");
  _animTimer = setTimeout(playNextAnimation, duration);
}

function dismissAnimation() {
  if (_animTimer) { clearTimeout(_animTimer); _animTimer = null; }
  playNextAnimation();
}

// Détecte les événements spéciaux d'un tour terminé (extensible).
function detectAnimations(mode, finisher, bust) {
  if (bust) return;
  const throws = finisher.last_throws || [];
  const total = throws.reduce((s, t) => s + t.score, 0);
  if (total >= 100) triggerAnimation("volee_100plus");
  if (total === 67) triggerAnimation("score_67");
  if (total < 5) triggerAnimation("score_bas");
  if (mode === "cricket" && throws.length === 3 &&
      throws.every(t => t.zone === "bull" || t.zone === "outer_bull" || CRICKET_TARGETS.includes(t.sector))) {
    triggerAnimation("cricket_trois_points");
  }
  // à venir : 180 exact, bullseye, checkout fermé, etc.
}

// ---- Pause de fin de tour (récupération des fléchettes) ----
const HOLD_SECONDS = 3;
let _prevTurns = null;
let _prevCurrent = null;
let _holding = false;
let _holdTimer = null;

function countTurns(state) {
  return state.players.reduce((s, p) => s + (p.history ? p.history.length : 0), 0);
}
function resetTracking(state) {
  _prevTurns = countTurns(state);
  _prevCurrent = state.current_player;
}
function clearHold() {
  _holding = false;
  if (_holdTimer) { clearInterval(_holdTimer); _holdTimer = null; }
  const mp = document.getElementById("manual-panel");
  if (mp) mp.classList.remove("dimmed");
}

// Point d'entrée unique pour appliquer un état reçu du serveur
// (lancer local, validation, ou polling de synchro).
function applyState(state, bust = false) {
  if (!state || state.error) return;
  if (state.winner) { clearHold(); renderGame(state); showWin(state.winner); return; }

  const turns = countTurns(state);
  // Un tour vient de se terminer → pause pour récupérer les fléchettes
  if (!_holding && _prevTurns !== null && turns > _prevTurns && _prevCurrent) {
    const finisher = state.players.find(p => p.name === _prevCurrent);
    resetTracking(state);
    if (finisher && finisher.last_throws && finisher.last_throws.length > 0) {
      detectAnimations(state.mode, finisher, bust);
      startHold(state, finisher, bust);
      return;
    }
  }
  if (_holding) return;            // affichage gelé pendant la pause
  renderGame(state);
  resetTracking(state);
}

function startHold(state, finisher, bust) {
  _holding = true;
  document.getElementById("manual-panel").classList.add("dimmed");
  renderRecap(state, finisher, bust);
  let remaining = HOLD_SECONDS;
  _holdTimer = setInterval(() => {
    remaining--;
    const el = document.getElementById("recap-count");
    if (el) el.textContent = remaining;
    if (remaining <= 0) endHold();
  }, 1000);
}

async function endHold() {
  clearHold();
  applyState(await api("GET", "/api/state"));
}

function renderRecap(state, finisher, bust) {
  document.getElementById("game-mode-label").textContent = modeLabel(state.mode);
  renderScoreboard(state, { active: finisher.name, live: false });
  const chips = (finisher.last_throws || []).map((t, i) => {
    const last = i === (finisher.last_throws.length - 1);
    return `<span class="dart-chip ${t.zone === "miss" ? "miss" : ""} ${last ? "latest" : ""}">${dartLabel(t)}</span>`;
  }).join("");
  const label = bust
    ? `<div class="turn-label" style="color:#ff6b6b">BUST — TOUR PERDU</div>`
    : `<div class="turn-label">FIN DU TOUR</div>`;
  // En mode TV, la pause est le seul moment où personne ne joue : c'est là
  // qu'on montre la cible de précision du joueur qui vient de terminer.
  // Rien à montrer si la partie s'est jouée à la main (pas de coordonnées).
  const impacts = isTv() ? cameraDarts(finisher) : [];
  const board = impacts.length
    ? `<div class="impact-board recap-board">${drawImpactBoard(impacts)}</div>`
    : "";

  document.getElementById("game-main").innerHTML =
    `<div class="gm-panel center-col" style="flex:1">
      ${label}
      <div class="turn-name">${finisher.name}</div>
      <div class="recap-value ${bust ? "bust" : ""}">${playerValue(state.mode, finisher.state)}</div>
      <div class="dart-row">${chips}</div>
      ${board}
      <div class="recap-hint">🎯 Récupérez les fléchettes · <span id="recap-count">${HOLD_SECONDS}</span> s</div>
    </div>`;
  document.getElementById("edit-score-card").style.display = "none";
}

// ---- Écrans ----
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

// Identifie une partie (approximatif : mode + joueurs) pour savoir si la
// partie active sur le serveur est bien celle que l'utilisateur a quittée
// volontairement via "Accueil", ou une nouvelle démarrée depuis un autre appareil.
function gameSignature(state) {
  return state.mode + "|" + state.players.map(p => p.name).join(",");
}
let _dismissedGameSig = null;

async function showHome() {
  showScreen("screen-home");
  const res = await api("GET", "/api/state");
  const card = document.getElementById("resume-card");
  if (!res.error && !res.winner) {
    const names = res.players.map(p => p.name).join(" vs ");
    document.getElementById("resume-info").textContent = `${modeLabel(res.mode)} — ${names}`;
    card.style.display = "flex";
    _dismissedGameSig = gameSignature(res);
  } else {
    card.style.display = "none";
  }
}

async function resumeGame() {
  const res = await api("GET", "/api/state");
  if (res.error) return;
  enterGame(res);
}

function enterGame(state) {
  _dismissedGameSig = null;
  clearHold();
  renderGame(state);
  resetTracking(state);
  showScreen("screen-game");
}

function modeLabel(mode) {
  if (mode === "clock") return "AUTOUR DE L'HORLOGE";
  if (mode === "cricket") return "CRICKET";
  return mode.toUpperCase();
}

// ============================================================
//  ACCUEIL — joueurs & modes
// ============================================================
async function loadSavedPlayers() {
  const res = await api("GET", "/api/players");
  savedPlayers = res.players;
  renderSavedPlayers();
}

function renderSavedPlayers() {
  const el = document.getElementById("saved-players-list");
  if (savedPlayers.length === 0) {
    el.innerHTML = "<span style='color:var(--muted3)'>Aucun joueur enregistré</span>";
    return;
  }
  el.innerHTML = savedPlayers.map(p => {
    const inGame = players.includes(p);
    return `<div class="player-chip saved ${inGame ? "in-game" : ""}">
      <span onclick="togglePlayerInGame('${esc(p)}')">${p}</span>
      <button class="remove" onclick="deleteSavedPlayer('${esc(p)}')">✕</button>
    </div>`;
  }).join("");
}

function togglePlayerInGame(name) {
  players = players.includes(name) ? players.filter(p => p !== name) : [...players, name];
  renderPlayers();
  renderSavedPlayers();
}

async function deleteSavedPlayer(name) {
  if (!confirm(`Supprimer définitivement "${name}" ?`)) return;
  const res = await api("DELETE", `/api/players/${encodeURIComponent(name)}`);
  savedPlayers = res.players;
  players = players.filter(p => p !== name);
  renderPlayers();
  renderSavedPlayers();
}

async function createPlayer() {
  const input = document.getElementById("player-input");
  const name = input.value.trim();
  if (!name) return;
  input.value = "";
  const res = await api("POST", "/api/players", { name });
  savedPlayers = res.players;
  if (!players.includes(name)) players.push(name);
  renderPlayers();
  renderSavedPlayers();
}

document.getElementById("player-input").addEventListener("keydown", e => {
  if (e.key === "Enter") createPlayer();
});

function removePlayer(name) {
  players = players.filter(p => p !== name);
  renderPlayers();
  renderSavedPlayers();
}

function renderPlayers() {
  const el = document.getElementById("players-list");
  if (players.length === 0) {
    el.innerHTML = "<span style='color:var(--muted3)'>Aucun joueur sélectionné</span>";
    return;
  }
  el.innerHTML = players.map(p =>
    `<div class="player-chip active-game">${p}<button class="remove" onclick="removePlayer('${esc(p)}')">✕</button></div>`
  ).join("");
}

document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    selectedMode = btn.dataset.mode;
    document.getElementById("x01-options").style.display =
      (selectedMode === "cricket" || selectedMode === "clock") ? "none" : "flex";
    document.getElementById("cricket-options").style.display =
      selectedMode === "cricket" ? "flex" : "none";
  });
});

async function startGame() {
  if (players.length < 1) { alert("Ajoute au moins un joueur !"); return; }
  const res = await api("POST", "/api/new_game", {
    players,
    mode: selectedMode,
    double_in:  document.getElementById("double-in").checked,
    double_out: document.getElementById("double-out").checked,
    cut_throat: document.getElementById("cut-throat").checked,
  });
  if (res.ok) enterGame(res.state);
}

// ============================================================
//  SAISIE — grille & lancers
// ============================================================
function buildSectorsGrid(state) {
  const mode = state.mode;
  const grid = document.getElementById("sectors-grid");
  const cricket = mode === "cricket";
  const cells = cricket ? [15,16,17,18,19,20] : [...Array(20)].map((_, i) => i + 1);
  const narrow = window.innerWidth < 760;

  // colonnes : dense sur écran large, moins sur mobile
  grid.style.gridTemplateColumns = cricket
    ? `repeat(${narrow ? 4 : 8}, 1fr)`
    : `repeat(${narrow ? 6 : 11}, 1fr)`;

  // cible courante (horloge) pour la mise en évidence
  let target = null;
  if (mode === "clock") {
    const idx = state.current_live_state ? state.current_live_state.target_idx : 0;
    target = CLOCK_SEQ[Math.min(idx, CLOCK_SEQ.length - 1)];
    _clockLiveTarget = target;
  }

  let html = cells.map(s => {
    const cls = target === s ? "target" : (s === 20 ? "hot" : "");
    return `<button class="sector-btn ${cls}" onclick="throwSector(${s})">${s}</button>`;
  }).join("");
  html += `<button class="sector-btn bull ${target === "bull" ? "target" : ""}" onclick="throwBull()">BULL</button>`;
  html += `<button class="sector-btn miss" onclick="throwDart(0,0,0,'miss')">MISS</button>`;
  grid.innerHTML = html;
}

document.querySelectorAll(".mult-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mult-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentMultiplier = parseInt(btn.dataset.mult);
  });
});

function resetMultiplier() {
  currentMultiplier = 1;
  document.querySelectorAll(".mult-btn").forEach(b => b.classList.remove("active"));
  document.querySelector(".mult-btn[data-mult='1']").classList.add("active");
}

function throwSector(sector) {
  const m = currentMultiplier;
  const zone = m === 3 ? "triple" : m === 2 ? "double" : "single";
  throwDart(sector * m, sector, m, zone);
  if (m !== 1) resetMultiplier();
}

function throwBull() {
  // Simple = 25 (couronne), Double/Triple = 50 (centre)
  if (currentMultiplier >= 2) throwDart(50, 25, 2, "bull");
  else                        throwDart(25, 25, 1, "outer_bull");
  if (currentMultiplier !== 1) resetMultiplier();
}

async function throwDart(score, sector, multiplier, zone) {
  if (_holding) return;   // ignore les tirs pendant la pause de fin de tour
  const res = await api("POST", "/api/throw", { score, sector, multiplier, zone });
  applyState(res.state, res.result === "bust");
}

async function endTurn() {
  if (_holding) return;
  const res = await api("POST", "/api/end_turn");
  applyState(res.state);
}

async function undoDart() {
  clearHold();
  const res = await api("POST", "/api/undo_dart");
  renderGame(res.state);
  resetTracking(res.state);
}

// ============================================================
//  RENDU DE L'ÉTAT DE JEU
// ============================================================
function renderGame(state) {
  document.getElementById("game-mode-label").textContent = modeLabel(state.mode);
  buildSectorsGrid(state);
  renderScoreboard(state);
  renderGameMain(state);

  // correction de score : x01 uniquement
  const editCard = document.getElementById("edit-score-card");
  if (state.mode === "cricket" || state.mode === "clock") {
    editCard.style.display = "none";
  } else {
    editCard.style.display = "block";
    renderEditScores(state);
  }

  renderImpactBoard(state);
}

// ---- Cible de précision : impacts caméra du joueur en cours ----
// Fléchettes d'un joueur ayant des coordonnées (donc détectées par les caméras ;
// les fléchettes saisies à la main n'en ont pas). `extra` permet d'ajouter le
// tour en cours, qui n'est pas encore dans l'historique.
function cameraDarts(player, extra) {
  const darts = [];
  (player.history || []).forEach(turn => turn.forEach(t => {
    if (t && t.x != null && t.y != null) darts.push(t);
  }));
  (extra || []).forEach(t => {
    if (t && t.x != null && t.y != null) darts.push(t);
  });
  return darts;
}

function renderImpactBoard(state) {
  const cp = currentPlayer(state);
  const darts = cameraDarts(cp, state.current_throws);

  const label = document.getElementById("impact-name");
  label.textContent = darts.length
    ? `${cp.name} · ${darts.length} impact${darts.length > 1 ? "s" : ""}`
    : `${cp.name} · aucun impact caméra`;
  document.getElementById("impact-board").innerHTML = drawImpactBoard(darts);
}

function drawImpactBoard(darts) {
  // cible neutre (aucune cible surlignée) réutilisée du mode horloge
  const svg = drawClockDartboard(null);
  const CX = 200, CY = 200, MM = 155 / 170;   // mm -> unités SVG (bord double = 170mm ↔ 155)
  const dots = darts.map(t => {
    const sx = (CX + t.x * MM).toFixed(1);
    const sy = (CY - t.y * MM).toFixed(1);     // axe y inversé (SVG vers le bas)
    return `<circle class="impact-dot" cx="${sx}" cy="${sy}" r="4.5" fill="#4de3ff" fill-opacity="0.75" stroke="#0d0620" stroke-width="1"/>`;
  }).join("");
  return svg.replace("</svg>", dots + "</svg>");
}

function playerValue(mode, st) {
  if (mode === "cricket") return `${st.score} pts`;
  if (mode === "clock")   return clockTargetLabel(st.target_idx);
  return st.score;
}

// opts.active : nom du joueur mis en avant (défaut = joueur courant)
// opts.live   : utiliser l'état live pour ce joueur (défaut true)
function renderScoreboard(state, opts = {}) {
  const activeName = opts.active || state.current_player;
  const useLive = opts.live !== false;
  const sb = document.getElementById("scoreboard");
  sb.innerHTML = state.players.map(p => {
    const active = p.name === activeName;
    const st = (active && useLive && state.current_live_state) ? state.current_live_state : p.state;
    return `<div class="ps-card ${active ? "active" : ""}">
      <div class="ps-name">${p.name}</div>
      <div class="ps-value">${playerValue(state.mode, st)}</div>
    </div>`;
  }).join("");
}

function renderGameMain(state) {
  const main = document.getElementById("game-main");
  if (state.mode === "cricket")   main.innerHTML = renderCricketMain(state);
  else if (state.mode === "clock") main.innerHTML = renderClockMain(state);
  else                            main.innerHTML = renderX01Main(state);
}

// ---- Étiquette d'un lancer ----
function dartLabel(t) {
  if (!t || t.zone === "miss") return "Raté";
  if (t.zone === "bull") return "Bull";
  if (t.zone === "outer_bull") return "25";
  if (t.zone === "triple") return `T${t.sector}`;
  if (t.zone === "double") return `D${t.sector}`;
  return `${t.score}`;
}

function currentPlayer(state) {
  return state.players.find(p => p.name === state.current_player) || state.players[0];
}

// ---- Fléchettes du tour, la dernière mise en avant ----
function dartRow(state) {
  const throws = state.current_throws;
  if (throws.length === 0) return `<div class="dart-row"></div>`;
  return `<div class="dart-row">` + throws.map((t, i) => {
    const last = i === throws.length - 1;
    const miss = t.zone === "miss";
    return `<span class="dart-chip ${miss ? "miss" : ""} ${last ? "latest" : ""}">${dartLabel(t)}</span>`;
  }).join("") + `</div>`;
}

// ---- X01 ----
function renderX01Main(state) {
  const cp = currentPlayer(state);
  // stats live à partir de l'historique complet
  let best = 0, total = 0, count = 0;
  (cp.history || []).forEach(turn => {
    const s = turn.reduce((a, t) => a + t.score, 0);
    best = Math.max(best, s); total += s; count++;
  });
  const avg = count ? (total / count).toFixed(1) : "—";
  const live = state.current_live_state || cp.state;

  return `<div class="gm-panel center-col">
      <div class="turn-label">AU TOUR DE</div>
      <div class="turn-name">${cp.name}</div>
      <div class="turn-score">${live.score}</div>
      ${dartRow(state)}
    </div>
    <div class="gm-panel side-col tv-hide">
      <span class="eyebrow">Stats</span>
      <div class="stat-line"><span>Meilleure volée</span><span>${best || "—"}</span></div>
      <div class="stat-line"><span>Moyenne / volée</span><span>${avg}</span></div>
    </div>`;
}

// ---- Horloge ----
function clockTargetLabel(idx) {
  const t = CLOCK_SEQ[Math.min(idx, CLOCK_SEQ.length - 1)];
  return t === "bull" ? "Cible Bull" : `Cible ${t}`;
}

function renderClockMain(state) {
  const cp = currentPlayer(state);
  const liveIdx = state.current_live_state ? state.current_live_state.target_idx : 0;
  const safeIdx = Math.min(liveIdx, CLOCK_SEQ.length - 1);
  const target = CLOCK_SEQ[safeIdx];
  const targetLabel = target === "bull" ? "BULL" : target;
  const remaining = 3 - state.current_throws.length;

  // classement par progression
  const ranked = state.players
    .map(p => ({ name: p.name, prog: p.state.target_idx }))
    .sort((a, b) => b.prog - a.prog);
  const classement = ranked.map((r, i) =>
    `<div class="rank-line ${i === 0 ? "" : "dim"}"><span>${r.name}</span><span>${r.prog}/21</span></div>`
  ).join("");

  // "TOUCHÉ" si la cible a avancé ce tour (état live > état validé)
  const touched = safeIdx > cp.state.target_idx;
  const badge = touched
    ? `<div class="clock-hit ok">TOUCHÉ</div>`
    : `<div class="clock-hit wait">${remaining} fléchette${remaining > 1 ? "s" : ""}</div>`;

  return `<div class="gm-panel center-col" style="flex:1.3">
      <div class="turn-label">AU TOUR DE</div>
      <div class="turn-name">${cp.name}</div>
      <div class="turn-label" style="margin-top:8px">PROCHAINE CIBLE</div>
      <div class="turn-target">${targetLabel}</div>
      ${badge}
      ${dartRow(state)}
    </div>
    <div class="gm-panel side-col" style="flex:1.2">
      <span class="eyebrow">Cible</span>
      <div class="clock-board">${drawClockDartboard(target)}</div>
    </div>
    <div class="gm-panel side-col tv-hide">
      <span class="eyebrow">Classement</span>
      ${classement}
    </div>`;
}

function drawClockDartboard(targetSector) {
  const DB_SECTORS = [20,1,18,4,13,6,10,15,2,17,3,19,7,16,8,11,14,9,12,5];
  const CX = 200, CY = 200;
  const R_BULL   = 6,  R_OBULL = 16;
  const R_TRI_IN = 90, R_TRI_OUT = 100;
  const R_DBL_IN = 147, R_DBL_OUT = 155;
  const R_LABEL  = 171;

  const toRad = d => d * Math.PI / 180;
  function arc(r1, r2, a1, a2) {
    const r1a = toRad(a1), r2a = toRad(a2);
    const x1 = CX+r1*Math.cos(r1a), y1 = CY+r1*Math.sin(r1a);
    const x2 = CX+r1*Math.cos(r2a), y2 = CY+r1*Math.sin(r2a);
    const x3 = CX+r2*Math.cos(r2a), y3 = CY+r2*Math.sin(r2a);
    const x4 = CX+r2*Math.cos(r1a), y4 = CY+r2*Math.sin(r1a);
    return `M${x1},${y1} A${r1},${r1} 0 0,1 ${x2},${y2} L${x3},${y3} A${r2},${r2} 0 0,0 ${x4},${y4}Z`;
  }

  let html = `<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">`;
  html += `<circle cx="${CX}" cy="${CY}" r="${R_DBL_OUT+16}" fill="#0d0620"/>`;

  DB_SECTORS.forEach((sec, i) => {
    const a1 = -90 + i*18 - 9, a2 = a1 + 18;
    const isTarget = sec === targetSector;
    const even     = i % 2 === 0;
    const singleC  = isTarget ? "#ffd23f" : (even ? "#1c1c1c" : "#e8e0cf");
    const ringC    = isTarget ? "#ff3cac" : (even ? "#c0392b" : "#27ae60");
    html += `<path d="${arc(R_OBULL, R_TRI_IN,  a1, a2)}" fill="${singleC}" stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_TRI_IN, R_TRI_OUT, a1, a2)}" fill="${ringC}"   stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_TRI_OUT, R_DBL_IN, a1, a2)}" fill="${singleC}" stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_DBL_IN, R_DBL_OUT, a1, a2)}" fill="${ringC}"   stroke="#000" stroke-width="0.4"/>`;
    const la = toRad(-90 + i*18);
    const lx = CX + R_LABEL*Math.cos(la), ly = CY + R_LABEL*Math.sin(la);
    const lSize = isTarget ? 16 : 12;
    const lFill = isTarget ? "#ff3cac" : "#e0e0e0";
    html += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" font-size="${lSize}" font-weight="${isTarget ? "bold" : "normal"}" fill="${lFill}">${sec}</text>`;
  });

  const bullTarget = targetSector === "bull";
  html += `<circle cx="${CX}" cy="${CY}" r="${R_OBULL}" fill="${bullTarget ? "#ffd23f" : "#27ae60"}" stroke="#000" stroke-width="0.5"/>`;
  html += `<circle cx="${CX}" cy="${CY}" r="${R_BULL}"  fill="${bullTarget ? "#ff3cac" : "#c0392b"}" stroke="#000" stroke-width="0.5"/>`;
  if (bullTarget) {
    html += `<text x="${CX}" y="${CY}" text-anchor="middle" dominant-baseline="middle" font-size="8" font-weight="bold" fill="#fff">BULL</text>`;
  }
  html += `</svg>`;
  return html;
}

// ---- Cricket ----
function renderCricketMain(state) {
  const TARGETS = [20, 19, 18, 17, 16, 15, 25];
  const cols = state.players.length;
  const headCells = `<div class="ct-cell ct-head" style="text-align:left">Cible</div>` +
    state.players.map(p =>
      `<div class="ct-cell ct-head ${p.name === state.current_player ? "me" : ""}">${p.name}</div>`
    ).join("");

  const rows = TARGETS.map(t => {
    const label = t === 25 ? "BULL" : t;
    const cells = state.players.map(p => {
      const marks = (p.name === state.current_player && state.current_live_state)
        ? state.current_live_state.marks : p.state.marks;
      const m = (marks && marks[t]) || 0;
      const closed = m >= 3;
      const txt = m === 0 ? "—" : "✕".repeat(m);
      return `<div class="ct-cell ct-marks ${closed ? "closed" : ""} ${m === 0 ? "none" : ""}">${txt}</div>`;
    }).join("");
    return `<div class="ct-cell ct-num">${label}</div>` + cells;
  }).join("");

  return `<div class="gm-panel" style="flex:1">
    <div class="cricket-table" style="grid-template-columns:1.2fr repeat(${cols}, minmax(60px,1fr))">
      ${headCells}${rows}
    </div>
    ${dartRow(state)}
  </div>`;
}

// ---- Correction de score ----
function renderEditScores(state) {
  const list = document.getElementById("edit-score-list");
  list.innerHTML = state.players.map((p, i) =>
    `<div class="edit-score-row">
      <span>${p.name}</span>
      <input type="number" id="edit-${i}" value="${p.state.score}" min="0" />
      <button onclick="applyScoreEdit(${i})">OK</button>
    </div>`
  ).join("");
}

async function applyScoreEdit(idx) {
  const val = parseInt(document.getElementById(`edit-${idx}`).value);
  if (isNaN(val) || val < 0) return;
  const res = await api("POST", "/api/set_score", { player_idx: idx, score: val });
  renderGame(res.state);
}

// ---- Victoire ----
function showWin(name) {
  document.getElementById("winner-name").textContent = name;
  showScreen("screen-win");
}

// Annuler la fléchette gagnante (erreur de saisie) et reprendre la partie
async function undoFromWin() {
  const res = await api("POST", "/api/undo_dart");
  if (res.error || res.state.error) return;
  clearHold();
  renderGame(res.state);
  resetTracking(res.state);
  showScreen("screen-game");
}

// ============================================================
//  HISTORIQUE & STATS
// ============================================================
async function showHistory() {
  await loadGames();
  await loadStats();
  switchTab("tab-games");
  showScreen("screen-history");
}

function switchTab(tabId) {
  const tabs = ["tab-games", "tab-stats"];
  document.querySelectorAll(".tab-btn").forEach((b, i) => b.classList.toggle("active", tabs[i] === tabId));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.id === tabId));
}

async function loadGames() {
  const games = await api("GET", "/api/games");
  const el = document.getElementById("games-list");
  if (games.length === 0) {
    el.innerHTML = "<div class='card'><span style='color:var(--muted)'>Aucune partie enregistrée</span></div>";
    return;
  }
  el.innerHTML = games.map(g => {
    const finished = !!g.winner;
    const winnerHtml = finished ? `🏆 ${g.winner}` : `<em style="color:var(--muted)">Non terminée</em>`;
    const actionBtn = finished
      ? `<button class="btn-ghost replay-btn" onclick='replayGame(${JSON.stringify(g).replace(/'/g, "&#39;")})'>Rejouer avec ces joueurs</button>`
      : `<button class="btn-secondary replay-btn" onclick="resumeArchivedGame(${g.id})">↩ Reprendre la partie</button>`;
    return `<div class="card game-record" id="game-${g.id}">
      <div class="game-record-header">
        <span class="game-mode-tag">${modeLabel(g.mode)}</span>
        <span class="game-date">${g.date}</span>
        <button class="btn-delete-game" onclick="deleteGame(${g.id})">🗑</button>
      </div>
      <div class="game-players">${g.players.join(" vs ")}</div>
      <div class="game-winner">${winnerHtml}</div>
      ${actionBtn}
    </div>`;
  }).join("") + `<button class="btn-ghost big delete-all-btn" onclick="deleteAllGames()">🗑 Effacer tout l'historique</button>`;
}

async function resumeArchivedGame(id) {
  const res = await api("POST", `/api/resume_game/${id}`);
  if (res.error) { alert(res.error); return; }
  enterGame(res.state);
}

async function deleteGame(id) {
  if (!confirm("Supprimer cette partie ?")) return;
  await api("DELETE", `/api/games/${id}`);
  await loadGames();
}

async function deleteAllGames() {
  if (!confirm("Effacer tout l'historique ?")) return;
  if (!confirm("Confirmer : supprimer toutes les parties définitivement ?")) return;
  await api("DELETE", "/api/games");
  await loadGames();
}

function replayGame(g) {
  players = [...g.players];
  selectedMode = g.mode;
  renderPlayers();
  renderSavedPlayers();
  document.querySelectorAll(".mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === g.mode));
  document.getElementById("x01-options").style.display =
    (g.mode === "cricket" || g.mode === "clock") ? "none" : "flex";
  document.getElementById("cricket-options").style.display = g.mode === "cricket" ? "flex" : "none";
  showHome();
}

async function loadStats() {
  const stats = await api("GET", "/api/stats");
  const el = document.getElementById("stats-list");
  const entries = Object.entries(stats);
  if (entries.length === 0) {
    el.innerHTML = "<div class='card'><span style='color:var(--muted)'>Aucun joueur enregistré</span></div>";
    return;
  }
  el.innerHTML = entries.map(([name, s]) => `
    <div class="card">
      <h2>${name}</h2>
      <div class="stats-grid">
        <div class="stat-box"><div class="stat-value">${s.games_played}</div><div class="stat-label">Parties</div></div>
        <div class="stat-box"><div class="stat-value">${s.games_won}</div><div class="stat-label">Victoires</div></div>
        <div class="stat-box"><div class="stat-value">${s.win_rate}%</div><div class="stat-label">% victoires</div></div>
        <div class="stat-box"><div class="stat-value">${s.avg_turn}</div><div class="stat-label">Moy. / tour</div></div>
        <div class="stat-box"><div class="stat-value">${s.best_turn}</div><div class="stat-label">Meilleur tour</div></div>
      </div>
    </div>`).join("");
}

// ============================================================
//  RAFRAÎCHISSEMENT AUTO (synchro multi-appareils)
// ============================================================
setInterval(async () => {
  const screen = document.querySelector(".screen.active");
  if (!screen) return;
  if (screen.id === "screen-game") {
    applyState(await api("GET", "/api/state"));
    return;
  }
  // Accueil / victoire : détecte une partie démarrée depuis un autre appareil
  // et y bascule automatiquement (pas besoin de recharger la page du Pi).
  // On ignore la partie que l'utilisateur vient de quitter volontairement
  // (bouton Accueil) pour ne pas l'y renvoyer de force — elle reste
  // accessible via la carte "Reprendre".
  if (screen.id === "screen-home" || screen.id === "screen-win") {
    const res = await api("GET", "/api/state");
    if (!res.error && !res.winner && gameSignature(res) !== _dismissedGameSig) enterGame(res);
  }
}, 2000);

// ============================================================
//  CLAVIER VIRTUEL (kiosk uniquement)
// ============================================================
const VKB_ROWS = [
  ["A","Z","E","R","T","Y","U","I","O","P"],
  ["Q","S","D","F","G","H","J","K","L"],
  ["W","X","C","V","B","N","M","⌫"],
  ["Espace","OK"]
];

function buildVkb() {
  const vkb = document.getElementById("vkb");
  const close = vkb.querySelector(".vkb-close");
  vkb.innerHTML = "";
  vkb.appendChild(close);
  VKB_ROWS.forEach(row => {
    const div = document.createElement("div");
    div.className = "vkb-row";
    row.forEach(k => {
      const btn = document.createElement("button");
      btn.textContent = k;
      btn.className = "vkb-key" +
        (k === "Espace" ? " vkb-space" : "") +
        (k === "OK"     ? " vkb-ok"    : "") +
        (k === "⌫"     ? " vkb-bksp"  : "");
      btn.addEventListener("pointerdown", e => { e.preventDefault(); vkbPress(k); }, {passive: false});
      div.appendChild(btn);
    });
    vkb.appendChild(div);
  });
}

function vkbPress(key) {
  const input = document.getElementById("player-input");
  if      (key === "⌫")      input.value = input.value.slice(0, -1);
  else if (key === "Espace")  input.value += " ";
  else if (key === "OK")      { createPlayer(); hideVkb(); }
  else                         input.value += key;
}

function showVkb() {
  document.getElementById("vkb").style.display = "flex";
  document.getElementById("screen-home").style.paddingBottom = "300px";
  setTimeout(() => {
    const input = document.getElementById("player-input");
    const rect  = input.getBoundingClientRect();
    const kbTop = window.innerHeight - 300;
    if (rect.bottom > kbTop - 20) window.scrollBy({ top: rect.bottom - kbTop + 30, behavior: "smooth" });
  }, 60);
}
function hideVkb() {
  document.getElementById("vkb").style.display = "none";
  document.getElementById("screen-home").style.paddingBottom = "";
}

async function exitKiosk() {
  if (!confirm("Fermer l'application et revenir au bureau du Pi ?")) return;
  await api("POST", "/api/exit_kiosk");
}

const isKiosk = new URLSearchParams(window.location.search).get("kiosk") === "1";
if (isKiosk) {
  document.getElementById("player-input").addEventListener("focus", showVkb);
  document.getElementById("exit-kiosk-btn").style.display = "block";
}

// ============================================================
//  MODE TV (écran du Pi)
//  L'écran du Pi se regarde de loin : on masque la saisie et on grossit
//  l'essentiel. Le bouton ⚙ rend l'interface complète le temps d'une
//  correction, puis l'affichage TV revient tout seul.
// ============================================================
const TV_RETURN_MS = 45000;
let _tvTimer = null;

function isTv() { return document.body.classList.contains("tv"); }

function setTv(on) {
  document.body.classList.toggle("tv", on);
  clearTimeout(_tvTimer);
  // hors mode TV, on y retourne après un moment sans rien toucher
  if (!on) _tvTimer = setTimeout(() => setTv(true), TV_RETURN_MS);
}

function toggleTv() { setTv(!isTv()); }

if (isKiosk) {
  document.getElementById("tv-toggle").style.display = "block";
  setTv(true);
  // chaque interaction repousse le retour automatique
  ["click", "touchstart", "keydown"].forEach(ev =>
    document.addEventListener(ev, () => { if (!isTv()) setTv(false); }, true));
}

// ---- utilitaires ----
function esc(s) { return String(s).replace(/'/g, "\\'"); }

async function api(method, url, body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  return res.json();
}

// ---- Init ----
buildVkb();
loadSavedPlayers();
(async () => {
  const res = await api("GET", "/api/state");
  if (!res.error) {
    renderGame(res);
    resetTracking(res);
    showScreen(res.winner ? "screen-win" : "screen-game");
    if (res.winner) document.getElementById("winner-name").textContent = res.winner;
  }
})();
