"""Détection en direct + constitution du jeu de données (étapes 2-3).

Lance les 3 caméras, détecte automatiquement chaque fléchette plantée,
prédit le score par triangulation et l'affiche. Toi, tu entres le score
réel : l'outil mesure la précision en continu et enregistre chaque
événement (images avant/après + méta) dans vision/dataset/ pour pouvoir
rejouer et améliorer l'algorithme hors ligne.

Usage (sur le Pi, depuis vision/, après calibration) :
    ../flechette-env/bin/python live_server.py 0 2 4

puis ouvrir http://flechettes.local:5003
"""

import json
import os
import shutil
import sys
import threading
import time
import urllib.request
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request

from board import score_from_point
from calib_model import ray_from_column, triangulate
from detector import SETTLE_PIXELS, changed_pixels, extract_impact, preprocess
from test_cameras import open_camera

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(HERE, "dataset")
KEEP_EVENTS = 20   # fenêtre glissante : seuls les N derniers événements sont
                   # conservés sur disque (évite de saturer la carte SD)
CALIB = json.load(open(os.path.join(HERE, "calibration.json")))["cams"]
ROTATIONS = {int(k): v for k, v in json.load(open(os.path.join(HERE, "rotations.json"))).items()} \
    if os.path.exists(os.path.join(HERE, "rotations.json")) else {}

ROTATE_CODES = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}

SETTLE_TICKS = 5          # frames stables consécutives pour valider l'impact
MOTION_PIXELS = 800       # pixels changés vs référence pour déclencher
MAX_THROW_TICKS = 8       # frames instables max pour un vrai lancer :
                          # une fléchette plante en 1-2 frames, une main
                          # (récupération, passage) bouge bien plus longtemps

SURFACE_FILE = os.path.join(HERE, "surface.json")

app = Flask(__name__)

_frames = {}              # dernière frame redressée par caméra
_lock = threading.Lock()

state = {"phase": "init", "events": [], "next_id": 1,
         "game_mode": True,    # envoyer les lancers au jeu (sans partie en
                               # cours, l'envoi échoue silencieusement)
         "turn_darts": 0}      # lancers envoyés depuis le début du tour

# surface_lines[cam] = (a, b) : ligne de surface v = a*u + b (image redressée)
surface_lines = {}
if os.path.exists(SURFACE_FILE):
    surface_lines = {int(k): tuple(v) for k, v in json.load(open(SURFACE_FILE)).items()}

# rois[cam] = (x1, y1, x2, y2) : zone de détection (image redressée).
# Tout ce qui est hors du cadre est invisible pour la détection — personnes
# qui passent, arrière-plan vivant, etc.
ROI_FILE = os.path.join(HERE, "roi.json")
rois = {}
if os.path.exists(ROI_FILE):
    rois = {int(k): tuple(v) for k, v in json.load(open(ROI_FILE)).items()}


def apply_roi(gray, cam):
    roi = rois.get(cam)
    if roi is None:
        return gray
    x1, y1, x2, y2 = roi
    out = np.zeros_like(gray)
    out[y1:y2, x1:x2] = gray[y1:y2, x1:x2]
    return out


def upright(frame, cam):
    rot = ROTATIONS.get(cam, 0)
    return cv2.rotate(frame, ROTATE_CODES[rot]) if rot in ROTATE_CODES else frame


def _open_camera_retry(index):
    """Ouvre la caméra en réessayant tant qu'elle n'est pas prête.
    Indispensable au boot : le service peut démarrer avant que les
    caméras USB soient énumérées."""
    while True:
        cap = open_camera(index)
        if cap is not None:
            print(f"caméra {index} ouverte")
            return cap
        print(f"caméra {index} pas encore prête — nouvel essai dans 2 s…")
        time.sleep(2)


def capture_loop(index):
    cap = _open_camera_retry(index)
    fails = 0
    while True:
        ok, frame = cap.read()
        if ok:
            with _lock:
                _frames[index] = upright(frame, index)
            fails = 0
        else:
            fails += 1
            time.sleep(0.05)
            # caméra débranchée / plantée en cours de route : on la rouvre
            if fails >= 100:
                print(f"caméra {index} : lectures en échec, réouverture…")
                try:
                    cap.release()
                except Exception:
                    pass
                cap = _open_camera_retry(index)
                fails = 0


