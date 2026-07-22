// ============================================================
//  Fléchettes — logique front (thème esport premium)
// ============================================================

const CLOCK_SEQ = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,"bull"];

let players = [];
let savedPlayers = [];
let selectedMode = "501";
let currentMultiplier = 1;
let _clockLiveTarget = 1;

// ---- Pause de fin de tour (récupération des fléchettes) ----
const HOLD_SECONDS = 5;
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
  const chips = (finisher.last_throws || []).map(t =>
    `<span class="dart-chip ${t.zone === "miss" ? "miss" : "hit"}">${dartLabel(t)}</span>`).join("");
  const label = bust
    ? `<div class="turn-label" style="color:var(--red)">BUST — TOUR PERDU</div>`
    : `<div class="turn-label">FIN DU TOUR</div>`;
  document.getElementById("game-main").innerHTML =
    `<div class="gm-panel center-col" style="flex:1">
      ${label}
      <div class="turn-name">${finisher.name}</div>
      <div style="font-size:clamp(2.4rem,8vw,5.5rem);font-weight:900;line-height:1;color:${bust ? "var(--red)" : "#fff"}">${playerValue(state.mode, finisher.state)}</div>
      <div class="dart-chips">${chips}</div>
      <div class="recap-hint">🎯 Récupérez les fléchettes · <span id="recap-count">${HOLD_SECONDS}</span> s</div>
    </div>`;
  document.getElementById("edit-score-card").style.display = "none";
}

// ---- Écrans ----
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

async function showHome() {
  showScreen("screen-home");
  const res = await api("GET", "/api/state");
  const card = document.getElementById("resume-card");
  if (!res.error && !res.winner) {
    const names = res.players.map(p => p.name).join(" vs ");
    document.getElementById("resume-info").textContent = `${modeLabel(res.mode)} — ${names}`;
    card.style.display = "flex";
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
  let cells;
  if (mode === "cricket") {
    cells = [15,16,17,18,19,20];
  } else {
    cells = [...Array(20)].map((_, i) => i + 1);
  }

  // cible courante (horloge) pour la mise en évidence
  let target = null;
  if (mode === "clock") {
    const idx = state.current_live_state ? state.current_live_state.target_idx : 0;
    target = CLOCK_SEQ[Math.min(idx, CLOCK_SEQ.length - 1)];
    _clockLiveTarget = target;
  }

  let html = cells.map(s => {
    const isTarget = target === s;
    return `<button class="sector-btn ${isTarget ? "target" : ""}" onclick="throwSector(${s})">${s}</button>`;
  }).join("");
  const bullTarget = target === "bull";
  html += `<button class="sector-btn bull ${bullTarget ? "target" : ""}" onclick="throwBull()">BULL</button>`;
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

// ---- Colonne volée (3 fléchettes) ----
function flightColumn(state, footer) {
  const throws = state.current_throws;
  const slots = [0,1,2].map(i => {
    const t = throws[i];
    if (t) return `<div class="flight-slot filled">${dartLabel(t)}</div>`;
    return `<div class="flight-slot dashed">en attente…</div>`;
  }).join("");
  const total = throws.reduce((s, t) => s + t.score, 0);
  return `<div class="gm-panel flight-col">
    <span class="eyebrow">Fléchettes — volée</span>
    <div class="flight-slots">${slots}</div>
    ${footer || `<div class="flight-total">Total volée : <b>${total}</b></div>`}
  </div>`;
}

// ---- Chips des lancers du tour ----
function throwChips(state) {
  const throws = state.current_throws;
  if (throws.length === 0) return `<div class="dart-chips"></div>`;
  return `<div class="dart-chips">` + throws.map(t =>
    `<span class="dart-chip ${t.zone === "miss" ? "miss" : "hit"}">${dartLabel(t)}</span>`
  ).join("") + `</div>`;
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

  return flightColumn(state) +
    `<div class="gm-panel center-col">
      <div class="turn-label">AU TOUR DE</div>
      <div class="turn-name">${cp.name}</div>
      <div class="turn-score">${live.score}</div>
      ${throwChips(state)}
    </div>
    <div class="gm-panel side-col">
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

  return `<div class="gm-panel center-col" style="flex:1.3">
      <div class="turn-label">AU TOUR DE</div>
      <div class="turn-name">${cp.name}</div>
      <div class="turn-label" style="margin-top:8px">PROCHAINE CIBLE</div>
      <div class="turn-target">${targetLabel}</div>
      <div class="dart-chips">
        <span class="dart-chip">${state.current_throws.length}/3 lancées</span>
        <span class="dart-chip">${remaining} restante${remaining > 1 ? "s" : ""}</span>
      </div>
    </div>
    <div class="gm-panel side-col" style="flex:1.2">
      <span class="eyebrow">Cible</span>
      <div class="clock-board">${drawClockDartboard(target)}</div>
    </div>
    <div class="gm-panel side-col">
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
  html += `<circle cx="${CX}" cy="${CY}" r="${R_DBL_OUT+16}" fill="#0d0d0d"/>`;

  DB_SECTORS.forEach((sec, i) => {
    const a1 = -90 + i*18 - 9, a2 = a1 + 18;
    const isTarget = sec === targetSector;
    const even     = i % 2 === 0;
    const singleC  = isTarget ? "#d62828" : (even ? "#1c1c1c" : "#e8e0cf");
    const ringC    = isTarget ? "#39ff8c" : (even ? "#c0392b" : "#27ae60");
    html += `<path d="${arc(R_OBULL, R_TRI_IN,  a1, a2)}" fill="${singleC}" stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_TRI_IN, R_TRI_OUT, a1, a2)}" fill="${ringC}"   stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_TRI_OUT, R_DBL_IN, a1, a2)}" fill="${singleC}" stroke="#000" stroke-width="0.4"/>`;
    html += `<path d="${arc(R_DBL_IN, R_DBL_OUT, a1, a2)}" fill="${ringC}"   stroke="#000" stroke-width="0.4"/>`;
    const la = toRad(-90 + i*18);
    const lx = CX + R_LABEL*Math.cos(la), ly = CY + R_LABEL*Math.sin(la);
    const lSize = isTarget ? 16 : 12;
    const lFill = isTarget ? "#39ff8c" : "#e0e0e0";
    html += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" font-size="${lSize}" font-weight="${isTarget ? "bold" : "normal"}" fill="${lFill}">${sec}</text>`;
  });

  const bullTarget = targetSector === "bull";
  html += `<circle cx="${CX}" cy="${CY}" r="${R_OBULL}" fill="${bullTarget ? "#39ff8c" : "#27ae60"}" stroke="#000" stroke-width="0.5"/>`;
  html += `<circle cx="${CX}" cy="${CY}" r="${R_BULL}"  fill="${bullTarget ? "#d62828" : "#c0392b"}" stroke="#000" stroke-width="0.5"/>`;
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

  const table = `<div class="gm-panel" style="flex:1">
    <div class="cricket-table" style="grid-template-columns:1fr repeat(${cols}, minmax(60px,1fr))">
      ${headCells}${rows}
    </div>
  </div>`;

  const footer = `<div class="flight-total">Au tour de <b>${state.current_player}</b></div>`;
  return flightColumn(state, footer) + table;
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
  if (!screen || screen.id !== "screen-game") return;
  applyState(await api("GET", "/api/state"));
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
