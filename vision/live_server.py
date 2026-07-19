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
import sys
import threading
import time
from datetime import datetime

import cv2
from flask import Flask, Response, jsonify, request

from board import score_from_point
from calib_model import ray_from_column, triangulate
from detector import SETTLE_PIXELS, changed_pixels, extract_impact, preprocess
from test_cameras import open_camera

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(HERE, "dataset")
CALIB = json.load(open(os.path.join(HERE, "calibration.json")))["cams"]
ROTATIONS = {int(k): v for k, v in json.load(open(os.path.join(HERE, "rotations.json"))).items()} \
    if os.path.exists(os.path.join(HERE, "rotations.json")) else {}

ROTATE_CODES = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}

SETTLE_TICKS = 5          # frames stables consécutives pour valider l'impact
MOTION_PIXELS = 800       # pixels changés vs référence pour déclencher

app = Flask(__name__)

_frames = {}              # dernière frame redressée par caméra
_lock = threading.Lock()

state = {"phase": "init", "events": [], "next_id": 1}


def upright(frame, cam):
    rot = ROTATIONS.get(cam, 0)
    return cv2.rotate(frame, ROTATE_CODES[rot]) if rot in ROTATE_CODES else frame


def capture_loop(index):
    cap = open_camera(index)
    if cap is None:
        print(f"ERREUR : caméra {index}")
        return
    while True:
        ok, frame = cap.read()
        if ok:
            with _lock:
                _frames[index] = upright(frame, index)
        else:
            time.sleep(0.05)


def grab_grays():
    with _lock:
        frames = {c: _frames[c].copy() for c in CAMERA_INDICES if c in _frames}
    return frames, {c: preprocess(f) for c, f in frames.items()}


def predict(tips):
    """tips: {cam: (u, v)} -> (throw, x, y, coherence) via triangulation."""
    rays = [ray_from_column(CALIB[str(c)]["params"], u) for c, (u, v) in tips.items()]
    if len(rays) < 2:
        return None
    (x, y), coherence = triangulate(rays)
    throw = score_from_point(x, y)
    return {"throw": throw, "x": round(x, 1), "y": round(y, 1), "coherence_mm": round(coherence, 1)}


def save_event(event, ref_frames, settled_frames, tips):
    folder = os.path.join(DATASET_DIR, event["stamp"])
    os.makedirs(folder, exist_ok=True)
    for c in CAMERA_INDICES:
        if c in ref_frames:
            cv2.imwrite(os.path.join(folder, f"cam{c}_before.jpg"), ref_frames[c])
        if c in settled_frames:
            annotated = settled_frames[c].copy()
            if c in tips:
                cv2.circle(annotated, tips[c], 10, (0, 165, 255), 2)
            cv2.imwrite(os.path.join(folder, f"cam{c}_after.jpg"), annotated)
    json.dump(event, open(os.path.join(folder, "meta.json"), "w"), indent=2)


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

        elif state["phase"] == "mouvement":
            if inter < SETTLE_PIXELS:
                stable_ticks += 1
            else:
                stable_ticks = 0
            if stable_ticks < SETTLE_TICKS:
                continue

            # scène stabilisée : classifier ce qui a changé
            results = {c: extract_impact(ref_grays[c], grays[c]) for c in grays}
            kinds = [r[0] for r in results.values()]

            if "clear" in kinds:
                # main / retrait des fléchettes : nouvelle référence, pas d'événement
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
                state["next_id"] += 1
                save_event(event, ref_frames, frames, tips)
                state["events"].insert(0, event)
                del state["events"][30:]

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
                    "scored": len(done), "correct": correct})


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


@app.route("/event_img/<stamp>/<int:cam>")
def event_img(stamp, cam):
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
  document.getElementById('tally').textContent =
    d.scored ? `Précision : ${d.correct}/${d.scored} (${Math.round(100*d.correct/d.scored)}%)` : '';
  document.getElementById('events').innerHTML = d.events.map(e => {
    const t = e.prediction ? e.prediction.throw : null;
    const pred = t ? `${t.multiplier}x${t.sector} — ${t.score} pts (${t.zone})` : 'échec triangulation';
    const coh = e.prediction ? ` · cohérence ${e.prediction.coherence_mm} mm` : '';
    const truth = e.truth !== null
      ? `<span class="truth-ok">réel : ${e.truth}</span>`
      : `<input id="in${e.id}" placeholder="ex: 3x20"> <button onclick="truth(${e.id})">Valider</button>`;
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