def grab_grays():
    with _lock:
        frames = {c: _frames[c].copy() for c in CAMERA_INDICES if c in _frames}
    return frames, {c: apply_roi(preprocess(f), c) for c, f in frames.items()}


DOUBTFUL_MM = 20.0   # au-delà : probablement une main, pas une fléchette

# ---- Liaison au jeu ----
# Quand le mode jeu est actif, chaque lancer fiable est envoyé au serveur
# de jeu (même route que la saisie manuelle), et une récupération des
# fléchettes en milieu de tour vaut "Valider le tour".
GAME_API = "http://localhost:5000/api"


def game_post(path, body=None):
    try:
        req = urllib.request.Request(GAME_API + path, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, json.dumps(body or {}).encode(), timeout=2) as r:
            return json.load(r)
    except Exception as exc:
        return {"error": str(exc)}


def predict(tips):
    """tips: {cam: (u, v)} -> (throw, x, y, coherence) via triangulation."""
    rays = [ray_from_column(CALIB[str(c)]["params"], u) for c, (u, v) in tips.items()]
    if len(rays) < 2:
        return None
    (x, y), coherence = triangulate(rays)
    throw = score_from_point(x, y)
    out = {"throw": throw, "x": round(x, 1), "y": round(y, 1),
           "coherence_mm": round(coherence, 1)}
    if coherence > DOUBTFUL_MM:
        out["doubtful"] = True
    return out


