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
DIFF_THRESHOLD   = 28      # intensité mini (gris) pour la détection de stabilité
COLOR_THRESHOLD  = 20      # intensité mini (max des canaux BGR) pour la silhouette
                           # plus sensible que le gris : le fût argenté d'une
                           # fléchette disparaît en niveaux de gris
MIN_DART_AREA    = 60      # blob plus petit = bruit
BIG_BLOB_AREA    = 800     # blob "taille empennage" (règle du retrait)
MAX_DART_AREA    = 9000    # blob plus grand = main / gros changement
CLEAR_AREA       = 15000   # surface totale changée -> retrait des fléchettes
SETTLE_PIXELS    = 400     # nb de pixels changés entre 2 frames consécutives
                           # en dessous duquel la scène est considérée stable
BAND_UP          = 140     # hauteur de la bande d'analyse au-dessus de la surface
BAND_DOWN        = 8       # marge sous la ligne de surface
SHADOW_MIN_RATIO = 0.45    # une ombre garde au moins 45% de la luminosité
SHADOW_SPREAD    = 0.08    # écart max entre canaux du ratio pour être une ombre
                           # (au-delà, la teinte change : objet réel, pas une ombre)


def preprocess(frame):
    """Frame couleur -> niveaux de gris flouté, prêt pour la différence."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def changed_pixels(gray_a, gray_b):
    """Nombre de pixels significativement différents entre deux frames."""
    diff = cv2.absdiff(gray_a, gray_b)
    return int(np.count_nonzero(diff > DIFF_THRESHOLD))


def surface_band_mask(shape, line):
    """Masque de la bande d'analyse au-dessus de la ligne de surface.

    line = (a, b) : la surface est la droite v = a*u + b dans l'image
    redressée. La bande va de BAND_UP px au-dessus à BAND_DOWN en dessous.
    Ne garder que cette bande élimine l'empennage, les mains hautes et
    les ombres portées sur la surface.
    """
    h, w = shape[:2]
    a, b = line
    us = np.arange(w, dtype=np.float32)
    vs = np.arange(h, dtype=np.float32).reshape(-1, 1)
    line_v = a * us + b
    dist = vs - line_v            # <0 au-dessus de la ligne
    mask = ((dist >= -BAND_UP) & (dist <= BAND_DOWN)).astype(np.uint8) * 255
    return mask


def extract_impact(reference_bgr, settled_bgr, line=None):
    """Cherche la silhouette nouvelle entre référence et image stabilisée.

    Travaille en COULEUR (max des canaux) : le fût argenté et la pointe
    d'une fléchette contrastent peu en niveaux de gris et disparaissaient
    de la silhouette — seul l'empennage sombre était détecté, et la
    "pointe" se retrouvait au bas de l'empennage.

    La pointe est le point détecté le plus proche de la ligne de surface,
    en préférant les points de la bande proche de la cible.

    Retourne (kind, tip, area) :
      kind = "dart"  -> tip = (u, v) de la pointe, area = surface totale
      kind = "clear" -> perturbation massive (main, fléchettes retirées)
      kind = "none"  -> rien de significatif
    """
    diff = cv2.absdiff(settled_bgr, reference_bgr)
    if diff.ndim == 3:
        diff = diff.max(axis=2)
    _, mask = cv2.threshold(diff, COLOR_THRESHOLD, 255, cv2.THRESH_BINARY)

    if settled_bgr.ndim == 3:
        # Suppression des ombres : une ombre ASSOMBRIT sans changer la teinte
        # (les 3 canaux baissent dans la même proportion). Une fléchette, un
        # objet réel, change la couleur -> le ratio diffère entre canaux.
        ref_f = reference_bgr.astype(np.float32) + 8.0
        cur_f = settled_bgr.astype(np.float32) + 8.0
        ratio = cur_f / ref_f
        rmin = ratio.min(axis=2)
        rspread = ratio.max(axis=2) - rmin
        shadow = (rmin >= SHADOW_MIN_RATIO) & (ratio.max(axis=2) < 1.0) & \
                 (rspread < SHADOW_SPREAD)
        mask[shadow] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # dilatation forte : reconnecte l'empennage au fût fin qui se détecte mal
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "none", None, 0

    blobs = [c for c in contours if cv2.contourArea(c) >= MIN_DART_AREA]
    total_area = int(sum(cv2.contourArea(c) for c in blobs))
    # Retrait des fléchettes : perturbation massive OU plusieurs GROSSES
    # silhouettes d'un coup (chaque fléchette retirée "disparaît" de la
    # référence). Seuls les blobs taille empennage comptent : une fléchette
    # seule se fragmente souvent en petits morceaux, ce n'est pas un retrait.
    big_blobs = sum(1 for c in blobs if cv2.contourArea(c) >= BIG_BLOB_AREA)
    if total_area > CLEAR_AREA or big_blobs >= 3:
        return "clear", None, total_area
    if not blobs or max(cv2.contourArea(c) for c in blobs) > MAX_DART_AREA:
        return ("clear", None, total_area) if blobs else ("none", None, 0)

    # candidats = tous les points des blobs valides (la fléchette peut être
    # fragmentée en 2 morceaux : empennage + bout de fût)
    pts = np.vstack([c.reshape(-1, 2) for c in blobs])
    if line is not None:
        a, b = line
        dist = pts[:, 1] - (a * pts[:, 0] + b)   # <0 au-dessus de la surface
        in_band = (dist >= -BAND_UP) & (dist <= BAND_DOWN)
        if in_band.any():
            pts, dist = pts[in_band], dist[in_band]
        tip = pts[dist.argmax()]                 # le plus proche de la surface
    else:
        tip = pts[pts[:, 1].argmax()]
    return "dart", (int(tip[0]), int(tip[1])), total_area
