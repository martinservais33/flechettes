// ---- État local ----
const SECTORS = [20,1,18,4,13,6,10,15,2,17,3,19,7,16,8,11,14,9,12,5];
let players = [];
let savedPlayers = [];
let selectedMode = "501";
let currentMultiplier = 1;

// ---- Écrans ----
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}
function showHome() { showScreen("screen-home"); }

// ---- Joueurs enregistrés ----
async function loadSavedPlayers() {
  const res = await api("GET", "/api/players");
  savedPlayers = res.players;
  renderSavedPlayers();
}

function renderSavedPlayers() {
  const el = document.getElementById("saved-players-list");
  if (savedPlayers.length === 0) {
    el.innerHTML = "<span style='color:var(--muted)'>Aucun joueur enregistré</span>";
    return;
  }
  el.innerHTML = savedPlayers.map(p => {
    const inGame = players.includes(p);
    return `<div class="player-chip saved ${inGame ? "in-game" : ""}">
      <span onclick="togglePlayerInGame('${p}')">${p}</span>
      <button class="remove" onclick="deleteSavedPlayer('${p}')">✕</button>
    </div>`;
  }).join("");
}

function togglePlayerInGame(name) {
  if (players.includes(name)) {
    players = players.filter(p => p !== name);
  } else {
    players.push(name);
  }
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

// ---- Joueurs de la partie ----
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
    el.innerHTML = "<span style='color:var(--muted)'>Aucun joueur sélectionné</span>";
    return;
  }
  el.innerHTML = players.map(p =>
    `<div class="player-chip active-game">${p}<button class="remove" onclick="removePlayer('${p}')">✕</button></div>`
  ).join("");
}

// ---- Mode de jeu ----
document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    selectedMode = btn.dataset.mode;
    document.getElementById("x01-options").style.display =
      selectedMode === "cricket" ? "none" : "flex";
    document.getElementById("cricket-options").style.display =
      selectedMode === "cricket" ? "flex" : "none";
  });
});

// ---- Démarrer la partie ----
async function startGame() {
  if (players.length < 1) { alert("Ajoute au moins un joueur !"); return; }
  const res = await api("POST", "/api/new_game", {
    players,
    mode: selectedMode,
    double_in:  document.getElementById("double-in").checked,
    double_out: document.getElementById("double-out").checked,
    cut_throat: document.getElementById("cut-throat").checked,
  });
  if (res.ok) {
    buildSectorsGrid();
    renderGame(res.state);
    showScreen("screen-game");
    document.getElementById("game-mode-label").textContent = selectedMode.toUpperCase();
  }
}

// ---- Grille secteurs ----
function buildSectorsGrid(mode) {
  const m = mode || selectedMode;
  const grid = document.getElementById("sectors-grid");
  if (m === "cricket") {
    grid.innerHTML = [15, 16, 17, 18, 19, 20].map(s =>
      `<button class="sector-btn" onclick="throwSector(${s})">${s}</button>`
    ).join("");
    grid.style.gridTemplateColumns = "repeat(3, 1fr)";
  } else {
    grid.innerHTML = [...Array(20)].map((_, i) => i + 1).map(s =>
      `<button class="sector-btn" onclick="throwSector(${s})">${s}</button>`
    ).join("");
    grid.style.gridTemplateColumns = "repeat(5, 1fr)";
  }
}

// ---- Multiplicateur ----
document.querySelectorAll(".mult-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mult-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentMultiplier = parseInt(btn.dataset.mult);
  });
});

function throwSector(sector) {
  const m = currentMultiplier;
  const zone = m === 3 ? "triple" : m === 2 ? "double" : "single";
  throwDart(sector * m, sector, m, zone);
  if (m !== 1) {
    currentMultiplier = 1;
    document.querySelectorAll(".mult-btn").forEach(b => b.classList.remove("active"));
    document.querySelector(".mult-btn[data-mult='1']").classList.add("active");
  }
}

async function throwDart(score, sector, multiplier, zone) {
  const res = await api("POST", "/api/throw", { score, sector, multiplier, zone });
  renderGame(res.state);
  if (res.result === "win") showWin(res.state.winner);
}