def save_event(event, ref_frames, settled_frames, tips):
    folder = os.path.join(DATASET_DIR, event["stamp"])
    os.makedirs(folder, exist_ok=True)
    for c in CAMERA_INDICES:
        if c in ref_frames:
            cv2.imwrite(os.path.join(folder, f"cam{c}_before.jpg"), ref_frames[c])
        if c in settled_frames:
            # image brute pour le rejeu hors ligne + annotée pour l'UI
            cv2.imwrite(os.path.join(folder, f"cam{c}_after.jpg"), settled_frames[c])
            annotated = settled_frames[c].copy()
            if c in tips:
                cv2.circle(annotated, tuple(int(round(x)) for x in tips[c]), 10, (0, 165, 255), 2)
            cv2.imwrite(os.path.join(folder, f"cam{c}_annot.jpg"), annotated)
    json.dump(event, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    prune_dataset()


def prune_dataset(keep=KEEP_EVENTS):
    """Ne garde que les `keep` événements les plus récents sur disque.
    Les noms de dossier commencent par la date/heure -> tri chronologique."""
    try:
        folders = sorted(d for d in os.listdir(DATASET_DIR)
                         if os.path.isdir(os.path.join(DATASET_DIR, d)))
    except FileNotFoundError:
        return
    for old in folders[:-keep]:
        shutil.rmtree(os.path.join(DATASET_DIR, old), ignore_errors=True)


def detection_loop():
    # attendre les premières frames
    while len(_frames) < len(CAMERA_INDICES):
        time.sleep(0.2)
    ref_frames, ref_grays = grab_grays()
    prev_grays = ref_grays
    stable_ticks = 0
    state["phase"] = "attente"

    while True:
        time.sleep(1 / 15)
        frames, grays = grab_grays()

        inter = max(changed_pixels(grays[c], prev_grays[c]) for c in grays)
        vs_ref = max(changed_pixels(grays[c], ref_grays[c]) for c in grays)
        prev_grays = grays

        if state["phase"] == "attente":
            if vs_ref > MOTION_PIXELS:
                state["phase"] = "mouvement"
                stable_ticks = 0
                unstable_ticks = 0

        elif state["phase"] == "mouvement":
            if inter < SETTLE_PIXELS:
                stable_ticks += 1
            else:
                stable_ticks = 0
                unstable_ticks += 1
            if stable_ticks < SETTLE_TICKS:
                continue

            # Mouvement trop long pour un lancer : une main à la cible,
            # c'est-à-dire (presque toujours) la récupération des fléchettes.
            # En mode jeu, en milieu de tour, ça vaut "Valider le tour".
            if unstable_ticks > MAX_THROW_TICKS:
                print(f"mouvement long ({unstable_ticks} frames instables) : ignoré")
                if state["game_mode"] and 0 < state["turn_darts"] < 3:
                    game_post("/end_turn")
                state["turn_darts"] = 0
                state["phase"] = "attente"
                ref_frames, ref_grays = frames, grays
                continue

            # scène stabilisée : classifier ce qui a changé
            results = {
                c: extract_impact(ref_grays[c], grays[c], surface_lines.get(c))
                for c in grays
            }
            kinds = [r[0] for r in results.values()]

            if "clear" in kinds:
                # main / retrait des fléchettes : nouvelle référence, pas d'événement.
                # En mode jeu, un retrait en milieu de tour (1 ou 2 lancers)
                # vaut "Valider le tour" ; après 3 lancers le jeu a déjà
                # changé de joueur tout seul.
                if state["game_mode"] and 0 < state["turn_darts"] < 3:
                    game_post("/end_turn")
                state["turn_darts"] = 0
                state["phase"] = "attente"
                ref_frames, ref_grays = frames, grays
                continue

            tips = {c: r[1] for c, r in results.items() if r[0] == "dart"}
            if len(tips) >= 2:
                pred = predict(tips)
                event = {
                    "id": state["next_id"],
                    "stamp": datetime.now().strftime("%Y%m%d_%H%M%S_") + str(state["next_id"]),
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "tips": {str(c): list(t) for c, t in tips.items()},
                    "areas": {str(c): r[2] for c, r in results.items()},
                    "prediction": pred,
                    "truth": None,
                }
                # Envoi au jeu (si mode jeu actif et lancer fiable)
                if state["game_mode"] and pred:
                    if pred.get("doubtful"):
                        event["sent"] = "non envoyé (douteux)"
                    else:
                        res = game_post("/throw", pred["throw"])
                        if "error" in res:
                            event["sent"] = f"erreur jeu : {res['error']}"
                        else:
                            result = res.get("result")
                            event["sent"] = f"envoyé ({result})"
                            if result == "added":
                                state["turn_darts"] += 1
                            else:  # turn_end / win : le jeu a clôturé le tour
                                state["turn_darts"] = 0
                state["next_id"] += 1
                save_event(event, ref_frames, frames, tips)
                state["events"].insert(0, event)
                del state["events"][KEEP_EVENTS:]

            # dans tous les cas : la scène actuelle devient la référence
            ref_frames, ref_grays = frames, grays
            state["phase"] = "attente"


# ------------------------------------------------------------------
# API + page
# ------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    done = [e for e in state["events"] if e["truth"] is not None and e["prediction"]]
    correct = sum(
        1 for e in done
        if e["truth"] == f'{e["prediction"]["throw"]["multiplier"]}x{e["prediction"]["throw"]["sector"]}'
        or e["truth"] == str(e["prediction"]["throw"]["score"])
    )
    return jsonify({"phase": state["phase"], "events": state["events"][:10],
                    "scored": len(done), "correct": correct,
                    "game_mode": state["game_mode"], "turn_darts": state["turn_darts"]})


@app.route("/api/game_mode", methods=["POST"])
def api_game_mode():
    state["game_mode"] = bool(request.json.get("on"))
    state["turn_darts"] = 0
    return jsonify({"ok": True, "game_mode": state["game_mode"]})


@app.route("/api/truth", methods=["POST"])
def api_truth():
    d = request.json
    for e in state["events"]:
        if e["id"] == d["id"]:
            e["truth"] = d["truth"].strip()
            folder = os.path.join(DATASET_DIR, e["stamp"])
            if os.path.exists(os.path.join(folder, "meta.json")):
                json.dump(e, open(os.path.join(folder, "meta.json"), "w"), indent=2)
            return jsonify({"ok": True})
    return jsonify({"error": "événement inconnu"}), 404


@app.route("/surface_img/<int:cam>")
def surface_img(cam):
    # Frame live avec la ligne de surface actuelle superposée
    with _lock:
        frame = _frames.get(cam)
    if frame is None:
        return "pas d'image", 404
    frame = frame.copy()
    if cam in surface_lines:
        a, b = surface_lines[cam]
        w = frame.shape[1]
        cv2.line(frame, (0, int(b)), (w - 1, int(a * (w - 1) + b)), (0, 165, 255), 2)
    _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(jpg.tobytes(), mimetype="image/jpeg")


@app.route("/api/set_surface", methods=["POST"])
def api_set_surface():
    d = request.json
    (u1, v1), (u2, v2) = d["points"]
    if abs(u2 - u1) < 5:
        return jsonify({"error": "points trop proches horizontalement"}), 400
    a = (v2 - v1) / (u2 - u1)
    b = v1 - a * u1
    surface_lines[int(d["cam"])] = (a, b)
    json.dump({str(k): list(v) for k, v in surface_lines.items()}, open(SURFACE_FILE, "w"), indent=2)
    return jsonify({"ok": True})


@app.route("/api/surface_state")
def api_surface_state():
    return jsonify({"cams": CAMERA_INDICES, "configured": sorted(surface_lines.keys())})


@app.route("/roi_img/<int:cam>")
def roi_img(cam):
    # Frame live avec le cadre de détection superposé
    with _lock:
        frame = _frames.get(cam)
    if frame is None:
        return "pas d'image", 404
    frame = frame.copy()
    if cam in rois:
        x1, y1, x2, y2 = rois[cam]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 80), 2)
    _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(jpg.tobytes(), mimetype="image/jpeg")


