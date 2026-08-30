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
import math
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

from board import (R_BULL, R_DOUBLE_IN, R_DOUBLE_OUT, R_OUTER_BULL,
                   R_TRIPLE_IN, R_TRIPLE_OUT, SECTORS, score_from_point,
                   sector_center_angle)
from calib_model import fit_camera, ray_from_column, triangulate
from detector import (REMOVAL_DELTA, SETTLE_PIXELS, brightening,
                      changed_pixels, extract_impact, preprocess)
from test_cameras import (NO_FRAME_TIMEOUT,
                          REOPEN_SETTLE, open_camera)

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(HERE, "dataset")
DIAG_DIR = os.path.join(HERE, "dataset_diag")
KEEP_DIAG = 60     # instrumentation : mains et mouvements longs, pour caler
                   # MAX_THROW_TICKS et MAX_DART_AREA sur des mesures reelles
KEEP_EVENTS = 20   # fenêtre glissante : seuls les N derniers événements sont
                   # conservés sur disque (évite de saturer la carte SD)
CALIB = json.load(open(os.path.join(HERE, "calibration.json")))["cams"]
ROTATIONS = {int(k): v for k, v in json.load(open(os.path.join(HERE, "rotations.json"))).items()} \
    if os.path.exists(os.path.join(HERE, "rotations.json")) else {}

ROTATE_CODES = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}

SETTLE_TICKS = 5          # frames stables consécutives pour valider l'impact
MOTION_PIXELS = 200       # pixels changés vs référence pour déclencher.
                          # Mesuré le 2026-08-28 avec le cadre resserré sur la
                          # cible : bruit sur scène statique <= 3 px (273
                          # échantillons), plus faible lancer réel 373 px.
                          # À 800 (valeur calibrée sur l'ancien cadre, qui
                          # contenait la fléchette entière) 19 lancers sur 20
                          # ne déclenchaient plus. Ce seuil est solidaire de
                          # la taille de la ROI : le rebaisser si on la réduit
                          # encore, le remonter si on l'élargit.
MAX_THROW_TICKS = 5       # frames instables max pour un vrai lancer :
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

# ------------------------------------------------------------------
# Calibration par lancers libres
# ------------------------------------------------------------------
# On lance des fléchettes normalement : la détection fournit les colonnes u
# de chaque caméra, et l'utilisateur clique la position réelle de la pointe
# sur une cible dessinée, ce qui donne le (x, y). Chaque échantillon est donc
# une paire ((x, y) mm, u px) par caméra — exactement ce qu'attend fit_camera.
CALIB_FILE = os.path.join(HERE, "calibration.json")
CALIB_BACKUP_FILE = os.path.join(HERE, "calibration.backup.json")
SAMPLES_FILE = os.path.join(HERE, "calib_samples.json")

MIN_FIT_SAMPLES = 12   # en deçà, le solveur (5 paramètres/caméra) part dans un
                       # minimum parasite : on n'ajuste pas du tout
MIN_PER_CAM = 8        # observations minimales pour ajuster une caméra donnée
MIN_APPLY_SAMPLES = 20 # en deçà, on refuse d'écraser la calibration active
ROLLING_WINDOW = 10    # erreurs hors-échantillon moyennées pour le critère d'arrêt

calib = {
    "on": False,
    "samples": [],   # {x, y, tips:{cam:[u,v]}, err_mm, stamp, time}
    "fit": None,     # même forme que calibration.json : {"cams": {...}, "date": ...}
    "fitting": False,
    "error": None,   # message du dernier fit en échec
}
_calib_lock = threading.Lock()

if os.path.exists(SAMPLES_FILE):
    try:
        calib["samples"] = json.load(open(SAMPLES_FILE))
    except (ValueError, OSError):
        calib["samples"] = []


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
    cap = None
    derniere_frame = time.time()
    while True:
        if cap is None:
            cap = open_camera(index)
            if cap is None:
                print(f"camera {index} pas encore prete - nouvel essai dans 2 s...")
                time.sleep(2)
                continue
            print(f"camera {index} ouverte")
            derniere_frame = time.time()

        ok, frame = cap.read()
        if ok and frame is not None:
            with _lock:
                _frames[index] = upright(frame, index)
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


# ------------------------------------------------------------------
# Calibration par lancers libres — ajustement du modèle
# ------------------------------------------------------------------
def calib_cams():
    """Caméras concernées par la calibration (celles déjà connues du modèle)."""
    return sorted(CALIB.keys(), key=int)


def calib_obs(samples, cam):
    """Observations ((x, y) mm, u px) d'une caméra, au format de fit_camera."""
    return [((s["x"], s["y"]), s["tips"][cam][0])
            for s in samples if cam in s["tips"]]