// ---- Fin de tour ----
async function endTurn() {
  const res = await api("POST", "/api/end_turn");
  renderGame(res.state);
  if (res.result === "win") showWin(res.state.winner);
}

// ---- Undo lancer ----
async function undoDart() {
  const res = await api("POST", "/api/undo_dart");
  renderGame(res.state);
}

// ---- Undo tour complet ----
async function undoTurn() {
  const res = await api("POST", "/api/undo");
  renderGame(res.state);
}

// ---- Archive + retour accueil ----
async function archiveAndGoHome() {
  await api("POST", "/api/archive");
  showHome();
}

// ---- Victoire ----
function showWin(name) {
  document.getElementById("winner-name").textContent = name;
  showScreen("screen-win");
}

// ---- Lancers du dernier tour ----
function renderLastThrows(throws) {
  if (!throws || throws.length === 0)
    return `<span class="lt-empty">—</span>`;
  const chips = throws.map(t => {
    if (t.zone === "miss") return `<span class="lt-chip lt-miss">Miss</span>`;
    const label = t.zone === "triple" ? `T${t.sector}`
                : t.zone === "double" ? `D${t.sector}`
                : `${t.score}`;
    return `<span class="lt-chip">${label}</span>`;
  });
  const total = throws.reduce((s, t) => s + t.score, 0);
  chips.push(`<span class="lt-total">${total}</span>`);
  return chips.join("");
}

// ---- Rendu état jeu ----
function renderGame(state) {
  buildSectorsGrid(state.mode);
  document.getElementById("current-player-name").textContent = state.current_player;

  const sb = document.getElementById("scoreboard");
  sb.innerHTML = state.players.map(p => {
    const isActive = p.name === state.current_player;
    if (state.mode === "cricket") return renderCricketCard(p, isActive);
    return `<div class="player-score-card ${isActive ? "active" : ""}">
      <div class="pname">${p.name}</div>
      <div class="pscore">${p.state.score}</div>
      <div class="last-throws">${renderLastThrows(p.last_throws)}</div>
    </div>`;
  }).join("");

  const throws = state.current_throws;
  document.getElementById("current-throws").innerHTML =
    throws.length === 0
      ? "<span style='color:var(--muted)'>Aucune flèche</span>"
      : throws.map(t => `<div class="throw-chip">${
          t.zone === "miss" ? "Miss"
          : t.score + (t.zone === "triple" ? " (T)" : t.zone === "double" ? " (D)" : "")
        }</div>`).join("");

  renderEditScores(state);
}

function cricketMarkSvg(m) {
  const closed = m >= 3;
  const lines = [];
  // 1er trait : diagonal gauche→droite
  if (m >= 1) lines.push(`<line x1="8" y1="8" x2="22" y2="22" stroke="${closed ? "var(--green)" : "var(--text)"}" stroke-width="3" stroke-linecap="round"/>`);
  // 2ème trait : diagonal droite→gauche (forme X)
  if (m >= 2) lines.push(`<line x1="22" y1="8" x2="8" y2="22" stroke="${closed ? "var(--green)" : "var(--text)"}" stroke-width="3" stroke-linecap="round"/>`);
  // Cercle pour fermer
  if (closed) lines.push(`<circle cx="15" cy="15" r="12" stroke="var(--green)" stroke-width="2.5" fill="none"/>`);

  return `<svg width="30" height="30" viewBox="0 0 30 30">${lines.join("")}</svg>`;
}

function renderCricketCard(p, isActive) {
  const TARGETS = [20, 19, 18, 17, 16, 15, 25];
  const marks = p.state.marks;
  const marksHtml = TARGETS.map(t => {
    const m = marks[t] || 0;
    const closed = m >= 3;
    return `<div class="cricket-row ${closed ? "row-closed" : ""}">
      <span class="cricket-num">${t === 25 ? "B" : t}</span>
      <div class="cricket-mark-svg">${cricketMarkSvg(m)}</div>
    </div>`;
  }).join("");

  return `<div class="player-score-card cricket-card ${isActive ? "active" : ""}">
    <div class="pname">${p.name}</div>
    <div class="pscore">${p.state.score}</div>
    <div class="cricket-marks-grid">${marksHtml}</div>
  </div>`;
}

