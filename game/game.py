from game.player import Player
from game.rules.x01 import X01
from game.rules.cricket import Cricket
from game.rules.clock import Clock


class Game:
    def __init__(self, players, mode="501", double_in=False, double_out=False, cut_throat=False):
        self.players = [Player(name) for name in players]
        self.mode = mode
        self.cut_throat = cut_throat
        self.current_idx = 0
        self.turn_throws = []
        self.winner = None
        self.history = []

        if mode in ("301", "501", "701"):
            self.rules = X01(
                start_score=int(mode),
                double_in=double_in,
                double_out=double_out,
            )
            self.states = [self.rules.init_player_state() for _ in self.players]
        elif mode == "cricket":
            self.rules = Cricket(cut_throat=cut_throat)
            self.states = [self.rules.init_player_state() for _ in self.players]
        elif mode == "clock":
            self.rules = Clock()
            self.states = [self.rules.init_player_state() for _ in self.players]
        else:
            raise ValueError(f"Mode inconnu : {mode}")

    # ------------------------------------------------------------------
    # Lancer une flèche dans le tour en cours
    # ------------------------------------------------------------------
    def throw(self, dart):
        """
        dart : dict {score, sector, multiplier, zone, ...}
        Retourne "added" | "turn_end" | "win"
        """
        if self.winner:
            return "win"

        self.turn_throws.append(dart)

        # vérifier la victoire après chaque flèche (apply_turn rejoue depuis l'état initial du tour)
        if self.mode == "cricket":
            _, result = self.rules.apply_turn(
                [{"marks": dict(s["marks"]), "score": s["score"]} for s in self.states],
                self.current_idx,
                self.turn_throws
            )
        else:
            _, result = self.rules.apply_turn(
                {**self.states[self.current_idx]},
                self.turn_throws
            )

        if result == "win":
            return self._end_turn()

        # Dépassement (bust) : le tour s'arrête immédiatement, le score
        # revient à sa valeur de début de tour, on passe au joueur suivant.
        if result == "bust":
            self._end_turn()
            return "bust"

        if len(self.turn_throws) == 3:
            return self._end_turn()

        return "added"

    # ------------------------------------------------------------------
    # Forcer la fin du tour (bouton "Valider" sur l'UI)
    # ------------------------------------------------------------------
    def end_turn(self):
        if self.winner:
            return "win"
        return self._end_turn()

    # ------------------------------------------------------------------
    # Interne : appliquer le tour aux règles
    # ------------------------------------------------------------------
    def _end_turn(self):
        idx = self.current_idx
        throws = list(self.turn_throws)

        # snapshot pour undo
        self.history.append({
            "idx": idx,
            "states": [
                {k: (dict(v) if isinstance(v, dict) else v) for k, v in s.items()}
                for s in self.states
            ],
            "throws": throws,
        })

        if self.mode == "cricket":
            new_states, result = self.rules.apply_turn(self.states, idx, throws)
            self.states = new_states
        else:
            new_state, result = self.rules.apply_turn(self.states[idx], throws)
            self.states[idx] = new_state

        self.players[idx].add_turn(throws)
        self.turn_throws = []

        if result == "win":
            self.winner = self.players[idx]
            return "win"

        self.current_idx = (self.current_idx + 1) % len(self.players)
        return "turn_end"

    # ------------------------------------------------------------------
    # Annuler le dernier lancer (tour en cours ou tour précédent)
    # ------------------------------------------------------------------
    def undo_dart(self):
        if self.turn_throws:
            self.turn_throws.pop()
            return True
        if not self.history:
            return False
        # Tour déjà validé : on restaure l'état d'avant ce tour
        # et on remet les n-1 lancers en cours
        snap = self.history.pop()
        self.states = snap["states"]
        self.current_idx = snap["idx"]
        self.players[snap["idx"]].undo_last_turn()
        self.winner = None
        self.turn_throws = snap["throws"][:-1]
        return True

    # ------------------------------------------------------------------
    # Annuler le dernier tour complet
    # ------------------------------------------------------------------
    def undo(self):
        if not self.history:
            return False

        snap = self.history.pop()
        self.states = snap["states"]
        self.current_idx = snap["idx"]
        self.players[snap["idx"]].undo_last_turn()
        self.turn_throws = []
        self.winner = None
        return True

    # ------------------------------------------------------------------
    # Modifier manuellement le score d'un joueur (correction UI)
    # ------------------------------------------------------------------
    def set_score(self, player_idx, new_score):
        if self.mode != "cricket":
            self.states[player_idx]["score"] = new_score

    # ------------------------------------------------------------------
    # Vue de l'état courant (pour l'UI)
    # ------------------------------------------------------------------
    def state_view(self):
        view = {
            "mode": self.mode,
            "cut_throat": self.cut_throat,
            "current_player": self.players[self.current_idx].name,
            "current_throws": self.turn_throws,
            "winner": self.winner.name if self.winner else None,
            "players": [
                {
                    "name": p.name,
                    "state": self.states[i],
                    "last_throws": p.history[-1] if p.history else [],
                    "history": p.history,
                }
                for i, p in enumerate(self.players)
            ],
        }
        # État "live" du joueur courant : intègre les lancers du tour en cours
        # (pas encore validés) pour afficher le score/les marques à chaque flèche.
        idx = self.current_idx
        if self.mode == "cricket":
            live_states, _ = self.rules.apply_turn(
                [{"marks": dict(s["marks"]), "score": s["score"]} for s in self.states],
                idx, self.turn_throws
            )
            view["current_live_state"] = live_states[idx]
        else:
            live, _ = self.rules.apply_turn(dict(self.states[idx]), self.turn_throws)
            view["current_live_state"] = live
        return view
