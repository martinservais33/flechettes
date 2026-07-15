"""Étape 1 — Visualisation en direct des caméras.

Serveur web indépendant du jeu (port 5001) pour :
  - voir les 3 flux en direct (positionnement des caméras)
  - prendre des snapshots horodatés (calibration, vérifications)

Usage (sur le Pi) :
    flechette-env/bin/python vision/preview_server.py 0 2 4

puis ouvrir http://flechettes.local:5001 depuis le Mac.
Arrêter avec Ctrl+C. Ne pas laisser tourner en même temps que le futur
service de détection : les caméras ne peuvent être ouvertes qu'une fois.
"""

import os
import sys
import threading
import time
from datetime import datetime

import cv2
from flask import Flask, Response, jsonify

from test_cameras import open_camera

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")

app = Flask(__name__)

# Dernière frame de chaque caméra, alimentée par un thread par caméra
_frames = {}
_lock = threading.Lock()


def capture_loop(index):
    cap = open_camera(index)
    if cap is None:
        print(f"ERREUR : impossible d'ouvrir la caméra {index}")
        return
    while True:
        ok, frame = cap.read()
        if ok:
            with _lock:
                _frames[index] = frame
        else:
            time.sleep(0.05)


def mjpeg_generator(index):
    while True:
        with _lock:
            frame = _frames.get(index)
        if frame is None:
            time.sleep(0.1)
            continue
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n")
        time.sleep(1 / 15)  # 15 FPS suffisent pour la visualisation


@app.route("/")
def index():
    cams = "".join(
        f'<div class="cam"><h3>Caméra {i}</h3><img src="/stream/{i}"></div>'
        for i in CAMERA_INDICES
    )
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Caméras</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ background:#1a1a2e; color:#eee; font-family:sans-serif; text-align:center; padding:10px; }}
  .cams {{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; }}
  .cam img {{ max-width:100%; width:420px; border:2px solid #444; border-radius:8px; }}
  button {{ font-size:1.2rem; padding:12px 30px; margin:14px; border:none; border-radius:8px;
           background:#e94560; color:#fff; cursor:pointer; }}
  #msg {{ color:#f5a623; min-height:1.4em; }}
</style></head><body>
<h1>🎯 Caméras</h1>
<div class="cams">{cams}</div>
<button onclick="snap()">📸 Snapshot des 3 caméras</button>
<div id="msg"></div>
<script>
async function snap() {{
  const r = await fetch('/snapshot', {{method:'POST'}});
  const d = await r.json();
  document.getElementById('msg').textContent = 'Sauvé : ' + d.files.join(', ');
}}
</script></body></html>"""


@app.route("/stream/<int:index>")
def stream(index):
    return Response(mjpeg_generator(index), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/snapshot", methods=["POST"])
def snapshot():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = []
    with _lock:
        for i, frame in _frames.items():
            name = f"{stamp}_cam{i}.jpg"
            cv2.imwrite(os.path.join(SNAPSHOT_DIR, name), frame)
            files.append(name)
    return jsonify({"ok": True, "files": files})


if __name__ == "__main__":
    CAMERA_INDICES = [int(a) for a in sys.argv[1:]] or [0]
    for i in CAMERA_INDICES:
        threading.Thread(target=capture_loop, args=(i,), daemon=True).start()
    print(f"Caméras {CAMERA_INDICES} — http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, threaded=True)
