import math
import numpy as np

SECTORS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

SECTOR_WIDTH = 2 * math.pi / 20


def angle_to_sector(theta):
    index = int(theta / SECTOR_WIDTH) % 20
    return SECTORS[index]


def radius_to_zone(r_norm):
    if r_norm < 0.03735:
        return "bull", 50
    elif r_norm < 0.09353:
        return "outer_bull", 25
    elif 0.5847 <= r_norm <= 0.6317:
        return "triple", 3
    elif 0.9529 <= r_norm <= 1.0:
        return "double", 2
    elif r_norm > 1.0:
        return "miss", 0
    else:
        return "single", 1


def compute_score(impact, centre, rayon, angle_offset):
    dx = impact[0] - centre[0]
    dy = centre[1] - impact[1]

    r = math.hypot(dx, dy)
    theta = math.atan2(dy, dx)
    theta = (theta - angle_offset) % (2 * math.pi)

    r_norm = r / rayon
    zone, multiplier = radius_to_zone(r_norm)
    sector = angle_to_sector(theta)

    if zone in ("bull", "outer_bull"):
        score = multiplier
    else:
        score = sector * multiplier

    return {
        "score": score,
        "sector": sector,
        "multiplier": multiplier,
        "zone": zone,
        "r_norm": round(r_norm, 4),
        "theta_deg": round(math.degrees(theta), 2),
    }
