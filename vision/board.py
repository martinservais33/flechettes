"""Géométrie de la cible : coordonnées en mm, origine au centre du bull.

Axe x vers la droite (secteur 6), axe y vers le haut (secteur 20).
Rayons standard d'une cible steel-tip (mesures aux fils).
"""

import math

# Secteurs dans le sens horaire en partant du haut
SECTORS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

R_BULL       = 6.35   # bull central (50)
R_OUTER_BULL = 15.9   # couronne 25
R_TRIPLE_IN  = 99.0
R_TRIPLE_OUT = 107.0
R_DOUBLE_IN  = 162.0
R_DOUBLE_OUT = 170.0

R_TRIPLE_MID = (R_TRIPLE_IN + R_TRIPLE_OUT) / 2   # 103 : centre du lit de triple
R_DOUBLE_MID = (R_DOUBLE_IN + R_DOUBLE_OUT) / 2   # 166 : centre du lit de double


def sector_center_angle(sector):
    """Angle (degrés, trigonométrique) du centre d'un secteur. 20 -> 90°, 6 -> 0°."""
    idx = SECTORS.index(sector)
    return 90 - 18 * idx


def sector_point(sector, radius):
    """Point (x, y) en mm au centre angulaire d'un secteur, à un rayon donné."""
    a = math.radians(sector_center_angle(sector))
    return (radius * math.cos(a), radius * math.sin(a))


def score_from_point(x, y):
    """Convertit une coordonnée (mm) en lancer {score, sector, multiplier, zone}."""
    r = math.hypot(x, y)
    if r <= R_BULL:
        return {"score": 50, "sector": 25, "multiplier": 2, "zone": "bull"}
    if r <= R_OUTER_BULL:
        return {"score": 25, "sector": 25, "multiplier": 1, "zone": "outer_bull"}
    if r > R_DOUBLE_OUT:
        return {"score": 0, "sector": 0, "multiplier": 0, "zone": "miss"}

    deg = math.degrees(math.atan2(y, x))
    rel = (90 - deg) % 360
    sector = SECTORS[int(((rel + 9) % 360) // 18)]

    if R_TRIPLE_IN <= r <= R_TRIPLE_OUT:
        return {"score": sector * 3, "sector": sector, "multiplier": 3, "zone": "triple"}
    if R_DOUBLE_IN <= r <= R_DOUBLE_OUT:
        return {"score": sector * 2, "sector": sector, "multiplier": 2, "zone": "double"}
    return {"score": sector, "sector": sector, "multiplier": 1, "zone": "single"}


# Points de calibration : (id, libellé, x, y)
# 9 doubles répartis sur le tour + bull + 2 triples pour la diversité radiale.
CALIB_POINTS = [
    ("bull", "Bull (centre exact)", 0.0, 0.0),
    ("d20", "Double 20 (haut)", *sector_point(20, R_DOUBLE_MID)),
    ("d4",  "Double 4",  *sector_point(4,  R_DOUBLE_MID)),
    ("d6",  "Double 6 (droite)", *sector_point(6, R_DOUBLE_MID)),
    ("d15", "Double 15", *sector_point(15, R_DOUBLE_MID)),
    ("d3",  "Double 3 (bas)", *sector_point(3, R_DOUBLE_MID)),
    ("d7",  "Double 7",  *sector_point(7,  R_DOUBLE_MID)),
    ("d11", "Double 11 (gauche)", *sector_point(11, R_DOUBLE_MID)),
    ("d9",  "Double 9",  *sector_point(9,  R_DOUBLE_MID)),
    ("t20", "Triple 20", *sector_point(20, R_TRIPLE_MID)),
    ("t3",  "Triple 3",  *sector_point(3,  R_TRIPLE_MID)),
]