def calib_fit(samples, warm=None):
    """Ajuste les 3 caméras sur les échantillons.

    warm : fit précédent servant de point de départ (démarrage à chaud). À
    défaut on part de la calibration active, bien meilleure que le gabarit
    générique — le solveur est local, le point de départ compte.
    Retourne (fit, erreur) ; fit vaut None si les données sont insuffisantes.
    """
    if len(samples) < MIN_FIT_SAMPLES:
        return None, f"{len(samples)} échantillons, minimum {MIN_FIT_SAMPLES}"

    cams = {}
    for cam in calib_cams():
        obs = calib_obs(samples, cam)
        if len(obs) < MIN_PER_CAM:
            return None, f"caméra {cam} : {len(obs)} observations, minimum {MIN_PER_CAM}"

        init = ((warm or {}).get("cams", {}).get(cam) or CALIB[cam])["params"]
        params, rms = fit_camera(obs, list(init))
        cams[cam] = {"params": params, "rms_px": round(rms, 2),
                     "n_points": len(obs), "rotation": ROTATIONS.get(int(cam), 0)}

    return {"cams": cams, "date": time.strftime("%Y-%m-%d %H:%M")}, None


def calib_position(tips, fit):
    """Position (x, y) en mm d'un impact selon un fit donné, ou None."""
    if not fit:
        return None
    rays = [ray_from_column(fit["cams"][c]["params"], uv[0])
            for c, uv in tips.items() if c in fit["cams"]]
    if len(rays) < 2:
        return None
    (x, y), _ = triangulate(rays)
    return x, y


def calib_holdout_error(tips, x, y):
    """Erreur de prédiction (mm) sur un point JAMAIS vu par le modèle courant.

    C'est le seul critère d'arrêt honnête : le RMS du fit, lui, tend vers 0
    dès qu'on approche de 5 points par caméra (autant de paramètres) sans que
    la calibration soit bonne pour autant.
    """
    pos = calib_position(tips, calib["fit"])
    if pos is None:
        return None
    return round(math.hypot(pos[0] - x, pos[1] - y), 1)


def calib_save_samples():
    json.dump(calib["samples"], open(SAMPLES_FILE, "w"), indent=2)


def calib_refit():
    """Réajuste en tâche de fond (0,1-0,5 s) pour ne pas bloquer Flask."""
    def run():
        samples = list(calib["samples"])
        fit, err = calib_fit(samples, warm=calib["fit"])
        with _calib_lock:
            if fit:
                calib["fit"] = fit
            calib["error"] = err
            calib["fitting"] = False

    with _calib_lock:
        if calib["fitting"]:
            return
        calib["fitting"] = True
    threading.Thread(target=run, daemon=True).start()


def calib_rolling_error():
    """Moyenne des dernières erreurs hors-échantillon (critère d'arrêt)."""
    errs = [s["err_mm"] for s in calib["samples"] if s.get("err_mm") is not None]
    if not errs:
        return None
    window = errs[-ROLLING_WINDOW:]
    return round(sum(window) / len(window), 1)


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


def prune_diag(keep=KEEP_DIAG):
    try:
        folders = sorted(d for d in os.listdir(DIAG_DIR)
                         if os.path.isdir(os.path.join(DIAG_DIR, d)))
    except FileNotFoundError:
        return
    for old in folders[:-keep]:
        shutil.rmtree(os.path.join(DIAG_DIR, old), ignore_errors=True)


def save_diag(kind, ref_frames, frames, ref_grays, grays, per_cam_ref,
              unstable_ticks, results=None):
    """Instrumentation : enregistre ce qui n est PAS retenu comme lancer.

    Le dataset des lancers ne garde que les evenements classes "dart" :
    on n a donc aucune mesure des mains et des mouvements longs, alors que
    ce sont eux qui doivent fixer MAX_THROW_TICKS et MAX_DART_AREA. On
    ecrit dans dataset_diag/, separe pour ne pas evincer les lancers.
    """
    if results is None:
        results = {c: extract_impact(ref_grays[c], grays[c], surface_lines.get(c))
                   for c in grays}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_") + kind
    folder = os.path.join(DIAG_DIR, stamp)
    os.makedirs(folder, exist_ok=True)
    for c in CAMERA_INDICES:
        if c in ref_frames:
            cv2.imwrite(os.path.join(folder, f"cam{c}_before.jpg"), ref_frames[c])
        if c in frames:
            cv2.imwrite(os.path.join(folder, f"cam{c}_after.jpg"), frames[c])
    meta = {
        "kind": kind,
        "stamp": stamp,
        "time": datetime.now().strftime("%H:%M:%S"),
        "unstable_ticks": unstable_ticks,
        "changed_px": {str(c): per_cam_ref[c] for c in per_cam_ref},
        "areas": {str(c): r[2] for c, r in results.items()},
        "kinds": {str(c): r[0] for c, r in results.items()},
    }
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    prune_diag()


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

        per_cam_ref = {c: changed_pixels(grays[c], ref_grays[c]) for c in grays}
        inter = max(changed_pixels(grays[c], prev_grays[c]) for c in grays)
        vs_ref = max(per_cam_ref.values())
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
                save_diag("long_movement", ref_frames, frames, ref_grays,
                          grays, per_cam_ref, unstable_ticks)
                if state["game_mode"] and 0 < state["turn_darts"] < 3:
                    game_post("/end_turn")
                state["turn_darts"] = 0
                state["phase"] = "attente"
                ref_frames, ref_grays = frames, grays
                continue

            # Retrait ou pose ? Décidé sur la MOYENNE des caméras : le signe
            # du changement d'intensité sépare les deux (voir brightening()),
            # mais une caméra isolée peut se tromper de signe sur un vrai
            # lancer — on ne laisse donc pas une seule voix trancher.
            deltas = [b for b in (brightening(ref_grays[c], grays[c]) for c in grays)
                      if b is not None]
            if deltas and sum(deltas) / len(deltas) > REMOVAL_DELTA:
                save_diag("removal", ref_frames, frames, ref_grays, grays,
                          per_cam_ref, unstable_ticks)
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
                save_diag("clear", ref_frames, frames, ref_grays, grays,
                          per_cam_ref, unstable_ticks, results)
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
                    "kinds": {str(c): r[0] for c, r in results.items()},
                    "changed_px": {str(c): per_cam_ref[c] for c in per_cam_ref},
                    "unstable_ticks": unstable_ticks,
                    "prediction": pred,
                    "truth": None,
                }
                # Envoi au jeu (si mode jeu actif et lancer fiable)
                if state["game_mode"] and pred:
                    if pred.get("doubtful"):
                        event["sent"] = "non envoyé (douteux)"
                    else:
                        # on joint les coordonnées de l'impact (mm) pour la
                        # cible de précision côté jeu
                        res = game_post("/throw", {**pred["throw"],
                                                   "x": pred["x"], "y": pred["y"]})
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


