from flask import Flask, jsonify, request, send_from_directory
import os
import sys
import json
import time
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from game.game import Game
from game import elo, tournament

app = Flask(__name__, static_folder="../ui/static", template_folder="../ui/templates")

_game = None

DATA_DIR     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")
GAMES_FILE   = os.path.join(DATA_DIR, "games.json")
TOURNAMENT_FILE = os.path.join(DATA_DIR, "tournament.json")

# Création automatique du dossier data/ et des fichiers s'ils n'existent pas
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(PLAYERS_FILE):
    json.dump({"players": []}, open(PLAYERS_FILE, "w"))
if not os.path.exists(GAMES_FILE):
    json.dump([], open(GAMES_FILE, "w"))


# ------------------------------------------------------------------
# Helpers fichiers
# ------------------------------------------------------------------
def load_saved_players():
    with open(PLAYERS_FILE, "r") as f:
        return json.load(f)["players"]

def save_players(players):
    with open(PLAYERS_FILE, "w") as f:
        json.dump({"players": players}, f, indent=2)

def load_games():
    with open(GAMES_FILE, "r") as f:
        return json.load(f)

def load_tournament():
    """Le tournoi en cours, ou None s'il n'y en a pas."""
    if not os.path.exists(TOURNAMENT_FILE):
        return None
    try:
        with open(TOURNAMENT_FILE, "r") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None

