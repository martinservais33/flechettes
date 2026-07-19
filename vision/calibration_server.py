"""Étape « calibration » — outil web guidé.

Pour chaque point de référence de la cible (bull, doubles, triples) :
plante une fléchette dessus, capture les 3 caméras, clique sur la pointe
dans chaque image. Quand tous les points sont faits, « Calculer » ajuste
le modèle de chaque caméra et sauvegarde vision/calibration.json.

Un mode test permet ensuite de planter une fléchette n'importe où et de
vérifier le score triangulé.

Usage (sur le Pi, depuis le dossier vision/) :
    ../flechette-env/bin/python calibration_server.py 0 2 4

puis ouvrir http://flechettes.local:5002 depuis le Mac.
"""

import json
import math
import os
import sys
import threading
import time

import cv2
from flask import Flask, Response, jsonify, request

from board import CALIB_POINTS, score_from_point, sector_center_angle
from calib_model import fit_camera, ray_from_column, triangulate
from test_cameras import open_camera

HERE = os.path.dirname(os.path.abspath(__file__))
POINTS_FILE = os.path.join(HERE, "calibration_points.json")
CALIB_FILE = os.path.join(HERE, "calibration.json")

CAM_R_INIT = 260.0  # mm : distance initiale supposée caméra-centre

app = Flask(__name__)

_frames = {}          # dernière frame live par caméra
_captured = {}        # frames figées de la dernière capture
_lock = threading.Lock()

# clicks[point_id][cam] = [u, v]
clicks = {}
if os.path.exists(POINTS_FILE):
    clicks = json.load(open(POINTS_FILE))


def capture_loop(index):
    cap = open_camera(index)
    if cap is None:
        print(f"ERREUR : caméra {index} impossible à ouvrir")
        return
    while True:
        ok, frame = cap.read()
        if ok:
            with _lock:
                _frames[index] = frame
        else:
            time.sleep(0.05)


# ------------------------------------------------------------------
# API
# ------------------------------------------------------------------
@app.route("/api/points")
def api_points():
    return jsonify({
        "points": [{"id": p[0], "label": p[1]} for p in CALIB_POINTS],
        "cams": CAMERA_INDICES,
        "clicks": clicks,
        "calibrated": os.path.exists(CALIB_FILE),
    })


@app.route("/api/capture", methods=["POST"])
def api_capture():
    with _lock:
        missing = [i for i in CAMERA_INDICES if i not in _frames]
        if missing:
            return jsonify({"error": f"pas d'image des caméras {missing}"}), 500
        for i in CAMERA_INDICES:
            _captured[i] = _frames[i].copy()
    return jsonify({"ok": True, "t": time.time()})


@app.route("/img/<int:cam>")
def img(cam):
    with _lock:
        frame = _captured.get(cam)
    if frame is None:
        return "pas de capture", 404
    _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return Response(jpg.tobytes(), mimetype="image/jpeg")


@app.route("/api/click", methods=["POST"])
def api_click():
    d = request.json
    clicks.setdefault(d["point"], {})[str(d["cam"])] = [d["u"], d["v"]]
    json.dump(clicks, open(POINTS_FILE, "w"), indent=2)
    return jsonify({"ok": True})


@app.route("/api/reset_point", methods=["POST"])
def api_reset_point():
    clicks.pop(request.json["point"], None)
    json.dump(clicks, open(POINTS_FILE, "w"), indent=2)
    return jsonify({"ok": True})


@app.route("/api/fit", methods=["POST"])
def api_fit():
    # positions initiales approximatives : secteur au-dessus duquel est chaque caméra
    init_sectors = request.json["init_sectors"]  # {"0": 3, "2": 9, "4": 4}
    coords = {p[0]: (p[2], p[3]) for p in CALIB_POINTS}

    result = {}
    for cam in CAMERA_INDICES:
        obs = [
            (coords[pid], pts[str(cam)][0])
            for pid, pts in clicks.items()
            if str(cam) in pts and pid in coords
        ]
        if len(obs) < 6:
            return jsonify({"error": f"caméra {cam} : seulement {len(obs)} points, minimum 6"}), 400

        a = math.radians(sector_center_angle(int(init_sectors[str(cam)])))
        cx, cy = CAM_R_INIT * math.cos(a), CAM_R_INIT * math.sin(a)
        phi = math.atan2(-cy, -cx)          # pointe vers le centre
        init = [cx, cy, phi, 460.0, 320.0]  # f pour ~70° de champ en 640px

        params, rms = fit_camera(obs, init)
        result[str(cam)] = {"params": params, "rms_px": round(rms, 2), "n_points": len(obs)}

    json.dump({"cams": result, "date": time.strftime("%Y-%m-%d %H:%M")},
              open(CALIB_FILE, "w"), indent=2)
    return jsonify({"ok": True, "result": result})


