"""Modèle de caméra à fleur de cible et triangulation.

Chaque caméra est dans le plan de la cible. Elle ne mesure qu'une chose :
la colonne de pixels (u) où elle voit la pointe. Le modèle relie u à la
direction (gisement) du point vu depuis la caméra :

    u = u0 + f * tan(beta - phi)

avec beta = atan2(y - cy, x - cx) le gisement du point, et 5 paramètres
par caméra : position (cx, cy), orientation phi, focale f (px), centre u0.

La calibration ajuste ces paramètres par moindres carrés (Levenberg-
Marquardt) sur les points cliqués. La triangulation intersecte les rayons
de 2+ caméras.
"""

import math

import numpy as np


def _wrap(a):
    """Ramène un angle dans [-pi, pi]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def _residuals(p, obs):
    cx, cy, phi, f, u0 = p
    res = []
    for (x, y), u in obs:
        beta = math.atan2(y - cy, x - cx)
        d = _wrap(beta - phi)
        d = max(-1.3, min(1.3, d))  # évite l'explosion de tan hors champ
        res.append(u0 + f * math.tan(d) - u)
    return np.array(res)


def fit_camera(obs, init):
    """Ajuste les 5 paramètres d'une caméra.

    obs  : liste de ((x, y) en mm, colonne u observée en px)
    init : [cx, cy, phi, f, u0] de départ
    Retourne (params, rms_px).
    """
    p = np.array(init, dtype=float)
    lam = 1e-3
    best = np.linalg.norm(_residuals(p, obs))

    for _ in range(200):
        r = _residuals(p, obs)
        # Jacobienne numérique
        J = np.zeros((len(r), 5))
        for j in range(5):
            dp = np.zeros(5)
            dp[j] = max(1e-4, abs(p[j]) * 1e-5)
            J[:, j] = (_residuals(p + dp, obs) - r) / dp[j]

        A = J.T @ J + lam * np.eye(5)
        g = J.T @ r
        try:
            step = np.linalg.solve(A, g)
        except np.linalg.LinAlgError:
            break

        p_new = p - step
        norm_new = np.linalg.norm(_residuals(p_new, obs))
        if norm_new < best:
            p, best, lam = p_new, norm_new, lam * 0.7
        else:
            lam *= 3
            if lam > 1e8:
                break
        if np.linalg.norm(step) < 1e-9:
            break

    rms = float(np.sqrt(np.mean(_residuals(p, obs) ** 2)))
    return p.tolist(), rms


def ray_from_column(params, u):
    """Rayon (origine, direction unitaire) correspondant à une colonne de pixels."""
    cx, cy, phi, f, u0 = params
    theta = phi + math.atan2(u - u0, f)
    return np.array([cx, cy]), np.array([math.cos(theta), math.sin(theta)])


def triangulate(rays):
    """Point le plus proche (moindres carrés) de 2+ rayons.

    rays : liste de (origine 2D, direction unitaire 2D)
    Retourne ((x, y), erreur_moyenne_mm) où l'erreur est la distance
    moyenne du point aux rayons — un bon indicateur de cohérence.
    """
    A = np.zeros((2, 2))
    b = np.zeros(2)
    for origin, direction in rays:
        proj = np.eye(2) - np.outer(direction, direction)
        A += proj
        b += proj @ origin
    point = np.linalg.solve(A, b)

    err = float(np.mean([
        np.linalg.norm((np.eye(2) - np.outer(d, d)) @ (point - o))
        for o, d in rays
    ]))
    return (float(point[0]), float(point[1])), err