@app.route("/api/set_roi", methods=["POST"])
def api_set_roi():
    d = request.json
    (u1, v1), (u2, v2) = d["points"]
    x1, x2 = sorted((int(u1), int(u2)))
    y1, y2 = sorted((int(v1), int(v2)))
    if x2 - x1 < 40 or y2 - y1 < 40:
        return jsonify({"error": "cadre trop petit"}), 400
    rois[int(d["cam"])] = (x1, y1, x2, y2)
    json.dump({str(k): list(v) for k, v in rois.items()}, open(ROI_FILE, "w"), indent=2)
    return jsonify({"ok": True})


@app.route("/api/roi_state")
def api_roi_state():
    return jsonify({"cams": CAMERA_INDICES, "configured": sorted(rois.keys())})


@app.route("/event_img/<stamp>/<int:cam>")
def event_img(stamp, cam):
    path = os.path.join(DATASET_DIR, stamp, f"cam{cam}_annot.jpg")
    if not os.path.exists(path):
        path = os.path.join(DATASET_DIR, stamp, f"cam{cam}_after.jpg")
    if not os.path.exists(path):
        return "absent", 404
    return Response(open(path, "rb").read(), mimetype="image/jpeg")


@app.route("/")
def page():
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Détection live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body { background:#1a1a2e; color:#eee; font-family:sans-serif; padding:12px; max-width:900px; margin:auto; }
 h1 { color:#e94560; text-align:center; }
 #phase { text-align:center; font-size:1.3rem; color:#f5a623; margin:10px; }
 #tally { text-align:center; font-size:1.1rem; color:#27ae60; margin:10px; }
 .ev { background:#16213e; border-radius:10px; padding:12px; margin:10px 0; }
 .ev .pred { font-size:1.6rem; color:#f5a623; }
 .ev .meta { color:#8892a4; font-size:.85rem; }
 .ev img { width:200px; border-radius:6px; margin:4px; }
 input { font-size:1.1rem; padding:8px; border-radius:6px; border:none; width:110px; }
 button { font-size:1rem; padding:8px 16px; border:none; border-radius:6px;
          background:#e94560; color:#fff; cursor:pointer; }
 .truth-ok { color:#27ae60; font-weight:bold; }
</style></head><body>
<h1>🎯 Détection live</h1>
<div class="ev">
 <b>📐 Ligne de surface</b> <span id="surfstate" style="color:#8892a4"></span>
 <div style="color:#8892a4;font-size:.85rem">Pour chaque caméra : clique 2 points LE LONG du bord de la
 cible (là où les pointes se plantent), un vers chaque extrémité. Sert à exclure les ombres
 (toujours sous la ligne) et à valider la pointe.</div>
 <button onclick="toggleSurface()" id="surfbtn">Régler</button>
 <div id="surfimgs" style="display:flex;flex-wrap:wrap;gap:8px"></div>
</div>
<div class="ev">
 <b>🔲 Zone de détection</b> <span id="roistate" style="color:#8892a4"></span>
 <div style="color:#8892a4;font-size:.85rem">Pour chaque caméra : clique le coin HAUT-GAUCHE puis le coin
 BAS-DROIT d'un cadre autour de la cible et des fléchettes. Tout ce qui est hors du cadre est ignoré
 (personnes qui passent, arrière-plan).</div>
 <button onclick="toggleRoi()" id="roibtn">Régler</button>
 <div id="roiimgs" style="display:flex;flex-wrap:wrap;gap:8px"></div>
</div>
<div class="ev">
 <b>🎮 Mode jeu</b>
 <label style="cursor:pointer"><input type="checkbox" id="gamemode" onchange="setGameMode()">
 envoyer les lancers détectés au jeu (lance d'abord une partie sur l'écran tactile ou le téléphone)</label>
 <span id="turndarts" style="color:#8892a4"></span>
</div>
<div id="phase"></div>
<div id="tally"></div>
<div class="muted" style="color:#8892a4;text-align:center">
 Lance des fléchettes ! Chaque impact détecté apparaît ici avec le score prédit.<br>
 Entre le score réel au format <b>1x20</b> (simple 20), <b>3x20</b> (triple 20), <b>2x25</b> (bull), <b>0x0</b> (miss/hors cible).
</div>
<div id="events"></div>
<script>
async function refresh() {
  const d = await (await fetch('/api/status')).json();
  document.getElementById('phase').textContent = 'État : ' + d.phase;
  document.getElementById('gamemode').checked = d.game_mode;
  document.getElementById('turndarts').textContent =
    d.game_mode ? ` — tour en cours : ${d.turn_darts}/3 fléchettes` : '';
  document.getElementById('tally').textContent =
    d.scored ? `Précision : ${d.correct}/${d.scored} (${Math.round(100*d.correct/d.scored)}%)` : '';
  // ne pas écraser la liste pendant qu'on tape dans un champ
  const active = document.activeElement;
  if (active && active.tagName === 'INPUT') return;
  document.getElementById('events').innerHTML = d.events.map(e => {
    const t = e.prediction ? e.prediction.throw : null;
    const pred = t ? `${t.multiplier}x${t.sector} — ${t.score} pts (${t.zone})` : 'échec triangulation';
    let coh = e.prediction ? ` · cohérence ${e.prediction.coherence_mm} mm` : '';
    if (e.prediction && e.prediction.doubtful) coh += ' · ⚠ DOUTEUX (main / occlusion ?)';
    if (e.sent) coh += ' · 🎮 ' + e.sent;
    const truth = e.truth !== null
      ? `<span class="truth-ok">réel : ${e.truth}</span>`
      : `<input id="in${e.id}" placeholder="ex: 3x20" onkeydown="if(event.key==='Enter')truth(${e.id})"> <button onclick="truth(${e.id})">Valider</button>`;
    const imgs = Object.keys(e.tips).map(c =>
      `<img src="/event_img/${e.stamp}/${c}">`).join('');
    return `<div class="ev"><span class="pred">${pred}</span>
      <div class="meta">#${e.id} à ${e.time}${coh}</div>
      ${truth}<div>${imgs}</div></div>`;
  }).join('');
}
async function truth(id) {
  const v = document.getElementById('in'+id).value;
  if (!v) return;
  await fetch('/api/truth', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: id, truth: v})});
  refresh();
}
// ---- Réglage de la ligne de surface ----
let surfOpen = false, surfClicks = {};
async function surfaceState() {
  const d = await (await fetch('/api/surface_state')).json();
  const missing = d.cams.filter(c => !d.configured.includes(c));
  document.getElementById('surfstate').textContent = missing.length
    ? '⚠️ à régler : caméras ' + missing.join(', ')
    : '✓ les ' + d.cams.length + ' caméras sont réglées';
  return d;
}
async function toggleSurface() {
  surfOpen = !surfOpen;
  document.getElementById('surfbtn').textContent = surfOpen ? 'Fermer' : 'Régler';
  if (!surfOpen) { document.getElementById('surfimgs').innerHTML = ''; return; }
  const d = await surfaceState();
  surfClicks = {};
  document.getElementById('surfimgs').innerHTML = d.cams.map(c => `
    <div><div>Caméra ${c} — clique 2 points sur le bord</div>
    <img id="surf${c}" src="/surface_img/${c}?t=${Date.now()}" style="width:280px;cursor:crosshair"
         onclick="surfClick(event, ${c}, this)"></div>`).join('');
}
async function surfClick(ev, cam, img) {
  const rect = img.getBoundingClientRect();
  const s = img.naturalWidth / rect.width;
  (surfClicks[cam] = surfClicks[cam] || []).push(
    [(ev.clientX-rect.left)*s, (ev.clientY-rect.top)*s]);
  if (surfClicks[cam].length === 2) {
    await fetch('/api/set_surface', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cam: cam, points: surfClicks[cam]})});
    surfClicks[cam] = [];
    img.src = '/surface_img/' + cam + '?t=' + Date.now();
    surfaceState();
  }
}
// ---- Réglage de la zone de détection ----
let roiOpen = false, roiClicks = {};
async function roiState() {
  const d = await (await fetch('/api/roi_state')).json();
  const missing = d.cams.filter(c => !d.configured.includes(c));
  document.getElementById('roistate').textContent = missing.length
    ? '⚠️ à régler : caméras ' + missing.join(', ')
    : '✓ les ' + d.cams.length + ' caméras sont réglées';
  return d;
}
async function toggleRoi() {
  roiOpen = !roiOpen;
  document.getElementById('roibtn').textContent = roiOpen ? 'Fermer' : 'Régler';
  if (!roiOpen) { document.getElementById('roiimgs').innerHTML = ''; return; }
  const d = await roiState();
  roiClicks = {};
  document.getElementById('roiimgs').innerHTML = d.cams.map(c => `
    <div><div>Caméra ${c} — coin haut-gauche puis bas-droit</div>
    <img id="roi${c}" src="/roi_img/${c}?t=${Date.now()}" style="width:280px;cursor:crosshair"
         onclick="roiClick(event, ${c}, this)"></div>`).join('');
}
async function roiClick(ev, cam, img) {
  const rect = img.getBoundingClientRect();
  const s = img.naturalWidth / rect.width;
  (roiClicks[cam] = roiClicks[cam] || []).push(
    [(ev.clientX-rect.left)*s, (ev.clientY-rect.top)*s]);
  if (roiClicks[cam].length === 2) {
    const r = await (await fetch('/api/set_roi', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cam: cam, points: roiClicks[cam]})})).json();
    roiClicks[cam] = [];
    if (r.error) { alert(r.error); return; }
    img.src = '/roi_img/' + cam + '?t=' + Date.now();
    roiState();
  }
}
async function setGameMode() {
  await fetch('/api/game_mode', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({on: document.getElementById('gamemode').checked})});
  refresh();
}
surfaceState();
roiState();
setInterval(refresh, 1500);
refresh();
</script></body></html>"""


if __name__ == "__main__":
    CAMERA_INDICES = [int(a) for a in sys.argv[1:]]
    if not CAMERA_INDICES:
        print("Usage : python live_server.py <index caméras>  (ex: 0 2 4)")
        sys.exit(1)
    for i in CAMERA_INDICES:
        threading.Thread(target=capture_loop, args=(i,), daemon=True).start()
    threading.Thread(target=detection_loop, daemon=True).start()
    print(f"Caméras {CAMERA_INDICES} — http://0.0.0.0:5003")
    app.run(host="0.0.0.0", port=5003, threaded=True)
