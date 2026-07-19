"""Rejeu visuel hors ligne des événements enregistrés.

Pour chaque événement du dataset et chaque caméra, produit une planche :

    [ après (brute) | masque de différence | blobs + pointe choisie ]

et un index.html pour tout parcourir dans un navigateur. C'est l'outil
pour VOIR ce que voit l'algorithme et comprendre chaque cercle mal placé,
sans lancer une seule fléchette.

Usage :
    python replay.py <dossier_dataset> [dossier_sortie]

Par défaut la sortie va dans replay_out/ à côté du dataset. L'algorithme
rejoué est celui de detector.py — modifier detector.py et relancer le
rejeu montre l'effet sur TOUS les événements enregistrés.
"""

import glob
import json
import os
import sys

import cv2
import numpy as np

from detector import MIN_DART_AREA, extract_impact, preprocess

# lignes de surface v = a*u + b par caméra (surface.json à côté de ce script)
SURFACE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "surface.json")
SURFACE = {}
if os.path.exists(SURFACE_FILE):
    SURFACE = {k: tuple(v) for k, v in json.load(open(SURFACE_FILE)).items()}

ORANGE = (0, 165, 255)
GREEN = (80, 220, 80)
BLUE = (255, 120, 40)


def erase_burned_circle(after, before, meta, cam):
    """Efface le cercle orange incrusté dans les anciens after.jpg
    (les premiers datasets sauvaient l'image annotée) en recopiant les
    pixels de l'image before dans un disque autour de la pointe."""
    tip = meta.get("tips", {}).get(str(cam))
    if tip is None:
        return after
    mask = np.zeros(after.shape[:2], np.uint8)
    cv2.circle(mask, tuple(tip), 16, 255, -1)
    out = after.copy()
    out[mask > 0] = before[mask > 0]
    return out


def debug_sheet(before, after, line=None):
    """Rejoue extract_impact et rend la planche [après | masque | analyse]."""
    ref_gray, cur_gray = preprocess(before), preprocess(after)
    kind, tip, area = extract_impact(ref_gray, cur_gray, line)

    # reconstruire le masque comme dans extract_impact (pour l'afficher)
    from detector import DIFF_THRESHOLD
    diff = cv2.absdiff(cur_gray, ref_gray)
    _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))

    analysis = after.copy()
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        a = cv2.contourArea(c)
        color = GREEN if a >= MIN_DART_AREA else (90, 90, 90)
        cv2.drawContours(analysis, [c], -1, color, 1)
        if a >= MIN_DART_AREA:
            x, y = c.reshape(-1, 2).min(axis=0)
            cv2.putText(analysis, str(int(a)), (int(x), max(12, int(y) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1)
    if line is not None:
        a, b = line
        w = analysis.shape[1]
        cv2.line(analysis, (0, int(b)), (w - 1, int(a * (w - 1) + b)), BLUE, 1)
    if tip is not None:
        cv2.circle(analysis, tuple(tip), 12, ORANGE, 2)
    cv2.putText(analysis, f"{kind} (aire {area})", (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, ORANGE, 2)

    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    return np.hstack([after, mask_bgr, analysis]), kind, tip, area


def main(dataset_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    events = sorted(d for d in glob.glob(os.path.join(dataset_dir, "*")) if os.path.isdir(d))
    rows = []

    for folder in events:
        stamp = os.path.basename(folder)
        meta = {}
        meta_path = os.path.join(folder, "meta.json")
        if os.path.exists(meta_path):
            meta = json.load(open(meta_path))

        cam_summaries = []
        for bpath in sorted(glob.glob(os.path.join(folder, "cam*_before.jpg"))):
            cam = os.path.basename(bpath).split("_")[0].replace("cam", "")
            apath = os.path.join(folder, f"cam{cam}_after.jpg")
            if not os.path.exists(apath):
                continue
            before, after = cv2.imread(bpath), cv2.imread(apath)
            if before is None or after is None:
                continue
            # les anciens datasets ont le cercle orange incrusté : on l'efface
            if not os.path.exists(os.path.join(folder, f"cam{cam}_annot.jpg")):
                after = erase_burned_circle(after, before, meta, cam)

            sheet, kind, tip, area = debug_sheet(before, after, SURFACE.get(cam))
            cv2.imwrite(os.path.join(out_dir, f"{stamp}_cam{cam}.jpg"), sheet)
            cam_summaries.append(f"cam{cam}: {kind} ({area}px)")

        pred = meta.get("prediction")
        t = pred["throw"] if pred else None
        pred_str = f'{t["multiplier"]}x{t["sector"]} ({t["score"]} pts)' if t else "?"
        truth = meta.get("truth") or "non annoté"
        rows.append((stamp, pred_str, truth, cam_summaries))
        print(f"{stamp}: pred {pred_str} | réel {truth} | {' · '.join(cam_summaries)}")

    # index HTML
    html = ["<html><head><meta charset='utf-8'><style>",
            "body{background:#1a1a2e;color:#eee;font-family:sans-serif;padding:10px}",
            "img{width:100%;max-width:1400px;display:block;margin:4px 0}",
            "h3{color:#f5a623;margin:18px 0 4px}",
            ".m{color:#8892a4}</style></head><body>",
            f"<h1>Rejeu — {len(rows)} événements</h1>"]
    for stamp, pred_str, truth, cams in rows:
        html.append(f"<h3>{stamp}</h3><div class='m'>prédit : {pred_str} — réel : {truth}"
                    f" — {' · '.join(cams)}</div>")
        for img in sorted(glob.glob(os.path.join(out_dir, f"{stamp}_cam*.jpg"))):
            html.append(f"<img src='{os.path.basename(img)}'>")
    html.append("</body></html>")
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write("\n".join(html))
    print(f"\n{len(rows)} événements rejoués -> {os.path.join(out_dir, 'index.html')}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python replay.py <dossier_dataset> [dossier_sortie]")
        sys.exit(1)
    dataset = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(dataset.rstrip("/")), "replay_out")
    main(dataset, out)
