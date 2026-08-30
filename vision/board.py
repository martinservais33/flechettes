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
R_PLAUSIBLE  = 250.0  # au-dela, aucune flechette ne peut se planter :
                      # 170 mm au double externe, ~225 avec l'anneau de
                      # recuperation. Une position plus lointaine n'est pas
                      # un miss, c'est une triangulation aberrante.

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


def corner_point(sector, neighbour, radius):
    """Point (x, y) en mm au CROISEMENT du fil séparant `sector` de son
    voisin `neighbour` (dans le sens horaire) avec un anneau de rayon
    donné. C'est un coin de case, repère exact où caler la pointe."""
    idx = SECTORS.index(sector)
    if SECTORS[(idx + 1) % 20] != neighbour:
        raise ValueError(f"{sector} et {neighbour} ne sont pas adjacents (sens horaire)")
    a = math.radians(sector_center_angle(sector) - 9)   # frontière = centre - 9°
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
# Des COINS de cases (croisements de fils) : la pointe se cale exactement
# dans le coin, bien plus précis qu'un milieu de case estimé à l'œil.
# 8 coins extérieurs de doubles répartis sur le tour + bull + 2 coins de
# triples pour la diversité radiale.
CALIB_POINTS = [
    ("bull",  "Bull (centre exact)", 0.0, 0.0),
    ("d20_1", "Double 20 — coin extérieur, côté du 1",  *corner_point(20, 1,  R_DOUBLE_OUT)),
    ("d4_13", "Double 4 — coin extérieur, côté du 13",  *corner_point(4,  13, R_DOUBLE_OUT)),
    ("d6_10", "Double 6 — coin extérieur, côté du 10",  *corner_point(6,  10, R_DOUBLE_OUT)),
    ("d15_2", "Double 15 — coin extérieur, côté du 2",  *corner_point(15, 2,  R_DOUBLE_OUT)),
    ("d3_19", "Double 3 — coin extérieur, côté du 19",  *corner_point(3,  19, R_DOUBLE_OUT)),
    ("d7_16", "Double 7 — coin extérieur, côté du 16",  *corner_point(7,  16, R_DOUBLE_OUT)),
    ("d11_14","Double 11 — coin extérieur, côté du 14", *corner_point(11, 14, R_DOUBLE_OUT)),
    ("d9_12", "Double 9 — coin extérieur, côté du 12",  *corner_point(9,  12, R_DOUBLE_OUT)),
    ("t20_1", "Triple 20 — coin extérieur, côté du 1",  *corner_point(20, 1,  R_TRIPLE_OUT)),
    ("t3_19", "Triple 3 — coin extérieur, côté du 19",  *corner_point(3,  19, R_TRIPLE_OUT)),
]