# ------------------------------------------------------------------
# API calibration par lancers libres
# ------------------------------------------------------------------
@app.route("/api/calib/mode", methods=["POST"])
def api_calib_mode():
    calib["on"] = bool(request.json.get("on"))
    if calib["on"]:
        # pendant la calibration, aucun lancer ne part vers le jeu
        state["game_mode"] = False
        state["turn_darts"] = 0
        calib_refit()
    return jsonify({"ok": True, "on": calib["on"]})


@app.route("/api/calib/state")
def api_calib_state():
    per_cam = {c: len(calib_obs(calib["samples"], c)) for c in calib_cams()}
    fit = calib["fit"]
    return jsonify({
        "on": calib["on"],
        "n": len(calib["samples"]),
        "per_cam": per_cam,
        "rolling_mm": calib_rolling_error(),
        "rms_px": {c: fit["cams"][c]["rms_px"] for c in fit["cams"]} if fit else None,
        "fitting": calib["fitting"],
        "error": calib["error"],
        "has_fit": fit is not None,
        "can_apply": fit is not None and len(calib["samples"]) >= MIN_APPLY_SAMPLES,
        "min_apply": MIN_APPLY_SAMPLES,
        "min_fit": MIN_FIT_SAMPLES,
        "samples": [
            {"i": i, "x": s["x"], "y": s["y"], "err_mm": s.get("err_mm"),
             "time": s.get("time"), "event_id": s.get("event_id"),
             "cams": sorted(s["tips"].keys())}
            for i, s in enumerate(calib["samples"])
        ],
    })


@app.route("/api/calib/predict/<int:event_id>")
def api_calib_predict(event_id):
    """Position prédite par le modèle EN COURS d'ajustement (point fantôme).

    Différente de event["prediction"], qui utilise la calibration active.
    C'est elle qui montre la convergence : quand le fantôme tombe sur la
    pointe réelle, la calibration est bonne.
    """
    event = next((e for e in state["events"] if e["id"] == event_id), None)
    if event is None:
        return jsonify({"error": "événement inconnu"}), 404
    # tant qu'aucun ajustement n'existe (début de session, ou contrôle après
    # application), c'est la calibration active qu'on veut voir à l'œuvre.
    model = calib["fit"] or {"cams": CALIB}
    pos = calib_position(event["tips"], model)
    if not pos:
        return jsonify({})
    return jsonify({"x": round(pos[0], 1), "y": round(pos[1], 1),
                    "source": "modèle en cours" if calib["fit"] else "calibration active"})


_manual_ref = {}   # référence figée pour la capture d'une fléchette posée


@app.route("/api/calib/manual_ref", methods=["POST"])
def api_calib_manual_ref():
    """Fige la cible VIDE comme référence, avant de poser une fléchette à la main."""
    frames, grays = grab_grays()
    if len(grays) < 2:
        return jsonify({"error": "pas assez de caméras"}), 500
    _manual_ref.clear()
    _manual_ref.update({"grays": grays, "frames": frames})
    return jsonify({"ok": True, "cams": sorted(grays)})


