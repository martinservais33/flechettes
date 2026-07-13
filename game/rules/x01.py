class X01:
    """
    Règles pour les jeux 301, 501, 701...
    - Double in (optionnel) : doit commencer par un double pour commencer à compter
    - Double out : doit terminer exactement sur un double (ou bull)
    """

    def __init__(self, start_score=501, double_in=False, double_out=True):
        self.start_score = start_score
        self.double_in = double_in
        self.double_out = double_out

    def init_player_state(self):
        return {
            "score": self.start_score,
            "locked_in": not self.double_in,  # False si double_in requis
        }

    def apply_turn(self, state, throws):
        """
        throws : liste de dicts {score, multiplier, zone, ...} (max 3)
        Retourne (new_state, result)
          result : "ok" | "bust" | "win"
        """
        score = state["score"]
        locked_in = state["locked_in"]

        for throw in throws:
            if not locked_in:
                if throw["multiplier"] == 2:
                    locked_in = True
                else:
                    continue  # on ignore jusqu'au double

            new_score = score - throw["score"]

            if new_score < 0:
                return state, "bust"

            if new_score == 0:
                if self.double_out:
                    if throw["multiplier"] == 2 or throw["zone"] == "bull":
                        return {**state, "score": 0, "locked_in": locked_in}, "win"
                    else:
                        return state, "bust"
                else:
                    return {**state, "score": 0, "locked_in": locked_in}, "win"

            if new_score == 1 and self.double_out:
                return state, "bust"

            score = new_score

        return {"score": score, "locked_in": locked_in}, "ok"

    def can_finish(self, score):
        """Retourne True si le score est atteignable en un tour (pour UI)."""
        if not self.double_out:
            return score <= 60
        if score > 170:
            return False
        # scores impossibles en double out
        impossible = {169, 168, 166, 165, 163, 162, 159}
        return score not in impossible