function renderEditScores(state) {
  const list = document.getElementById("edit-score-list");
  if (state.mode === "cricket") {
    list.innerHTML = "<span style='color:var(--muted)'>Non disponible en Cricket</span>";
    return;
  }
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

// ---- Historique & Stats ----
async function showHistory() {
  await loadGames();
  await loadStats();
  switchTab("tab-games");
  showScreen("screen-history");
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach((b, i) => {
    const tabs = ["tab-games", "tab-stats"];
    b.classList.toggle("active", tabs[i] === tabId);
  });
  document.querySelectorAll(".tab-content").forEach(c => {
    c.classList.toggle("active", c.id === tabId);
  });
}

async function loadGames() {
  const games = await api("GET", "/api/games");
  const el = document.getElementById("games-list");
  if (games.length === 0) {
    el.innerHTML = "<div class='card'><span style='color:var(--muted)'>Aucune partie enregistrée</span></div>";
    return;
  }
  el.innerHTML = games.map(g => {
    const winner = g.winner ? `🏆 ${g.winner}` : "Non terminée";
    const players = g.players.join(" vs ");
    return `<div class="card game-record" id="game-${g.id}">
      <div class="game-record-header">
        <span class="game-mode-tag">${g.mode.toUpperCase()}</span>
        <span class="game-date">${g.date}</span>
        <button class="btn-delete-game" onclick="deleteGame(${g.id})">🗑</button>
      </div>
      <div class="game-players">${players}</div>
      <div class="game-winner">${winner}</div>
      <button class="btn-ghost replay-btn" onclick="replayGame(${JSON.stringify(g).split('"').join("'")})">
        Rejouer avec ces joueurs
      </button>
    </div>`;
  }).join("") + `<button class="btn-ghost big delete-all-btn" onclick="deleteAllGames()">🗑 Effacer tout l'historique</button>`;
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
  // pré-sélectionner les joueurs et le mode
  players = [...g.players];
  selectedMode = g.mode;
  renderPlayers();
  renderSavedPlayers();
  document.querySelectorAll(".mode-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.mode === g.mode);
  });
  document.getElementById("x01-options").style.display =
    g.mode === "cricket" ? "none" : "flex";
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
    <div class="card stats-card">
      <h2>${name}</h2>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-value">${s.games_played}</div>
          <div class="stat-label">Parties jouées</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">${s.games_won}</div>
          <div class="stat-label">Victoires</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">${s.win_rate}%</div>
          <div class="stat-label">% victoires</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">${s.avg_turn}</div>
          <div class="stat-label">Moy. / tour</div>
        </div>
        <div class="stat-box">
          <div class="stat-value">${s.best_turn}</div>
          <div class="stat-label">Meilleur tour</div>
        </div>
      </div>
    </div>
  `).join("");
}

// ---- Rafraîchissement automatique ----
setInterval(async () => {
  const screen = document.querySelector(".screen.active");
  if (!screen || screen.id !== "screen-game") return;
  const res = await api("GET", "/api/state");
  if (res.error) return;
  renderGame(res);
}, 2000);

// ---- Thème ----
function toggleTheme() {
  const link = document.getElementById("theme-css");
  const btn  = document.getElementById("theme-toggle");
  const isLight = link.href.includes("style_light");
  link.href = isLight ? "/static/css/style.css" : "/static/css/style_light.css";
  btn.textContent = isLight ? "🌙 Sombre" : "☀️ Clair";
  localStorage.setItem("theme", isLight ? "dark" : "light");
}

// ---- Init ----
// restaurer le thème sauvegardé
(function() {
  const saved = localStorage.getItem("theme");
  if (saved === "light") {
    document.getElementById("theme-css").href = "/static/css/style_light.css";
    document.getElementById("theme-toggle").textContent = "☀️ Clair";
  }
})();
loadSavedPlayers();
(async () => {
  const res = await api("GET", "/api/state");
  if (!res.error) {
    buildSectorsGrid();
    renderGame(res);
    document.getElementById("game-mode-label").textContent = res.mode.toUpperCase();
    showScreen(res.winner ? "screen-win" : "screen-game");
    if (res.winner) document.getElementById("winner-name").textContent = res.winner;
  }
})();

// ---- Utilitaire API ----
async function api(method, url, body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  return res.json();
}