@app.route("/api/calib/manual_capture", methods=["POST"])
def api_calib_manual_capture():
    """Détecte une fléchette POSÉE À LA MAIN.

    La boucle de détection ne peut pas le faire : elle écarte tout mouvement
    durant plus de MAX_THROW_TICKS frames (~0,5 s) comme étant une main à la
    cible, et une pose manuelle dépasse toujours ce seuil. On court-circuite
    donc sa machine à états et on applique extract_impact directement entre la
    référence et l'image courante — même détecteur, comme l'outil 11 points.

    Indispensable au contrôle final : poser une fléchette sur un repère exact
    (bull, coin de case) est la seule façon de détecter un biais systématique
    de clic, que l'erreur glissante ne peut pas voir.
    """
    if not _manual_ref:
        return jsonify({"error": "prends d'abord la référence, cible vide"}), 400

    frames, grays = grab_grays()
    ref_grays, ref_frames = _manual_ref["grays"], _manual_ref["frames"]
    results = {c: extract_impact(ref_grays[c], grays[c], surface_lines.get(c))
               for c in grays if c in ref_grays}
    tips = {c: r[1] for c, r in results.items() if r[0] == "dart"}
    if len(tips) < 2:
        return jsonify({"error": "fléchette vue par moins de 2 caméras",
                        "kinds": {str(c): r[0] for c, r in results.items()}}), 400

    event = {
        "id": state["next_id"],
        "stamp": datetime.now().strftime("%Y%m%d_%H%M%S_") + str(state["next_id"]),
        "time": datetime.now().strftime("%H:%M:%S"),
        "tips": {str(c): list(t) for c, t in tips.items()},
        "areas": {str(c): r[2] for c, r in results.items()},
        "prediction": predict(tips),
        "truth": None,
        "manual": True,
    }
    state["next_id"] += 1
    save_event(event, ref_frames, frames, tips)
    state["events"].insert(0, event)
    del state["events"][KEEP_EVENTS:]
    return jsonify({"ok": True, "id": event["id"]})


@app.route("/api/calib/point", methods=["POST"])
def api_calib_point():
    """Attache la position réelle (cliquée sur la cible) à un impact détecté."""
    d = request.json
    event = next((e for e in state["events"] if e["id"] == d["id"]), None)
    if event is None:
        return jsonify({"error": "événement inconnu"}), 404
    if any(s.get("event_id") == event["id"] for s in calib["samples"]):
        return jsonify({"error": "impact déjà utilisé"}), 400

    x, y = float(d["x"]), float(d["y"])
    # mesuré AVANT d'ajouter l'échantillon : le point n'a donc jamais servi
    # à l'ajustement, l'erreur est honnête (hors-échantillon).
    err = calib_holdout_error(event["tips"], x, y)

    calib["samples"].append({
        "event_id": event["id"], "stamp": event["stamp"], "time": event["time"],
        "x": x, "y": y, "tips": event["tips"], "err_mm": err,
    })
    calib_save_samples()
    calib_refit()
    return jsonify({"ok": True, "err_mm": err, "n": len(calib["samples"])})


@app.route("/api/calib/delete", methods=["POST"])
def api_calib_delete():
    i = int(request.json["i"])
    if not 0 <= i < len(calib["samples"]):
        return jsonify({"error": "échantillon inconnu"}), 404
    calib["samples"].pop(i)
    calib_save_samples()
    calib_refit()
    return jsonify({"ok": True, "n": len(calib["samples"])})


@app.route("/api/calib/apply", methods=["POST"])
def api_calib_apply():
    """Remplace la calibration active. Sauvegarde l'ancienne d'abord."""
    global CALIB
    fit = calib["fit"]
    if not fit:
        return jsonify({"error": "pas encore de modèle ajusté"}), 400
    if len(calib["samples"]) < MIN_APPLY_SAMPLES:
        return jsonify({"error": f"minimum {MIN_APPLY_SAMPLES} échantillons"}), 400

    if os.path.exists(CALIB_FILE):
        shutil.copyfile(CALIB_FILE, CALIB_BACKUP_FILE)
    json.dump(fit, open(CALIB_FILE, "w"), indent=2)
    CALIB = fit["cams"]      # prise d'effet immédiate, sans redémarrer le service
    return jsonify({"ok": True, "backup": os.path.basename(CALIB_BACKUP_FILE)})


