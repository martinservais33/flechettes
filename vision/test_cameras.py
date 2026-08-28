"""Étape 0 — Validation matérielle des caméras.

Deux usages :

1. Scanner les caméras disponibles :
       python vision/test_cameras.py

2. Tester le streaming SIMULTANÉ des caméras trouvées (les 3 en même temps) :
       python vision/test_cameras.py 0 2 4

   (remplacer 0 2 4 par les index trouvés au scan)

Le test simultané mesure les FPS réels de chaque caméra pendant 10 secondes
et sauvegarde un snapshot par caméra dans vision/snapshots/ pour vérifier
l'image (netteté, orientation, champ de vision).
"""

import glob
import os
import sys
import threading
import time

import cv2

WIDTH, HEIGHT, FPS = 640, 480, 30
TEST_DURATION = 10  # secondes
SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


def resolve_device(index):
    """Chemin du peripherique pour une camera logique.

    Les index /dev/videoN sont reattribues par le noyau a chaque enumeration
    USB (plus petit mineur libre) : ils ne designent pas une camera en
    particulier. Les symlinks /dev/dartcamN, poses par les regles udev
    99-flechettes-cams.rules, sont ancres sur le port physique du hub et
    font foi des qu ils existent. Repli sur l index brut sinon.
    """
    stable = f"/dev/dartcam{index}"
    return stable if os.path.exists(stable) else f"/dev/video{index}"


def open_camera(index, stable=True):
    device = resolve_device(index) if stable else f"/dev/video{index}"
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    # MJPEG obligatoire : en YUYV brut, 3 caméras saturent le bus USB
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    return cap


def scan():
    """Essaie chaque /dev/video* et indique lesquels donnent une image."""
    devices = sorted(glob.glob("/dev/video*"), key=lambda p: int(p.replace("/dev/video", "")))
    if not devices:
        print("Aucun /dev/video* trouvé — caméras branchées ?")
        return

    print(f"{len(devices)} périphériques vidéo trouvés (note : chaque caméra USB en expose souvent 2).\n")
    working = []
    for dev in devices:
        index = int(dev.replace("/dev/video", ""))
        cap = open_camera(index, stable=False)
        if cap is None:
            print(f"  {dev}: impossible à ouvrir")
            continue
        ok, frame = cap.read()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4))
            print(f"  {dev}: OK — {w}x{h}, codec {codec}")
            working.append(index)
        else:
            print(f"  {dev}: s'ouvre mais ne donne pas d'image (probablement le 2e nœud d'une caméra)")
        cap.release()

    if working:
        print(f"\nCaméras fonctionnelles : {working}")
        print(f"Lance maintenant le test simultané :\n    python vision/test_cameras.py {' '.join(map(str, working))}")


def capture_loop(index, results, stop_event):
    """Thread : capture en continu et compte les frames."""
    cap = open_camera(index)
    if cap is None:
        results[index] = {"error": "ouverture impossible"}
        return

    count, failures = 0, 0
    snapshot = None
    start = time.time()
    while not stop_event.is_set():
        ok, frame = cap.read()
        if ok:
            count += 1
            if snapshot is None:
                snapshot = frame
        else:
            failures += 1
    elapsed = time.time() - start
    cap.release()

    if snapshot is not None:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        path = os.path.join(SNAPSHOT_DIR, f"cam{index}.jpg")
        cv2.imwrite(path, snapshot)

    results[index] = {
        "frames": count,
        "failures": failures,
        "fps": count / elapsed if elapsed > 0 else 0,
    }


def simultaneous_test(indices):
    print(f"Test simultané de {len(indices)} caméras pendant {TEST_DURATION}s : {indices}\n")
    results = {}
    stop_event = threading.Event()
    threads = [
        threading.Thread(target=capture_loop, args=(i, results, stop_event), daemon=True)
        for i in indices
    ]
    for t in threads:
        t.start()
    time.sleep(TEST_DURATION)
    stop_event.set()
    for t in threads:
        t.join(timeout=5)

    print(f"{'Caméra':<10}{'FPS':>8}{'Frames':>10}{'Échecs':>10}")
    all_ok = True
    for i in indices:
        r = results.get(i, {"error": "aucun résultat"})
        if "error" in r:
            print(f"video{i:<5}    ERREUR : {r['error']}")
            all_ok = False
        else:
            print(f"video{i:<5}{r['fps']:>8.1f}{r['frames']:>10}{r['failures']:>10}")
            if r["fps"] < 15:
                all_ok = False

    print(f"\nSnapshots sauvés dans {SNAPSHOT_DIR}/")
    if all_ok:
        print("RÉSULTAT : OK — toutes les caméras tiennent >= 15 FPS en simultané.")
    else:
        print("RÉSULTAT : PROBLÈME — au moins une caméra est sous 15 FPS ou en erreur.")
        print("Pistes : vérifier que MJPG est actif, baisser la résolution, répartir sur d'autres ports USB.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        simultaneous_test([int(a) for a in sys.argv[1:]])
    else:
        scan()
