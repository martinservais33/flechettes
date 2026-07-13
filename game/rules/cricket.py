CRICKET_TARGETS = [15, 16, 17, 18, 19, 20, 25]  # 25 = bull


class Cricket:
    """
    Cricket standard ou Cut-throat.
    - Standard   : points pour soi sur numéros fermés par soi, pas les autres. Gagne score le + haut.
    - Cut-throat : points pour les adversaires qui n'ont pas fermé. Gagne score le + bas.
    """

    def __init__(self, cut_throat=False):
        self.cut_throat = cut_throat

    def init_player_state(self):
        return {
            "marks": {t: 0 for t in CRICKET_TARGETS},
            "score": 0,
        }

    def apply_turn(self, states, current_idx, throws):
        new_states = [
            {"marks": dict(s["marks"]), "score": s["score"]}
            for s in states
        ]
        current = new_states[current_idx]

        for throw in throws:
            target = self._throw_to_target(throw)
            if target is None:
                continue

            zone = throw["zone"]
            if zone == "miss":
                hits = 0
            elif zone == "bull":
                hits = 2
            elif zone == "outer_bull":
                hits = 1
            else:
                hits = throw["multiplier"]

            remaining = 3 - current["marks"].get(target, 0)
            if remaining > 0:
                added = min(hits, remaining)
                current["marks"][target] = current["marks"].get(target, 0) + added
                hits -= added

            if hits > 0 and current["marks"].get(target, 0) >= 3:
                if self.cut_throat:
                    # points vont aux adversaires qui n'ont pas fermé
                    for i, s in enumerate(new_states):
                        if i != current_idx and s["marks"].get(target, 0) < 3:
                            s["score"] += target * hits
                else:
                    # points pour soi si au moins un adversaire n'a pas fermé
                    others_closed = all(
                        s["marks"].get(target, 0) >= 3
                        for i, s in enumerate(new_states)
                        if i != current_idx
                    )
                    if not others_closed:
                        current["score"] += target * hits

        if self._is_winner(new_states, current_idx):
            return new_states, "win"

        return new_states, "ok"

    def _throw_to_target(self, throw):
        zone = throw.get("zone")
        if zone == "miss":
            return None
        if zone in ("bull", "outer_bull"):
            return 25
        sector = throw.get("sector")
        if sector in CRICKET_TARGETS:
            return sector
        return None

    def _is_winner(self, states, idx):
        current = states[idx]
        if not all(current["marks"].get(t, 0) >= 3 for t in CRICKET_TARGETS):
            return False
        if self.cut_throat:
            # gagne celui avec le moins de points (tous numéros fermés)
            return current["score"] <= min(
                s["score"] for i, s in enumerate(states) if i != idx
            )
        else:
            return current["score"] >= max(
                (s["score"] for i, s in enumerate(states) if i != idx),
                default=0
            )
