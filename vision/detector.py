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
MAX_DART_AREA    = 3500    # blob plus grand (dans la bande) = main / gros changement
CLEAR_AREA       = 8000    # surface totale changée -> retrait des fléchettes
SETTLE_PIXELS    = 400     # nb de pixels changés entre 2 frames consécutives
                           # en dessous duquel la scène est considérée stable
BAND_UP          = 70      # hauteur de la bande d'analyse au-dessus de la surface
BAND_DOWN        = 8       # marge sous la ligne de surface


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


def extract_impact(reference_gray, settled_gray, band_mask=None, line=None):
    """Cherche la silhouette nouvelle entre référence et image stabilisée.

    band_mask / line : restreignent l'analyse à la bande au-dessus de la
    surface et définissent la pointe comme le point le plus PROCHE de la
    surface (et non le plus bas de la silhouette, faussé quand la
    fléchette penche et que l'empennage descend sous la pointe).

    Retourne (kind, tip, area) :
      kind = "dart"  -> tip = (u, v) de la pointe, area = taille du blob
      kind = "clear" -> perturbation massive (main, fléchettes retirées)
      kind = "none"  -> rien de significatif
    """
    diff = cv2.absdiff(settled_gray, reference_gray)
    _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    if band_mask is not None:
        mask = cv2.bitwise_and(mask, band_mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "none", None, 0

    blobs = [c for c in contours if cv2.contourArea(c) >= MIN_DART_AREA]
    total_area = sum(cv2.contourArea(c) for c in contours)
    # Retrait des fléchettes : perturbation massive OU plusieurs blobs à la
    # fois (les fléchettes retirées "disparaissent" chacune de la référence)
    if total_area > CLEAR_AREA or len(blobs) >= 3:
        return "clear", None, int(total_area)

    if not blobs:
        return "none", None, 0
    biggest = max(blobs, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area > MAX_DART_AREA:
        return "clear", None, int(area)

    pts = biggest.reshape(-1, 2)
    if line is not None:
        a, b = line
        # pointe = point le plus proche de la ligne de surface
        tip = pts[(pts[:, 1] - (a * pts[:, 0] + b)).argmax()]
    else:
        tip = pts[pts[:, 1].argmax()]
    return "dart", (int(tip[0]), int(tip[1])), int(area)