@app.route("/calib/board.svg")
def calib_board_svg():
    """Cible dessinée à partir des constantes de board.py.

    Toute la méthode repose sur la précision du clic : le dessin doit donc
    correspondre exactement à la géométrie qui sert à scorer. On le génère
    depuis les mêmes constantes plutôt que de recopier des rayons à la main.

    Repère : viewBox en mm réels, origine au centre, y inversé pour l'écran.
    La conversion clic -> mm est donc x = svgX, y = -svgY.
    """
    def pt(r, deg):
        a = math.radians(deg)
        return r * math.cos(a), -r * math.sin(a)   # y inversé (écran vers le bas)

    def wedge(r1, r2, a1, a2):
        """Secteur d'anneau entre deux rayons et deux angles (degrés)."""
        x1, y1 = pt(r1, a1)
        x2, y2 = pt(r2, a1)
        x3, y3 = pt(r2, a2)
        x4, y4 = pt(r1, a2)
        # y étant inversé, un angle croissant tourne dans le sens anti-horaire
        # à l'écran : sweep-flag 0 à l'aller, 1 au retour.
        return (f"M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f} "
                f"A{r2:.2f},{r2:.2f} 0 0,0 {x3:.2f},{y3:.2f} "
                f"L{x4:.2f},{y4:.2f} "
                f"A{r1:.2f},{r1:.2f} 0 0,1 {x1:.2f},{y1:.2f} Z")

    BLACK, CREAM = "#241c14", "#e6d9b8"
    RED, GREEN = "#b5342a", "#1c7a45"
    WIRE = "#9aa0aa"

    parts = [f'<circle cx="0" cy="0" r="{R_DOUBLE_OUT:.2f}" fill="#0f0d18"/>']
    for i, sector in enumerate(SECTORS):
        centre = sector_center_angle(sector)
        a1, a2 = centre - 9, centre + 9
        single, ring = (BLACK, RED) if i % 2 == 0 else (CREAM, GREEN)
        # simples (intérieur et extérieur), puis triple et double
        parts.append(f'<path d="{wedge(R_OUTER_BULL, R_TRIPLE_IN, a1, a2)}" fill="{single}"/>')
        parts.append(f'<path d="{wedge(R_TRIPLE_OUT, R_DOUBLE_IN, a1, a2)}" fill="{single}"/>')
        parts.append(f'<path d="{wedge(R_TRIPLE_IN, R_TRIPLE_OUT, a1, a2)}" fill="{ring}"/>')
        parts.append(f'<path d="{wedge(R_DOUBLE_IN, R_DOUBLE_OUT, a1, a2)}" fill="{ring}"/>')

    # fils : rayons et cercles — ce sont les repères visuels du clic
    for sector in SECTORS:
        a = sector_center_angle(sector) + 9
        xa, ya = pt(R_OUTER_BULL, a)
        xb, yb = pt(R_DOUBLE_OUT, a)
        parts.append(f'<line x1="{xa:.2f}" y1="{ya:.2f}" x2="{xb:.2f}" y2="{yb:.2f}" '
                     f'stroke="{WIRE}" stroke-width="0.8"/>')
    for r in (R_BULL, R_OUTER_BULL, R_TRIPLE_IN, R_TRIPLE_OUT, R_DOUBLE_IN, R_DOUBLE_OUT):
        parts.append(f'<circle cx="0" cy="0" r="{r:.2f}" fill="none" '
                     f'stroke="{WIRE}" stroke-width="0.8"/>')

    parts.append(f'<circle cx="0" cy="0" r="{R_OUTER_BULL:.2f}" fill="{GREEN}"/>')
    parts.append(f'<circle cx="0" cy="0" r="{R_BULL:.2f}" fill="{RED}"/>')

    for sector in SECTORS:
        tx, ty = pt(R_DOUBLE_OUT + 12, sector_center_angle(sector))
        parts.append(f'<text x="{tx:.2f}" y="{ty:.2f}" fill="#e8e8f0" font-size="14" '
                     f'text-anchor="middle" dominant-baseline="central" '
                     f'font-family="sans-serif">{sector}</text>')

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-190 -190 380 380">'
           + "".join(parts) + "</svg>")
    return Response(svg, mimetype="image/svg+xml")


@app.route("/calib/zoom/<stamp>/<int:cam>")
def calib_zoom(stamp, cam):
    """Vue agrandie autour de la pointe détectée.

    À 200 px de large, le marqueur est trop petit pour distinguer une vraie
    pointe d'une ombre. On recadre donc autour du point détecté et on
    agrandit : à cette échelle, la différence est immédiate.
    """
    folder = os.path.join(DATASET_DIR, stamp)
    meta_path = os.path.join(folder, "meta.json")
    img_path = os.path.join(folder, f"cam{cam}_after.jpg")
    if not (os.path.exists(meta_path) and os.path.exists(img_path)):
        return "absent", 404

    try:
        tips = json.load(open(meta_path)).get("tips", {})
    except (ValueError, OSError):
        return "méta illisible", 404      # écriture interrompue, copie partielle…
    if str(cam) not in tips:
        return "pas de pointe pour cette caméra", 404
    u, v = tips[str(cam)]

    img = cv2.imread(img_path)
    if img is None:
        return "image illisible", 404
    h, w = img.shape[:2]

    half, scale = 80, 3
    # fenêtre ramenée dans l'image : près d'un bord elle se décale au lieu
    # d'être tronquée, la pointe n'est alors plus au centre du recadrage.
    x1 = max(0, min(int(u) - half, w - 2 * half))
    y1 = max(0, min(int(v) - half, h - 2 * half))
    x2, y2 = min(w, x1 + 2 * half), min(h, y1 + 2 * half)

    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_NEAREST)
    cx, cy = int((u - x1) * scale), int((v - y1) * scale)
    cv2.line(crop, (cx - 26, cy), (cx - 7, cy), (0, 165, 255), 1)
    cv2.line(crop, (cx + 7, cy), (cx + 26, cy), (0, 165, 255), 1)
    cv2.line(crop, (cx, cy - 26), (cx, cy - 7), (0, 165, 255), 1)
    cv2.line(crop, (cx, cy + 7), (cx, cy + 26), (0, 165, 255), 1)
    cv2.circle(crop, (cx, cy), 6, (0, 165, 255), 1)

    _, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return Response(jpg.tobytes(), mimetype="image/jpeg")


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