def save_tournament(data):
    with open(TOURNAMENT_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def archive_game(game):
    """Sauvegarde la partie dans games.json et retourne son id.

    Si la partie a déjà été archivée (reprise depuis l'historique, ou
    archivée automatiquement), son enregistrement est mis à jour au lieu
    d'en créer un doublon.
    """
    games = load_games()
    existing_id = getattr(game, "archive_id", None)
    previous = next((g for g in games if g["id"] == existing_id), None) if existing_id else None

    record = {
        "id": previous["id"] if previous else int(time.time() * 1000),
        "date": previous["date"] if previous else datetime.now().strftime("%d/%m/%Y %H:%M"),
        "mode": game.mode,
        "double_in": getattr(game.rules, "double_in", False),
        "double_out": getattr(game.rules, "double_out", True),
        "players": [p.name for p in game.players],
        "cut_throat": game.cut_throat,
        "winner": game.winner.name if game.winner else None,
        # Renseigné seulement pour une partie lancée depuis la page tournoi :
        # c'est ce qui permet au tableau de se remplir tout seul.
        "tournament_match": getattr(game, "tournament_match", None),
        "turns": [
            {
                "player": game.players[i].name,
                "history": game.players[i].history,
            }
            for i in range(len(game.players))
        ],
    }
    games = [g for g in games if g["id"] != record["id"]]
    games.insert(0, record)
    with open(GAMES_FILE, "w") as f:
        json.dump(games, f, indent=2, ensure_ascii=False)
    game.archive_id = record["id"]
    return record["id"]


def auto_archive_unfinished(game):
    """Archive la partie en cours avant qu'elle soit remplacée.

    Ne fait rien si la partie est terminée (déjà archivée à la victoire)
    ou vierge (aucune flèche lancée). Les flèches du tour en cours sont
    d'abord intégrées à l'historique pour ne rien perdre.
    """
    if game is None or game.winner:
        return
    if not (game.turn_throws or any(p.history for p in game.players)):
        return
    if game.turn_throws:
        game.end_turn()
    archive_game(game)


def get_game():
    if _game is None:
        return None, jsonify({"error": "Aucune partie en cours"}), 400
    return _game, None, None


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


# ------------------------------------------------------------------
# API parties
# ------------------------------------------------------------------
def state_payload(game):
    """État de la partie tel que l'interface l'attend.

    Enrichit la vue du moteur avec le contexte tournoi. `Game` l'ignore
    volontairement : il connaît les règles, pas le calendrier. Toutes les
    routes qui renvoient un état passent par ici, sans quoi le bandeau
    « match de tournoi » disparaîtrait à chaque fléchette.
    """
    view = game.state_view()
    mid = getattr(game, "tournament_match", None)
    view["tournament_match"] = mid
    view["tournament_phase"] = None
    if mid:
        tournoi = load_tournament()
        match = next((m for m in (tournoi or {}).get("matches", [])
                      if m["id"] == mid), None)
        if match:
            view["tournament_phase"] = match["phase"]
    return view


@app.route("/api/new_game", methods=["POST"])
def new_game():
    # Appelé par le bouton "Lancer la partie" dans le navigateur (écran tactile ou téléphone).
    global _game
    data = request.json
    players  = data.get("players", [])
    mode       = data.get("mode", "501")
    double_in  = data.get("double_in", False)
    double_out = data.get("double_out", False)
    cut_throat = data.get("cut_throat", False)

    if len(players) < 1:
        return jsonify({"error": "Au moins 1 joueur requis"}), 400

    # Ne pas perdre une partie entamée : on l'archive avant de la remplacer
    auto_archive_unfinished(_game)

    _game = Game(players, mode=mode, double_in=double_in, double_out=double_out, cut_throat=cut_throat)
    return jsonify({"ok": True, "state": state_payload(_game)})


@app.route("/api/state")
def state():
    # Appelé toutes les 2 secondes par chaque appareil connecté (polling) pour rester synchronisé.
    # Aussi appelé au chargement de la page pour reprendre une partie en cours.
    game, err, code = get_game()
    if err:
        return err, code
    return jsonify(state_payload(game))


@app.route("/api/throw", methods=["POST"])
def throw():
    # Appelé par un clic sur un secteur dans le navigateur (saisie man uelle),
    # ou par le code de détection caméra (Phase 3) — même route dans les deux cas.
    game, err, code = get_game()
    if err:
        return err, code

    dart = {
        "score":      request.json["score"],
        "sector":     request.json.get("sector", 0),
        "multiplier": request.json.get("multiplier", 1),
        "zone":       request.json.get("zone", "single"),
    }
    # Coordonnées d'impact (mm) — présentes seulement pour les lancers caméra,
    # utilisées par la cible de précision. Absentes en saisie manuelle.
    if request.json.get("x") is not None and request.json.get("y") is not None:
        dart["x"] = request.json["x"]
        dart["y"] = request.json["y"]
    result = game.throw(dart)
    if result == "win":
        archive_game(game)
    return jsonify({"result": result, "state": state_payload(game)})


@app.route("/api/end_turn", methods=["POST"])
def end_turn():
    # Appelé par le bouton "Valider le tour" dans le navigateur,
    # utile quand le joueur a lancé moins de 3 flèches (miss, ou flèche tombée).
    game, err, code = get_game()
    if err:
        return err, code
    result = game.end_turn()
    if result == "win":
        archive_game(game)
    return jsonify({"result": result, "state": state_payload(game)})


@app.route("/api/undo", methods=["POST"])
def undo():
    # Appelé si besoin depuis le code (non exposé dans l'UI actuelle — réservé usage futur).
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo()
    return jsonify({"ok": ok, "state": state_payload(game)})


@app.route("/api/undo_dart", methods=["POST"])
def undo_dart():
    # Appelé par le bouton "↩ Lancer" dans le navigateur.
    # Fonctionne même si le tour est déjà validé — remonte au joueur précédent.
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo_dart()
    # Si on vient d'annuler une victoire, la partie archivée redevient
    # "non terminée" : on met à jour son enregistrement (upsert par archive_id).
    if ok and getattr(game, "archive_id", None) and not game.winner:
        archive_game(game)
    return jsonify({"ok": ok, "state": state_payload(game)})


@app.route("/api/set_score", methods=["POST"])
def set_score():
    # Appelé par le formulaire "Correction de score" dans le navigateur (X01 uniquement).
    game, err, code = get_game()
    if err:
        return err, code
    data = request.json
    game.set_score(data["player_idx"], data["score"])
    return jsonify({"ok": True, "state": state_payload(game)})


@app.route("/api/archive", methods=["POST"])
def archive():
    """Archive manuelle (bouton depuis l'écran de jeu)."""
    game, err, code = get_game()
    if err:
        return err, code
    gid = archive_game(game)
    return jsonify({"ok": True, "id": gid})


# ------------------------------------------------------------------
# Animations personnelles
#
# Un joueur peut avoir sa propre vidéo de victoire : il suffit de déposer
# un fichier à son nom dans ui/static/animations/players/. Aucun réglage,
# aucune déclaration — le rapprochement se fait sur le nom, normalisé des
# deux côtés (minuscules, sans accents ni séparateurs) pour que
# "Jean-Marc.mp4" retrouve bien le joueur "jean marc".
# ------------------------------------------------------------------
PLAYER_ANIM_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "ui", "static", "animations", "players"
)
PLAYER_ANIM_EXT = (".mp4", ".webm", ".mov", ".m4v", ".gif")


