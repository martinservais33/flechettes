"""Détection d'impact par différence d'images.

Principe : on garde une image de référence (la cible dans son état actuel).
Quand une fléchette se plante, la nouvelle silhouette apparaît dans la
différence entre l'image stabilisée et la référence. La pointe est le
point de la silhouette le plus proche de la surface (le plus bas dans
l'image redressée).

Une main qui retire les fléchettes produit une perturbation énorme :
on la classifie comme "clear" (fin de tour) au lieu d'un impact.
"""

import cv2
import numpy as np

# Seuils (pixels sur images 640x480 / 480x640)
DIFF_THRESHOLD   = 28      # intensité mini pour considérer un pixel changé
MIN_DART_AREA    = 60      # blob plus petit = bruit
MAX_DART_AREA    = 12000   # blob plus grand = main / gros changement
SETTLE_PIXELS    = 400     # nb de pixels changés entre 2 frames consécutives
                           # en dessous duquel la scène est considérée stable
LINE_MARGIN      = 6       # tolérance (px) autour de la ligne de surface


def preprocess(frame):
    """Frame couleur -> niveaux de gris flouté, prêt pour la différence."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def changed_pixels(gray_a, gray_b):
    """Nombre de pixels significativement différents entre deux frames."""
    diff = cv2.absdiff(gray_a, gray_b)
    return int(np.count_nonzero(diff > DIFF_THRESHOLD))


def extract_impact(reference_gray, settled_gray, line=None):
    """Cherche la silhouette nouvelle entre référence et image stabilisée.

    Retourne (kind, tip, area) :
      kind = "dart"  -> tip = (u, v) de la pointe, area = taille du blob
      kind = "clear" -> perturbation massive (main, fléchettes retirées)
      kind = "none"  -> rien de significatif
    """
    diff = cv2.absdiff(settled_gray, reference_gray)
    _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    raw_mask = mask.copy()   # avant morphologie, pour l'affinage sub-pixel
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "none", None, 0

    total_area = sum(cv2.contourArea(c) for c in contours)
    if total_area > MAX_DART_AREA:
        return "clear", None, int(total_area)

    valid = [c for c in contours if cv2.contourArea(c) >= MIN_DART_AREA]

    # Rien de RÉEL ne peut apparaître sous le plan de la cible : un blob
    # entièrement sous la ligne de surface est une ombre portée ou un
    # reflet (mesuré sur données réelles : indiscernable d'un fût par la
    # couleur, mais toujours sous la ligne).
    if line is not None:
        a, b = line
        valid = [c for c in valid
                 if (c.reshape(-1, 2)[:, 1]
                     <= a * c.reshape(-1, 2)[:, 0] + b + LINE_MARGIN).any()]
    if not valid:
        return "none", None, int(max(cv2.contourArea(c) for c in contours))

    # Le fût fin se fragmente souvent en plusieurs blobs sous l'empennage :
    # on regroupe le plus gros blob avec les fragments alignés avec lui,
    # et la pointe est le point le plus bas de l'ENSEMBLE (image redressée),
    # sans descendre sous la ligne de surface.
    cluster = _cluster_aligned(valid)
    pts = np.vstack([c.reshape(-1, 2) for c in cluster])
    if line is not None:
        above = pts[:, 1] <= a * pts[:, 0] + b + LINE_MARGIN
        if above.any():
            pts = pts[above]
    tip = pts[pts[:, 1].argmax()]
    area = sum(cv2.contourArea(c) for c in cluster)
    # Affinage : la dilatation décale le coin bas du blob de 2-3 px, ce qui
    # suffit à rater un triple. On recentre u sur le centroïde du masque
    # BRUT dans les dernières lignes avant la surface.
    u_ref = _refine_tip_u(raw_mask, tip)
    return "dart", (round(float(u_ref), 1), int(tip[1])), int(area)


def _refine_tip_u(raw_mask, tip):
    u0, v0 = int(tip[0]), int(tip[1])
    h, w = raw_mask.shape
    roi = raw_mask[max(0, v0 - 6):min(h, v0 + 3), max(0, u0 - 8):min(w, u0 + 9)]
    ys, xs = np.nonzero(roi)
    if len(xs) == 0:
        return float(u0)
    return max(0, u0 - 8) + float(xs.mean())


def _cluster_aligned(blobs, margin=25):
    """Regroupe le plus gros blob avec les blobs qui le chevauchent
    horizontalement (à ± margin px), de proche en proche — la chaîne
    empennage / fût / pointe d'une même fléchette."""
    order = sorted(range(len(blobs)), key=lambda i: cv2.contourArea(blobs[i]), reverse=True)
    cluster = [blobs[order[0]]]
    rest = [blobs[i] for i in order[1:]]
    lo = int(cluster[0].reshape(-1, 2)[:, 0].min()) - margin
    hi = int(cluster[0].reshape(-1, 2)[:, 0].max()) + margin
    changed = True
    while changed:
        changed = False
        remaining = []
        for b in rest:
            us = b.reshape(-1, 2)[:, 0]
            if us.min() <= hi and us.max() >= lo:
                cluster.append(b)
                lo = min(lo, int(us.min()) - margin)
                hi = max(hi, int(us.max()) + margin)
                changed = True
            else:
                remaining.append(b)
        rest = remaining
    return cluster