@app.route("/api/test", methods=["POST"])
def api_test():
    if not os.path.exists(CALIB_FILE):
        return jsonify({"error": "pas encore calibré"}), 400
    calib = json.load(open(CALIB_FILE))["cams"]
    cols = request.json["columns"]  # {"0": u, "2": u, ...} cams cliquées
    if len(cols) < 2:
        return jsonify({"error": "cliquer la pointe dans au moins 2 images"}), 400

    rays = [ray_from_column(calib[c]["params"], u) for c, u in cols.items()]
    (x, y), err = triangulate(rays)
    throw = score_from_point(x, y)
    return jsonify({"ok": True, "x": round(x, 1), "y": round(y, 1),
                    "coherence_mm": round(err, 1), "throw": throw})


# ------------------------------------------------------------------
# Page
# ------------------------------------------------------------------
@app.route("/")
def page():
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Calibration</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body { background:#1a1a2e; color:#eee; font-family:sans-serif; padding:12px; max-width:1000px; margin:auto; }
 h1 { color:#e94560; text-align:center; }
 .step { background:#16213e; border-radius:10px; padding:14px; margin:12px 0; }
 button { font-size:1rem; padding:10px 20px; border:none; border-radius:8px;
          background:#e94560; color:#fff; cursor:pointer; margin:4px; }
 button.ghost { background:transparent; border:1px solid #8892a4; color:#8892a4; }
 select, input { font-size:1rem; padding:6px; border-radius:6px; }
 .pt { display:inline-block; padding:6px 12px; margin:3px; border-radius:16px;
       background:#0f3460; cursor:pointer; border:2px solid transparent; }
 .pt.done { background:#27ae60; }
 .pt.sel { border-color:#f5a623; }
 .imgs { display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; }
 .imgwrap { position:relative; }
 .imgwrap img { width:min(100%, 460px); border:2px solid #444; border-radius:6px; cursor:crosshair; display:block; }
 .imgwrap.clicked img { border-color:#27ae60; }
 .marker { position:absolute; width:14px; height:14px; margin:-7px; border:2px solid #f5a623;
           border-radius:50%; pointer-events:none; }
 #result { font-size:1.5rem; color:#f5a623; text-align:center; min-height:2em; }
 .muted { color:#8892a4; font-size:.9rem; }
</style></head><body>
<h1>🎯 Calibration des caméras</h1>

<div class="step">
 <b>1. Position approximative des caméras</b>
 <div class="muted">Pour chaque caméra : au-dessus de quel secteur est-elle fixée ? (précision sans importance)</div>
 <div id="initsel"></div>
</div>

<div class="step">
 <b>2. Points de calibration</b>
 <div class="muted">Choisis un point, plante une fléchette PILE dessus, capture, puis clique la POINTE dans chaque image.</div>
 <div id="pointlist"></div>
 <button onclick="capture()">📸 Capturer</button>
 <button class="ghost" onclick="resetPoint()">Refaire ce point</button>
 <div class="imgs" id="imgs"></div>
</div>

<div class="step">
 <b>3. Calcul</b>
 <button onclick="fit()">⚙️ Calculer la calibration</button>
 <div id="fitres" class="muted"></div>
</div>

<div class="step">
 <b>4. Test</b>
 <div class="muted">Plante une fléchette n'importe où, capture en mode test, clique la pointe dans chaque image.</div>
 <button onclick="startTest()">🧪 Capturer (test)</button>
 <div class="imgs" id="testimgs"></div>
 <div id="result"></div>
</div>

<script>
let CAMS = [], POINTS = [], CLICKS = {}, selected = null, testCols = {};

async function load() {
  const d = await (await fetch('/api/points')).json();
  CAMS = d.cams; POINTS = d.points; CLICKS = d.clicks;
  document.getElementById('initsel').innerHTML = CAMS.map(c =>
    `Caméra ${c} : <select id="init${c}">` +
    [20,1,18,4,13,6,10,15,2,17,3,19,7,16,8,11,14,9,12,5].map(s =>
      `<option value="${s}">${s}</option>`).join('') +
    `</select> &nbsp;`).join('');
  renderPoints();
}
function renderPoints() {
  document.getElementById('pointlist').innerHTML = POINTS.map(p => {
    const done = CLICKS[p.id] && Object.keys(CLICKS[p.id]).length === CAMS.length;
    return `<span class="pt ${done ? 'done':''} ${selected===p.id?'sel':''}"
      onclick="selected='${p.id}'; renderPoints()">${p.label}</span>`;
  }).join('');
}
async function capture() {
  if (!selected) { alert('Choisis d\\'abord un point !'); return; }
  const r = await (await fetch('/api/capture', {method:'POST'})).json();
  document.getElementById('imgs').innerHTML = CAMS.map(c => `
    <div class="imgwrap" id="w${c}">
      <div>Caméra ${c} — clique la pointe</div>
      <img src="/img/${c}?t=${r.t}" onclick="clickImg(event, ${c}, this)">
    </div>`).join('');
}
async function clickImg(ev, cam, img) {
  const rect = img.getBoundingClientRect();
  const scale = img.naturalWidth / rect.width;
  const u = (ev.clientX - rect.left) * scale, v = (ev.clientY - rect.top) * scale;
  await fetch('/api/click', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({point: selected, cam: cam, u: u, v: v})});
  const w = document.getElementById('w'+cam);
  w.classList.add('clicked');
  w.querySelectorAll('.marker').forEach(m => m.remove());
  const m = document.createElement('div');
  m.className = 'marker';
  m.style.left = (ev.clientX - rect.left) + 'px';
  m.style.top = (ev.clientY - rect.top + w.firstElementChild.offsetHeight) + 'px';
  w.appendChild(m);
  CLICKS[selected] = CLICKS[selected] || {};
  CLICKS[selected][cam] = [u, v];
  renderPoints();
}
async function resetPoint() {
  if (!selected) return;
  await fetch('/api/reset_point', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({point: selected})});
  delete CLICKS[selected];
  document.getElementById('imgs').innerHTML = '';
  renderPoints();
}
async function fit() {
  const init = {};
  CAMS.forEach(c => init[c] = document.getElementById('init'+c).value);
  const r = await (await fetch('/api/fit', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({init_sectors: init})})).json();
  if (r.error) { document.getElementById('fitres').textContent = 'ERREUR : ' + r.error; return; }
  document.getElementById('fitres').innerHTML = Object.entries(r.result).map(([c, v]) =>
    `Caméra ${c} : erreur résiduelle ${v.rms_px} px sur ${v.n_points} points`).join('<br>') +
    '<br><b>Calibration sauvegardée.</b> (viser < 3 px ; sinon refaire les points imprécis)';
}
async function startTest() {
  testCols = {};
  const r = await (await fetch('/api/capture', {method:'POST'})).json();
  document.getElementById('result').textContent = '';
  document.getElementById('testimgs').innerHTML = CAMS.map(c => `
    <div class="imgwrap" id="tw${c}">
      <div>Caméra ${c}</div>
      <img src="/img/${c}?t=${r.t}" onclick="testClick(event, ${c}, this)">
    </div>`).join('');
}
async function testClick(ev, cam, img) {
  const rect = img.getBoundingClientRect();
  testCols[cam] = (ev.clientX - rect.left) * img.naturalWidth / rect.width;
  document.getElementById('tw'+cam).classList.add('clicked');
  if (Object.keys(testCols).length >= 2) {
    const r = await (await fetch('/api/test', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({columns: testCols})})).json();
    if (r.error) { document.getElementById('result').textContent = r.error; return; }
    const t = r.throw;
    const label = t.zone === 'miss' ? 'MISS' : t.zone === 'bull' ? 'BULL (50)' :
      t.zone === 'outer_bull' ? '25' :
      (t.multiplier === 3 ? 'Triple ' : t.multiplier === 2 ? 'Double ' : '') + t.sector + ' (' + t.score + ' pts)';
    document.getElementById('result').innerHTML =
      label + `<div class="muted">position (${r.x}, ${r.y}) mm — cohérence des rayons : ${r.coherence_mm} mm</div>`;
  }
}
load();
</script></body></html>"""


if __name__ == "__main__":
    CAMERA_INDICES = [int(a) for a in sys.argv[1:]]
    if not CAMERA_INDICES:
        print("Usage : python calibration_server.py <index caméras>  (ex: 0 2 4)")
        sys.exit(1)
    for i in CAMERA_INDICES:
        threading.Thread(target=capture_loop, args=(i,), daemon=True).start()
    print(f"Caméras {CAMERA_INDICES} — http://0.0.0.0:5002")
    app.run(host="0.0.0.0", port=5002, threaded=True)