def anim_slug(name):
    """Clé de rapprochement joueur <-> fichier. Doit rester identique à slug() côté JS."""
    decomposed = unicodedata.normalize("NFD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.lower() if c.isascii() and c.isalnum())


@app.route("/api/animations/players")
def get_player_animations():
    # Appelé au chargement de la page : renvoie {slug: nom de fichier}.
    found = {}
    if os.path.isdir(PLAYER_ANIM_DIR):
        for filename in sorted(os.listdir(PLAYER_ANIM_DIR)):
            stem, ext = os.path.splitext(filename)
            if ext.lower() not in PLAYER_ANIM_EXT:
                continue
            slug = anim_slug(stem)
            if slug:
                found[slug] = filename
    return jsonify({"players": found})


# ------------------------------------------------------------------
# API joueurs
# ------------------------------------------------------------------
@app.route("/api/players", methods=["GET"])
def get_players():
    # Appelé au chargement de la page d'accueil pour afficher la liste des joueurs enregistrés.
    return jsonify({"players": load_saved_players()})


@app.route("/api/players", methods=["POST"])
def add_player():
    # Appelé quand l'utilisateur crée un nouveau joueur depuis l'écran d'accueil.
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom vide"}), 400
    players = load_saved_players()
    if name not in players:
        players.append(name)
        save_players(players)
    return jsonify({"players": players})


@app.route("/api/players/<name>", methods=["DELETE"])
def delete_player(name):
    # Appelé par le bouton ✕ sur un joueur enregistré (avec confirmation préalable).
    players = [p for p in load_saved_players() if p != name]
    save_players(players)
    return jsonify({"players": players})


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
    # Appelé par le bouton 🗑 sur une partie dans l'écran historique.
    games = [g for g in load_games() if g["id"] != game_id]
    with open(GAMES_FILE, "w") as f:
        json.dump(games, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/games", methods=["DELETE"])
def delete_all_games():
    # Appelé par le bouton "Effacer tout l'historique" (double confirmation requise).
    with open(GAMES_FILE, "w") as f:
        json.dump([], f)
    return jsonify({"ok": True})


@app.route("/api/exit_kiosk", methods=["POST"])
def exit_kiosk():
    # Appelé par le bouton "Quitter l'application" (visible seulement en mode kiosk sur le Pi).
    # Ferme chromium pour rendre la main au bureau du Pi.
    import subprocess
    subprocess.Popen(["pkill", "chromium"])
    return jsonify({"ok": True})


@app.route("/api/resume_game/<int:game_id>", methods=["POST"])
def resume_game(game_id):
    # Appelé par le bouton "Reprendre" sur une partie sans gagnant dans l'historique.
    # Reconstruit la partie en rejouant tous les tours archivés, puis reprend depuis là.
    global _game

    # Sauvegarder la partie en cours avant de la remplacer
    # (avant load_games : l'auto-archive peut réécrire games.json)
    auto_archive_unfinished(_game)

    record = next((g for g in load_games() if g["id"] == game_id), None)
    if not record:
        return jsonify({"error": "Partie non trouvée"}), 404
    if record.get("winner"):
        return jsonify({"error": "Partie déjà terminée"}), 400

    resumed = Game(
        record["players"],
        mode=record["mode"],
        double_in=record.get("double_in", False),
        double_out=record.get("double_out", True),
        cut_throat=record.get("cut_throat", False),
    )

    # Interleave des tours : p0_tour0, p1_tour0, p0_tour1, p1_tour1, ...
    player_histories = [t["history"] for t in record["turns"]]
    max_turns = max((len(h) for h in player_histories), default=0)

    for turn_idx in range(max_turns):
        for player_idx in range(len(player_histories)):
            if turn_idx >= len(player_histories[player_idx]):
                continue
            result = "added"
            for dart in player_histories[player_idx][turn_idx]:
                result = resumed.throw(dart)
                if result in ("win", "turn_end"):
                    break
            # Tour < 3 flèches ou tour vide : forcer la fin pour avancer au joueur suivant
            if result not in ("win", "turn_end"):
                resumed.end_turn()

    # Lier la partie à son enregistrement : la prochaine archive le mettra
    # à jour au lieu de créer un doublon dans l'historique
    resumed.archive_id = record["id"]
    _game = resumed

    return jsonify({"ok": True, "state": state_payload(_game)})


# ------------------------------------------------------------------
# API historique & stats
# ------------------------------------------------------------------
@app.route("/api/games")
def get_games():
    # Appelé à l'ouverture de l'écran historique pour afficher la liste des parties.
    return jsonify(load_games())


# ------------------------------------------------------------------
# API tournoi
# ------------------------------------------------------------------
def tournament_results(tournoi):
    """Résultats du tournoi : {id de match -> vainqueur}.

    Reconstruits à chaque appel depuis l'historique des parties, complétés
    par les forfaits. Une partie réellement jouée prime sur un forfait, ce
    qui permet de rejouer un match déclaré par erreur.
    """
    results = dict(tournoi.get("walkovers", {}))
    for g in load_games():
        mid = g.get("tournament_match")
        if mid and g.get("winner"):
            results[mid] = g["winner"]
    return results


def tournament_elo(tournoi=None):
    """Elo sous forme {nom: points}, pour départager les poules à égalité."""
    return {r["name"]: r["elo"] for r in elo.compute(load_games(), load_saved_players())}


@app.route("/api/tournament/structures")
def tournament_structures():
    # Structures de poules possibles pour un nombre d'inscrits donné.
    return jsonify(tournament.structures(int(request.args.get("n", 0))))


@app.route("/api/tournament", methods=["GET"])
def tournament_state():
    tournoi = load_tournament()
    if not tournoi:
        return jsonify({"exists": False})
    results = tournament_results(tournoi)
    view = tournament.view(tournoi, results, tournament_elo(tournoi))
    view["exists"] = True
    view["elo"] = elo.compute(load_games(), load_saved_players())
    return jsonify(view)


@app.route("/api/tournament", methods=["POST"])
def tournament_create():
    d = request.json
    players = d.get("players", [])
    if len(players) < 4:
        return jsonify({"error": "Au moins 4 joueurs"}), 400

    valides = tournament.structures(len(players))
    n_groups, qualify = int(d["n_groups"]), int(d["qualify"])
    if not any(s["groups"] == n_groups and s["qualify"] == qualify for s in valides):
        return jsonify({"error": "Structure de poules invalide pour ce nombre de joueurs"}), 400

    tournoi = tournament.create(
        players, mode=d.get("mode", "501"),
        options=d.get("options"), n_groups=n_groups, qualify=qualify)
    save_tournament(tournoi)
    return jsonify({"ok": True})


@app.route("/api/tournament", methods=["DELETE"])
def tournament_delete():
    # Le calendrier disparaît ; les parties jouées restent dans l'historique.
    if os.path.exists(TOURNAMENT_FILE):
        os.remove(TOURNAMENT_FILE)
    return jsonify({"ok": True})


@app.route("/api/tournament/start_match", methods=["POST"])
def tournament_start_match():
    """Lance la partie correspondant à un match du tournoi."""
    global _game
    tournoi = load_tournament()
    if not tournoi:
        return jsonify({"error": "Aucun tournoi"}), 400

    mid = request.json["id"]
    match = next((m for m in tournoi["matches"] if m["id"] == mid), None)
    if not match:
        return jsonify({"error": "Match inconnu"}), 404

    results = tournament_results(tournoi)
    if mid in results:
        return jsonify({"error": "Match déjà joué"}), 400
    players = tournament.match_players(tournoi, match, results, tournament_elo(tournoi))
    if not all(players):
        return jsonify({"error": "Les joueurs de ce match ne sont pas encore connus"}), 400

    auto_archive_unfinished(_game)
    opts = tournoi.get("options", {})
    _game = Game(players, mode=tournoi["mode"],
                 double_in=opts.get("double_in", False),
                 double_out=opts.get("double_out", True),
                 cut_throat=opts.get("cut_throat", False))
    _game.tournament_match = mid
    return jsonify({"ok": True, "state": state_payload(_game)})


@app.route("/api/tournament/walkover", methods=["POST"])
def tournament_walkover():
    """Déclare un vainqueur sans jouer (départ anticipé, blessure, oubli).

    Ne compte pas pour l'Elo : aucune partie n'est créée.
    """
    tournoi = load_tournament()
    if not tournoi:
        return jsonify({"error": "Aucun tournoi"}), 400
    d = request.json
    match = next((m for m in tournoi["matches"] if m["id"] == d["id"]), None)
    if not match:
        return jsonify({"error": "Match inconnu"}), 404

    players = tournament.match_players(tournoi, match, tournament_results(tournoi),
                                       tournament_elo(tournoi))
    if d["winner"] not in players:
        return jsonify({"error": "Ce joueur ne dispute pas ce match"}), 400

    tournoi.setdefault("walkovers", {})[d["id"]] = d["winner"]
    save_tournament(tournoi)
    return jsonify({"ok": True})


@app.route("/api/tournament/reset_match", methods=["POST"])
def tournament_reset_match():
    """Annule le résultat d'un match : il redevient jouable."""
    tournoi = load_tournament()
    if not tournoi:
        return jsonify({"error": "Aucun tournoi"}), 400
    mid = request.json["id"]

    tournoi.get("walkovers", {}).pop(mid, None)
    save_tournament(tournoi)

    games = [g for g in load_games() if g.get("tournament_match") != mid]
    with open(GAMES_FILE, "w") as f:
        json.dump(games, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/elo")
def get_elo():
    # Classement Elo de TOUS les joueurs enregistrés : toutes les parties
    # comptent, tournoi ou non. Ceux qui n'ont pas encore joué restent à 1000.
    return jsonify({"ranking": elo.compute(load_games(), load_saved_players())})


@app.route("/api/stats")
def get_stats():
    # Appelé à l'ouverture de l'onglet "Stats joueurs" dans l'écran historique.
    games   = load_games()
    players = load_saved_players()
    result  = {}

    for player in players:
        player_games = [g for g in games if player in g["players"]]
        won = [g for g in player_games if g.get("winner") == player]

        total_turns = 0
        total_score = 0
        best_turn   = 0

        for g in player_games:
            if g["mode"] not in ("301", "501", "701"):
                continue
            for turn_data in g.get("turns", []):
                if turn_data["player"] != player:
                    continue
                for turn in turn_data["history"]:
                    score = sum(t["score"] for t in turn)
                    total_score += score
                    total_turns += 1
                    if score > best_turn:
                        best_turn = score

        result[player] = {
            "games_played": len(player_games),
            "games_won":    len(won),
            "win_rate":     round(len(won) / len(player_games) * 100) if player_games else 0,
            "avg_turn":     round(total_score / total_turns) if total_turns else 0,
            "best_turn":    best_turn,
        }

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
