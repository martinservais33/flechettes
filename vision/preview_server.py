"""Étape 1 — Visualisation en direct des caméras.

Serveur web indépendant du jeu (port 5001).
"""

import os
import sys
import threading
import time
from datetime import datetime

import cv2
from flask import Flask, Response, jsonify

from test_cameras import (FPS, HEIGHT, NO_FRAME_TIMEOUT, REOPEN_SETTLE,
                          WIDTH, open_camera, resolve_device)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")

app = Flask(__name__)

# Dernière frame de chaque caméra
_frames = {}
_lock = threading.Lock()


def safe_open_camera(cam_input):
    """Ouvre la camera en imposant cv2.CAP_V4L2 et le format MJPG 640x480.

    Pour un index, on delegue a test_cameras.open_camera : meme resolution
    de peripherique (symlink stable /dev/dartcamN) et meme format que
    live_server. C est ce qui garantit que l apercu montre exactement les
    memes images que la detection -- sinon une ROI tracee ici ne
    correspondrait pas aux frames sur lesquelles la detection travaille.
    """
    if isinstance(cam_input, int) or str(cam_input).isdigit():
        return open_camera(int(cam_input))

    # Chemin explicite passe en argument (ex: /dev/video0) : meme reglages.
    cap = cv2.VideoCapture(str(cam_input), cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    return cap


def capture_loop(index):
    """Capture en continu, en reouvrant la camera tant que necessaire.

    Deux cas reels : une camera tout juste liberee par
    flechettes-vision.service met un instant a redevenir disponible, et une
    camera peut decrocher en cours de route. Sans reessai, le thread mourait
    a l ouverture et l apercu restait vide jusqu au relancement manuel.
    Meme logique que live_server.capture_loop.
    """
    cap = None
    derniere_frame = time.time()
    while True:
        if cap is None:
            cap = safe_open_camera(index)
            if cap is None:
                print(f"camera {index} pas encore prete - nouvel essai dans 2 s...")
                time.sleep(2)
                continue
            print(f"camera {index} ouverte")
            derniere_frame = time.time()

        ok, frame = cap.read()
        if ok and frame is not None:
            with _lock:
                _frames[index] = frame
            derniere_frame = time.time()
            continue

        # Chien de garde en TEMPS, pas en nombre d'essais : une camera qui
        # s'ouvre sans streamer fait bloquer read() jusqu'au timeout uvcvideo.
        if time.time() - derniere_frame > NO_FRAME_TIMEOUT:
            print(f"camera {index} : aucune frame depuis {NO_FRAME_TIMEOUT:.0f} s, reouverture...")
            try:
                cap.release()
            except Exception:
                pass
            cap = None
            time.sleep(REOPEN_SETTLE)
        else:
            time.sleep(0.05)

def mjpeg_generator(index):
    while True:
        with _lock:
            frame = _frames.get(index)
        if frame is None:
            time.sleep(0.1)
            continue
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            )
        time.sleep(1 / 15)


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
  body {{ background:#1a1a2e; color:#eee; font-family:sans-serif; text-align:center; margin:20px; }}
  .cams {{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; }}
  .cam img {{ max-width:100%; width:420px; border:2px solid #0f3460; border-radius:8px; }}
  button {{ font-size:1.2rem; padding:12px 30px; margin-top:20px; background:#e94560; color:#fff; border:none; border-radius:6px; cursor:pointer; }}
  #msg {{ color:#f5a623; min-height:1.4em; margin-top:10px; }}
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


@app.route("/stream/<path:index>")
def stream(index):
    # Convertir en int si possible pour la clé du dictionnaire
    key = int(index) if str(index).isdigit() else index
    return Response(mjpeg_generator(key), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/snapshot", methods=["POST"])
def snapshot():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = []
    with _lock:
        for i, frame in _frames.items():
            cam_label = str(i).replace("/", "_")
            name = f"{stamp}_cam{cam_label}.jpg"
            cv2.imwrite(os.path.join(SNAPSHOT_DIR, name), frame)
            files.append(name)
    return jsonify({"ok": True, "files": files})


if __name__ == "__main__":
    raw_args = sys.argv[1:]
    CAMERA_INDICES = []
    for arg in raw_args:
        CAMERA_INDICES.append(int(arg) if arg.isdigit() else arg)
    
    if not CAMERA_INDICES:
        CAMERA_INDICES = [0, 2, 4]

    for i in CAMERA_INDICES:
        threading.Thread(target=capture_loop, args=(i,), daemon=True).start()

    print(f"Caméras {CAMERA_INDICES} — http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, threaded=True)
