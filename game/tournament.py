"""Tournoi : poules, calendrier, classement et tableau final.

Logique pure — aucune entrée/sortie, aucune dépendance à Flask. Le module
décrit *le calendrier* ; les résultats, eux, viennent de l'historique des
parties (voir api/server.py). Un tournoi n'est donc jamais « mis à jour » :
on relit les parties jouées et on en déduit tout.

Vocabulaire des références :
    "A1"      -> 1er de la poule A
    "W:T1-2"  -> vainqueur du match T1-2
"""

import random

ROUND_NAMES = {1: "Finale", 2: "Demi-finales", 4: "Quarts de finale",
               8: "Huitièmes de finale", 16: "Seizièmes de finale"}


# ------------------------------------------------------------------
# Structures de poules possibles
# ------------------------------------------------------------------
def group_sizes(n, g):
    """Répartit n joueurs en g poules aussi égales que possible."""
    base, extra = divmod(n, g)
    return [base + 1] * extra + [base] * (g - extra)


def _is_power_of_two(x):
    return x >= 2 and (x & (x - 1)) == 0


def structures(n):
    """Structures de poules valables pour n inscrits.

    On n'en propose une que si le nombre total de qualifiés est une puissance
    de 2 — sinon le tableau final réclamerait des repêchages, règle bien plus
    lourde à expliquer comme à coder. On exige aussi qu'au moins un joueur
    soit éliminé, sans quoi la phase de poules ne servirait à rien.
    """
    out = []
    for g in range(2, n // 2 + 1):
        sizes = group_sizes(n, g)
        if min(sizes) < 2:
            continue
        poule_matches = sum(s * (s - 1) // 2 for s in sizes)
        for q in range(1, min(sizes) + 1):
            total = g * q
            if not _is_power_of_two(total) or total >= n:
                continue
            out.append({
                "groups": g,
                "sizes": sizes,
                "qualify": q,
                "qualified": total,
                "group_matches": poule_matches,
                "bracket_matches": total - 1,
                "total_matches": poule_matches + total - 1,
                # nombre de matchs garantis au minimum (plus petite poule)
                "min_per_player": min(sizes) - 1,
                "label": f"{g} poules de {'/'.join(str(s) for s in sorted(set(sizes), reverse=True))}"
                         f", les {q} premiers",
            })
    out.sort(key=lambda s: s["total_matches"])
    return out


# ------------------------------------------------------------------
# Création du calendrier
# ------------------------------------------------------------------
def _bracket_order(n):
    """Ordre des têtes de série d'un tableau à n places.

    Donne [1, 8, 4, 5, 2, 7, 3, 6] pour 8 : les deux premières têtes ne
    peuvent se rencontrer qu'en finale.
    """
    if n == 1:
        return [1]
    prev = _bracket_order(n // 2)
    out = []
    for s in prev:
        out.extend([s, n + 1 - s])
    return out


def group_letter(i):
    return chr(ord("A") + i)


def create(players, mode="501", options=None, n_groups=2, qualify=1, shuffle=True):
    """Construit un tournoi complet : poules, matchs de poule, tableau final."""
    players = list(players)
    if shuffle:
        random.shuffle(players)

    sizes = group_sizes(len(players), n_groups)
    groups, i = [], 0
    for s in sizes:
        groups.append(players[i:i + s])
        i += s

    matches = []

    # --- poules : chacun rencontre tous les autres de sa poule ---
    for gi, group in enumerate(groups):
        num = 0
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                num += 1
                matches.append({
                    "id": f"P{gi + 1}-{num}",
                    "phase": f"Poule {group_letter(gi)}",
                    "group": gi,
                    "players": [group[a], group[b]],
                })

    # --- tableau final ---
    # Tête de série : les premiers de poule d'abord, puis les deuxièmes, etc.
    # Croisé avec l'ordre de tableau, cela évite que deux joueurs d'une même
    # poule se retrouvent dès le premier tour.
    seeds = {}
    n = 0
    for rank in range(1, qualify + 1):
        for gi in range(n_groups):
            n += 1
            seeds[n] = f"{group_letter(gi)}{rank}"

    slots = [seeds[s] for s in _bracket_order(len(seeds))]
    rnd = 1
    while len(slots) > 1:
        phase = ROUND_NAMES.get(len(slots) // 2, f"Tour {rnd}")
        nxt = []
        for m in range(0, len(slots), 2):
            mid = f"T{rnd}-{m // 2 + 1}"
            matches.append({
                "id": mid,
                "phase": phase,
                "round": rnd,
                "from": [slots[m], slots[m + 1]],
            })
            nxt.append(f"W:{mid}")
        slots = nxt
        rnd += 1

    return {
        "name": "Tournoi Grassiens",
        "mode": mode,
        "options": options or {"double_in": False, "double_out": True},
        "players": players,
        "groups": groups,
        "qualify": qualify,
        "matches": matches,
        "walkovers": {},
    }


# ------------------------------------------------------------------
# Classement d'une poule
# ------------------------------------------------------------------
def group_standings(tournament, gi, results, elo_by_name=None):
    """Classement d'une poule : victoires, puis confrontation directe, puis Elo."""
    group = tournament["groups"][gi]
    elo_by_name = elo_by_name or {}

    played = {p: 0 for p in group}
    wins = {p: 0 for p in group}
    beaten = {p: set() for p in group}       # qui chaque joueur a battu

    for m in tournament["matches"]:
        if m.get("group") != gi or m["id"] not in results:
            continue
        winner = results[m["id"]]
        a, b = m["players"]
        loser = b if winner == a else a
        played[a] += 1
        played[b] += 1
        wins[winner] += 1
        beaten[winner].add(loser)

    def sort_key(p):
        # confrontation directe : nombre de joueurs à égalité de victoires
        # que ce joueur a battus
        pairs = sum(1 for q in group
                    if q != p and wins[q] == wins[p] and q in beaten[p])
        return (-wins[p], -pairs, -elo_by_name.get(p, 0), p.lower())

    ordered = sorted(group, key=sort_key)
    return [
        {"rank": i, "name": p, "played": played[p], "wins": wins[p]}
        for i, p in enumerate(ordered, 1)
    ]


def group_complete(tournament, gi, results):
    return all(m["id"] in results
               for m in tournament["matches"] if m.get("group") == gi)


# ------------------------------------------------------------------
# Résolution des matchs du tableau
# ------------------------------------------------------------------
def resolve(tournament, ref, results, elo_by_name=None):
    """Nom du joueur derrière une référence ("A1", "W:T1-2"), ou None."""
    if ref.startswith("W:"):
        return results.get(ref[2:])
    gi = ord(ref[0]) - ord("A")
    rank = int(ref[1:])
    if gi >= len(tournament["groups"]) or not group_complete(tournament, gi, results):
        return None
    standings = group_standings(tournament, gi, results, elo_by_name)
    return standings[rank - 1]["name"] if rank <= len(standings) else None


def match_players(tournament, match, results, elo_by_name=None):
    """Les deux joueurs d'un match, résolus si possible."""
    if "players" in match:
        return list(match["players"])
    return [resolve(tournament, r, results, elo_by_name) for r in match["from"]]


def view(tournament, results, elo_by_name=None):
    """État complet du tournoi, prêt à afficher.

    Chaque match est classé « joué », « jouable » (les deux joueurs sont
    connus) ou « en attente » (il dépend de résultats manquants).
    """
    out = []
    for m in tournament["matches"]:
        players = match_players(tournament, m, results, elo_by_name)
        winner = results.get(m["id"])
        out.append({
            "id": m["id"],
            "phase": m["phase"],
            "group": m.get("group"),
            # d'où viennent les joueurs, pour afficher « 1er poule A » tant que
            # le match du tableau n'est pas encore déterminé
            "from": m.get("from"),
            "players": players,
            "winner": winner,
            "walkover": m["id"] in tournament.get("walkovers", {}),
            "state": "joué" if winner
                     else ("jouable" if all(players) else "en attente"),
        })

    standings = [
        {
            "group": group_letter(gi),
            "complete": group_complete(tournament, gi, results),
            "rows": group_standings(tournament, gi, results, elo_by_name),
        }
        for gi in range(len(tournament["groups"]))
    ]

    final = next((m for m in reversed(out) if m["phase"] == "Finale"), None)
    return {
        "name": tournament["name"],
        "mode": tournament["mode"],
        "options": tournament.get("options", {}),
        "qualify": tournament["qualify"],
        "players": tournament["players"],
        "matches": out,
        "standings": standings,
        "champion": final["winner"] if final else None,
    }
