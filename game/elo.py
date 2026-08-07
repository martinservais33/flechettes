"""Classement Elo, calculé depuis l'historique des parties.

Aucun compteur n'est stocké : le classement est recalculé à partir de
`data/games.json` à chaque appel. C'est un peu moins efficace, mais bien plus
robuste — supprimer une partie saisie par erreur corrige automatiquement le
classement, sans procédure de rattrapage.

Deux subtilités :

1. **L'ordre chronologique.** L'Elo dépend de l'ordre des parties (battre
   quelqu'un avant ou après sa progression ne rapporte pas la même chose).
   Or `games.json` est trié du plus récent au plus ancien, et son champ
   `date` est une chaîne "jour/mois/année" inutilisable pour trier. On trie
   donc sur `id`, qui est un horodatage en millisecondes.

2. **Les parties à plus de deux joueurs.** L'Elo est fait pour le duel. On
   décompose une partie à N joueurs en duels (le vainqueur bat chacun des
   autres), mais en divisant K par le nombre d'adversaires : sans cet
   amortissement, gagner une partie à 6 rapporterait cinq fois plus qu'un
   duel, et une seule grosse partie de groupe écraserait tout le classement.
"""

START = 1000.0
K = 32.0


def expected(rating_a, rating_b):
    """Probabilité que A batte B selon leurs classements."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _gain(rating_winner, rating_loser, k):
    """Points pris par le vainqueur à ce perdant."""
    return k * (1.0 - expected(rating_winner, rating_loser))


def compute(games, roster):
    """Classement Elo des joueurs de `roster` d'après `games`.

    games  : enregistrements de games.json (ordre quelconque)
    roster : noms pris en compte ; les autres joueurs sont ignorés, ce qui
             permet à un invité extérieur de jouer sans fausser le classement.

    Retourne une liste triée du meilleur au moins bon.
    """
    roster = list(roster)
    inscrits = set(roster)
    ratings = {name: START for name in roster}
    played = {name: 0 for name in roster}
    won = {name: 0 for name in roster}
    last_delta = {name: 0.0 for name in roster}

    for game in sorted(games, key=lambda g: g.get("id", 0)):
        winner = game.get("winner")
        if not winner or winner not in inscrits:
            continue
        losers = [p for p in game.get("players", [])
                  if p != winner and p in inscrits]
        if not losers:
            continue          # aucun adversaire inscrit : la partie n'apprend rien

        # K réparti entre les adversaires : une partie vaut un duel, quel que
        # soit le nombre de joueurs autour de la cible.
        k = K / len(losers)

        # Tous les duels sont évalués sur les classements d'AVANT la partie,
        # puis appliqués : sinon le résultat dépendrait de l'ordre des
        # adversaires dans la liste, ce qui n'a aucun sens.
        deltas = {loser: _gain(ratings[winner], ratings[loser], k) for loser in losers}
        for loser, d in deltas.items():
            ratings[loser] -= d
            played[loser] += 1
            last_delta[loser] = -d
        ratings[winner] += sum(deltas.values())
        played[winner] += 1
        won[winner] += 1
        last_delta[winner] = sum(deltas.values())

    classement = [
        {
            "name": name,
            "elo": round(ratings[name]),
            "games": played[name],
            "wins": won[name],
            "last_delta": round(last_delta[name]),
        }
        for name in roster
    ]
    classement.sort(key=lambda r: (-r["elo"], r["name"].lower()))
    for i, row in enumerate(classement, 1):
        row["rank"] = i
    return classement
