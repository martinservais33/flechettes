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


def preprocess(frame):
    """Frame couleur -> niveaux de gris flouté, prêt pour la différence."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def changed_pixels(gray_a, gray_b):
    """Nombre de pixels significativement différents entre deux frames."""
    diff = cv2.absdiff(gray_a, gray_b)
    return int(np.count_nonzero(diff > DIFF_THRESHOLD))


def extract_impact(reference_gray, settled_gray):
    """Cherche la silhouette nouvelle entre référence et image stabilisée.

    Retourne (kind, tip, area) :
      kind = "dart"  -> tip = (u, v) de la pointe, area = taille du blob
      kind = "clear" -> perturbation massive (main, fléchettes retirées)
      kind = "none"  -> rien de significatif
    """
    diff = cv2.absdiff(settled_gray, reference_gray)
    _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "none", None, 0

    total_area = sum(cv2.contourArea(c) for c in contours)
    if total_area > MAX_DART_AREA:
        return "clear", None, int(total_area)

    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area < MIN_DART_AREA:
        return "none", None, int(area)

    # La pointe : le point du blob le plus bas (image redressée = surface en bas)
    pts = biggest.reshape(-1, 2)
    tip = pts[pts[:, 1].argmax()]
    return "dart", (int(tip[0]), int(tip[1])), int(area)
