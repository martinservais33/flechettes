from flask import Flask, jsonify, request, send_from_directory
import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from game.game import Game

app = Flask(__name__, static_folder="../ui/static", template_folder="../ui/templates")

_game = None

DATA_DIR     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")
GAMES_FILE   = os.path.join(DATA_DIR, "games.json")

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
@app.route("/api/new_game", methods=["POST"])
def new_game():
    # Appelé par le bouton "Lancer la partie" dans le navigateur (écran tactile ou téléphone).
    global _game
    data = request.json
    players  = data.get("players", [])
    mode       = data.get("mode", "501")
    double_in  = data.get("double_in", False)
    double_out = data.get("double_out", True)
    cut_throat = data.get("cut_throat", False)

    if len(players) < 1:
        return jsonify({"error": "Au moins 1 joueur requis"}), 400

    # Ne pas perdre une partie entamée : on l'archive avant de la remplacer
    auto_archive_unfinished(_game)

    _game = Game(players, mode=mode, double_in=double_in, double_out=double_out, cut_throat=cut_throat)
    return jsonify({"ok": True, "state": _game.state_view()})


@app.route("/api/state")
def state():
    # Appelé toutes les 2 secondes par chaque appareil connecté (polling) pour rester synchronisé.
    # Aussi appelé au chargement de la page pour reprendre une partie en cours.
    game, err, code = get_game()
    if err:
        return err, code
    return jsonify(game.state_view())


@app.route("/api/throw", methods=["POST"])
def throw():
    # Appelé par un clic sur un secteur dans le navigateur (saisie manuelle),
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
    result = game.throw(dart)
    if result == "win":
        archive_game(game)
    return jsonify({"result": result, "state": game.state_view()})


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
    return jsonify({"result": result, "state": game.state_view()})


@app.route("/api/undo", methods=["POST"])
def undo():
    # Appelé si besoin depuis le code (non exposé dans l'UI actuelle — réservé usage futur).
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo()
    return jsonify({"ok": ok, "state": game.state_view()})


@app.route("/api/undo_dart", methods=["POST"])
def undo_dart():
    # Appelé par le bouton "↩ Lancer" dans le navigateur.
    # Fonctionne même si le tour est déjà validé — remonte au joueur précédent.
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo_dart()
    return jsonify({"ok": ok, "state": game.state_view()})


@app.route("/api/set_score", methods=["POST"])
def set_score():
    # Appelé par le formulaire "Correction de score" dans le navigateur (X01 uniquement).
    game, err, code = get_game()
    if err:
        return err, code
    data = request.json
    game.set_score(data["player_idx"], data["score"])
    return jsonify({"ok": True, "state": game.state_view()})


@app.route("/api/archive", methods=["POST"])
def archive():
    """Archive manuelle (bouton depuis l'écran de jeu)."""
    game, err, code = get_game()
    if err:
        return err, code
    gid = archive_game(game)
    return jsonify({"ok": True, "id": gid})


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

    return jsonify({"ok": True, "state": _game.state_view()})


# ------------------------------------------------------------------
# API historique & stats
# ------------------------------------------------------------------
@app.route("/api/games")
def get_games():
    # Appelé à l'ouverture de l'écran historique pour afficher la liste des parties.
    return jsonify(load_games())


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
