SEQUENCE = list(range(1, 21)) + ["bull"]


class Clock:
    """Tour de l'horloge : atteindre les secteurs 1→20 puis le bull, dans l'ordre.
    Simple = avance 1, Double = avance 2, Triple = avance 3.
    """

    def init_player_state(self):
        return {"target_idx": 0}

    def apply_turn(self, state, throws):
        """Rejoue tous les lancers depuis l'état initial. Retourne (état_final, résultat)."""
        current = dict(state)
        for dart in throws:
            current, result = self._apply_dart(current, dart)
            if result == "win":
                return current, "win"
        return current, "ok"

    def _apply_dart(self, state, dart):
        idx = state["target_idx"]
        if idx >= len(SEQUENCE):
            return state, "win"
        target = SEQUENCE[idx]

        if target == "bull":
            if dart["zone"] in ("bull", "outer_bull"):
                return {"target_idx": idx}, "win"
            return state, "ok"

        if dart.get("sector") == target and dart["zone"] != "miss":
            advance = dart.get("multiplier", 1)
            new_idx = min(idx + advance, len(SEQUENCE) - 1)
            return {"target_idx": new_idx}, "ok"

        return state, "ok"
