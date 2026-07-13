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
    """Sauvegarde la partie dans games.json et retourne son id."""
    games = load_games()
    record = {
        "id": int(time.time() * 1000),
        "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "mode": game.mode,
        "double_in": getattr(game.rules, "double_in", False),
        "double_out": getattr(game.rules, "double_out", True),
        "players": [p.name for p in game.players],
        "winner": game.winner.name if game.winner else None,
        "turns": [
            {
                "player": game.players[i].name,
                "history": game.players[i].history,
            }
            for i in range(len(game.players))
        ],
    }
    games.insert(0, record)
    with open(GAMES_FILE, "w") as f:
        json.dump(games, f, indent=2, ensure_ascii=False)
    return record["id"]


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
    global _game
    data = request.json
    players  = data.get("players", [])
    mode       = data.get("mode", "501")
    double_in  = data.get("double_in", False)
    double_out = data.get("double_out", True)
    cut_throat = data.get("cut_throat", False)

    if len(players) < 1:
        return jsonify({"error": "Au moins 1 joueur requis"}), 400

    _game = Game(players, mode=mode, double_in=double_in, double_out=double_out, cut_throat=cut_throat)
    return jsonify({"ok": True, "state": _game.state_view()})


@app.route("/api/state")
def state():
    game, err, code = get_game()
    if err:
        return err, code
    return jsonify(game.state_view())


@app.route("/api/throw", methods=["POST"])
def throw():
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
    game, err, code = get_game()
    if err:
        return err, code
    result = game.end_turn()
    if result == "win":
        archive_game(game)
    return jsonify({"result": result, "state": game.state_view()})


@app.route("/api/undo", methods=["POST"])
def undo():
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo()
    return jsonify({"ok": ok, "state": game.state_view()})


@app.route("/api/undo_dart", methods=["POST"])
def undo_dart():
    game, err, code = get_game()
    if err:
        return err, code
    ok = game.undo_dart()
    return jsonify({"ok": ok, "state": game.state_view()})


@app.route("/api/set_score", methods=["POST"])
def set_score():
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
    return jsonify({"players": load_saved_players()})


@app.route("/api/players", methods=["POST"])
def add_player():
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
    players = [p for p in load_saved_players() if p != name]
    save_players(players)
    return jsonify({"players": players})


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def delete_game(game_id):
    games = [g for g in load_games() if g["id"] != game_id]
    with open(GAMES_FILE, "w") as f:
        json.dump(games, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/games", methods=["DELETE"])
def delete_all_games():
    with open(GAMES_FILE, "w") as f:
        json.dump([], f)
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# API historique & stats
# ------------------------------------------------------------------
@app.route("/api/games")
def get_games():
    return jsonify(load_games())


@app.route("/api/stats")
def get_stats():
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