@app.route("/calib")
def calib_page():
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Calibration par lancers</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body { background:#1a1a2e; color:#eee; font-family:sans-serif; padding:12px; max-width:900px; margin:auto; }
 h1 { color:#e94560; text-align:center; margin-bottom:4px; }
 a { color:#4de3ff; }
 .ev { background:#16213e; border-radius:10px; padding:12px; margin:10px 0; }
 .muted { color:#8892a4; font-size:.85rem; }
 button { font-size:1rem; padding:8px 16px; border:none; border-radius:6px;
          background:#e94560; color:#fff; cursor:pointer; }
 button.ghost { background:#2c3e63; }
 button:disabled { background:#2c3e63; color:#6b7793; cursor:not-allowed; }
 .imgs { display:flex; flex-wrap:wrap; gap:8px; }
 .imgs figure { margin:0; }
 .imgs img.full { width:260px; border-radius:6px; display:block; }
 .imgs img.zoom { width:260px; border-radius:6px; display:block; image-rendering:pixelated; }
 .imgs figcaption { color:#8892a4; font-size:.75rem; text-align:center; }
 .board-wrap { position:relative; width:100%; max-width:600px; margin:0 auto; cursor:crosshair; }
 .board-wrap img { width:100%; display:block; border-radius:8px; }
 .board-wrap svg { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
 #lens { position:absolute; width:150px; height:150px; border-radius:50%; display:none;
         border:2px solid #4de3ff; background-repeat:no-repeat; pointer-events:none;
         box-shadow:0 0 12px #000; }
 #lens::after { content:''; position:absolute; left:50%; top:50%; width:11px; height:11px;
                margin:-6px 0 0 -6px; border:1px solid #4de3ff; border-radius:50%; }
 .band { position:sticky; bottom:0; background:#0f1626; border-top:2px solid #e94560;
         padding:10px; display:flex; flex-wrap:wrap; gap:14px; align-items:center;
         justify-content:center; border-radius:8px 8px 0 0; }
 .band b { color:#f5a623; }
 .good { color:#27ae60; } .warn { color:#f5a623; } .bad { color:#e94560; }
 table { width:100%; border-collapse:collapse; font-size:.85rem; }
 td, th { padding:4px 6px; text-align:left; border-bottom:1px solid #22304f; }
 td button { padding:2px 8px; font-size:.75rem; }
</style></head><body>
<h1>🎯 Calibration par lancers</h1>
<div class="muted" style="text-align:center">
  <a href="/">← retour à la détection live</a>
</div>

<div class="ev">
 <label style="cursor:pointer"><input type="checkbox" id="mode" onchange="setMode()">
 <b>Activer le mode calibration</b></label>
 <div class="muted">Lance des fléchettes normalement. Pour chaque impact détecté :
 vérifie d'abord sur les vues zoomées que le repère est bien sur la POINTE (et pas sur une
 ombre), puis — seulement si tu peux situer la fléchette précisément à l'œil (près d'un fil,
 d'un bord d'anneau ou du bull) — clique sa position exacte sur la cible. Sinon, « Ignorer ».
 <br>Le mode jeu est coupé automatiquement : rien n'est envoyé au jeu pendant la calibration.</div>
</div>

<div class="ev">
 <b>🔎 Contrôle : fléchette posée à la main</b>
 <div class="muted">La détection automatique ignore les poses manuelles (elle les prend pour
 une main devant la cible). Utilise ces deux boutons pour poser une fléchette sur un repère
 EXACT — bull, coin de case — et vérifier ce que le modèle en cours prédit. C'est le seul
 moyen de repérer un biais systématique de clic : l'erreur glissante, elle, ne le voit pas.
 <br>1. cible vide → « Référence ». 2. pose la fléchette, écarte-toi → « Capturer ».</div>
 <div style="margin-top:8px">
   <button class="ghost" onclick="manualRef()">1. Référence (cible vide)</button>
   <button class="ghost" onclick="manualCapture()">2. Capturer la fléchette posée</button>
   <span id="manualinfo" class="muted"></span>
 </div>
</div>

<div id="pending"></div>

<div class="ev">
 <div class="board-wrap" id="wrap">
   <img id="board" src="/calib/board.svg" alt="cible">
   <svg id="overlay" viewBox="-190 -190 380 380"></svg>
   <div id="lens"></div>
 </div>
 <div class="muted" style="text-align:center" id="hint"></div>
</div>

<div class="ev">
 <b>Échantillons</b> <span class="muted" id="scount"></span>
 <table id="stable"></table>
</div>

<div class="band">
 <span>Points : <b id="n">0</b></span>
 <span class="muted" id="percam"></span>
 <span>Erreur (10 derniers) : <b id="roll">—</b></span>
 <span class="muted" id="rms"></span>
 <button id="apply" onclick="apply()" disabled>Appliquer</button>
</div>

<script>
const VB = 380, HALF = 190;          // viewBox du SVG, en mm
let pending = null;                  // impact en attente de validation
let ignored = new Set();
let used = new Set();
let ghost = null;

async function j(url, body) {
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'},
                      body: JSON.stringify(body)} : {};
  return (await fetch(url, opt)).json();
}

async function setMode() {
  await j('/api/calib/mode', {on: document.getElementById('mode').checked});
  refresh();
}

async function refresh() {
  const [st, cal] = await Promise.all([j('/api/status'), j('/api/calib/state')]);
  document.getElementById('mode').checked = cal.on;

  cal.samples.forEach(s => { if (s.event_id != null) used.add(s.event_id); });

  // impact en attente = le plus récent ni utilisé ni ignoré
  const next = st.events.find(e => !used.has(e.id) && !ignored.has(e.id)
                                   && Object.keys(e.tips).length >= 2);
  if (!pending || !next || pending.id !== next.id) {
    pending = next || null;
    ghost = null;
    if (pending) {
      const p = await j('/api/calib/predict/' + pending.id);
      ghost = (p && p.x != null) ? p : null;
    }
  }
  renderPending();
  renderBand(cal);
  renderSamples(cal);
  drawOverlay(cal);
}

function renderPending() {
  const box = document.getElementById('pending');
  if (!pending) {
    box.innerHTML = `<div class="ev muted">En attente d'un impact…
      ${document.getElementById('mode').checked ? 'Lance une fléchette.'
        : 'Active le mode calibration ci-dessus.'}</div>`;
    document.getElementById('hint').textContent = '';
    return;
  }
  const pr = pending.prediction;
  const coh = pr ? `cohérence ${pr.coherence_mm} mm` : 'triangulation impossible';
  const suspect = pr && pr.doubtful;
  const imgs = Object.keys(pending.tips).sort().map(c => `
    <figure>
      <img class="zoom" src="/calib/zoom/${pending.stamp}/${c}">
      <figcaption>caméra ${c} — zoom sur la pointe</figcaption>
    </figure>`).join('') +
    Object.keys(pending.tips).sort().map(c => `
    <figure>
      <img class="full" src="/event_img/${pending.stamp}/${c}">
      <figcaption>caméra ${c} — vue complète</figcaption>
    </figure>`).join('');
  box.innerHTML = `<div class="ev">
    <b>Impact #${pending.id}</b> <span class="muted">à ${pending.time} — ${coh}</span>
    ${pending.manual ? '<span class="warn"> · posée à la main</span>' : ''}
    ${suspect ? '<div class="bad">⚠ Cohérence faible : une caméra a probablement accroché une ombre. À ignorer sauf si les zooms sont nets.</div>' : ''}
    <div class="muted">Le repère orange doit être sur la pointe de la fléchette.</div>
    <div class="imgs">${imgs}</div>
    <button class="ghost" onclick="skip()">Ignorer cet impact</button>
  </div>`;

  const hint = document.getElementById('hint');
  if (ghost) {
    const r = Math.hypot(ghost.x, ghost.y).toFixed(1);
    hint.innerHTML = `${ghost.source || 'Modèle'} → <b>x ${ghost.x} mm, y ${ghost.y} mm</b>`
      + ` <span class="muted">(${r} mm du centre)</span>`
      + `<br><span class="muted">Cercle bleu sur la cible. Pour un contrôle : pose la fléchette`
      + ` au bull, la prédiction doit être proche de 0, 0.</span>`
      + `<br>Clique la position réelle de la pointe pour l'ajouter aux échantillons.`;
  } else {
    hint.textContent = 'Clique la position exacte de la pointe sur la cible.';
  }
}

function renderBand(cal) {
  document.getElementById('n').textContent = cal.n;
  document.getElementById('percam').textContent =
    'par caméra : ' + Object.entries(cal.per_cam).map(([c, v]) => `${c}:${v}`).join('  ');
  const r = document.getElementById('roll');
  if (cal.rolling_mm == null) {
    r.textContent = '—'; r.className = '';
  } else {
    r.textContent = cal.rolling_mm + ' mm';
    r.className = cal.rolling_mm < 3 ? 'good' : (cal.rolling_mm < 6 ? 'warn' : 'bad');
  }
  document.getElementById('rms').textContent = cal.rms_px
    ? 'RMS ' + Object.values(cal.rms_px).map(v => v.toFixed(1)).join('/') + ' px (indicatif — ne pas s\\'y fier)'
    : (cal.error ? cal.error : '');
  const b = document.getElementById('apply');
  b.disabled = !cal.can_apply;
  b.textContent = cal.can_apply ? 'Appliquer' : `Appliquer (${cal.n}/${cal.min_apply})`;
}

function renderSamples(cal) {
  document.getElementById('scount').textContent =
    cal.n ? `— supprime un point si son erreur est aberrante` : '';
  document.getElementById('stable').innerHTML = cal.n ? `
    <tr><th>#</th><th>position (mm)</th><th>erreur</th><th>caméras</th><th></th></tr>` +
    cal.samples.slice().reverse().map(s => `<tr>
      <td>${s.i + 1}</td>
      <td>${s.x.toFixed(0)}, ${s.y.toFixed(0)}</td>
      <td class="${s.err_mm == null ? '' : (s.err_mm < 3 ? 'good' : (s.err_mm < 6 ? 'warn' : 'bad'))}">
        ${s.err_mm == null ? '—' : s.err_mm + ' mm'}</td>
      <td>${s.cams.join(' ')}</td>
      <td><button class="ghost" onclick="del(${s.i})">suppr</button></td>
    </tr>`).join('') : '';
}

function drawOverlay(cal) {
  const dots = cal.samples.map(s =>
    `<circle cx="${s.x.toFixed(1)}" cy="${(-s.y).toFixed(1)}" r="2.5"
             fill="#4de3ff" fill-opacity=".5" stroke="#06202e" stroke-width=".6"/>`).join('');
  const g = ghost
    ? `<circle cx="${ghost.x}" cy="${-ghost.y}" r="5" fill="none"
               stroke="#4de3ff" stroke-width="1.6" stroke-dasharray="3 2"/>`
    : '';
  document.getElementById('overlay').innerHTML = dots + g;
}

async function manualRef() {
  const info = document.getElementById('manualinfo');
  const r = await j('/api/calib/manual_ref', {});
  info.textContent = r.error ? ('⚠ ' + r.error)
    : `référence prise (caméras ${r.cams.join(', ')}) — pose la fléchette`;
}

async function manualCapture() {
  const info = document.getElementById('manualinfo');
  const r = await j('/api/calib/manual_capture', {});
  if (r.error) {
    info.textContent = '⚠ ' + r.error
      + (r.kinds ? ' (' + Object.entries(r.kinds).map(([c, k]) => c + ':' + k).join(' ') + ')' : '');
    return;
  }
  info.textContent = 'fléchette capturée';
  ignored.delete(r.id);
  await refresh();
}

function skip() { if (pending) { ignored.add(pending.id); pending = null; refresh(); } }
async function del(i) { await j('/api/calib/delete', {i}); refresh(); }

async function apply() {
  if (!confirm('Remplacer la calibration active ?\\n\\n' +
               'L\\'ancienne est sauvegardée dans calibration.backup.json.')) return;
  const r = await j('/api/calib/apply', {});
  alert(r.error ? ('Erreur : ' + r.error)
                : 'Calibration appliquée. Sauvegarde : ' + r.backup);
  refresh();
}

// ---- clic sur la cible ----
function toMm(e) {
  const r = document.getElementById('board').getBoundingClientRect();
  return { x: (e.clientX - r.left) / r.width * VB - HALF,
           y: -((e.clientY - r.top) / r.height * VB - HALF) };
}

document.getElementById('wrap').addEventListener('click', async e => {
  if (!pending) { alert('Aucun impact en attente.'); return; }
  const p = toMm(e);
  if (Math.hypot(p.x, p.y) > 175) return;      // hors cible : clic ignoré
  const r = await j('/api/calib/point', {id: pending.id, x: +p.x.toFixed(1), y: +p.y.toFixed(1)});
  if (r.error) { alert(r.error); return; }
  used.add(pending.id);
  pending = null;
  refresh();
});

// ---- loupe : le clic doit être précis au millimètre ----
const wrap = document.getElementById('wrap'), lens = document.getElementById('lens');
const ZOOM = 4, LENS = 150;
wrap.addEventListener('mousemove', e => {
  const r = wrap.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  lens.style.display = 'block';
  lens.style.left = (x - LENS / 2) + 'px';
  lens.style.top = (y - LENS - 14) + 'px';
  lens.style.backgroundImage = 'url(/calib/board.svg)';
  lens.style.backgroundSize = (r.width * ZOOM) + 'px ' + (r.height * ZOOM) + 'px';
  lens.style.backgroundPosition = (LENS / 2 - x * ZOOM) + 'px ' + (LENS / 2 - y * ZOOM) + 'px';
});
wrap.addEventListener('mouseleave', () => { lens.style.display = 'none'; });

setInterval(refresh, 1500);
refresh();
</script></body></html>"""


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
<div style="text-align:center;margin-bottom:10px">
 <a href="/calib" style="color:#4de3ff">→ Calibration par lancers</a>
</div>
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
