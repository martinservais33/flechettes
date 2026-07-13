from game.game import Game

def make_throw(score, sector, multiplier, zone):
    return {"score": score, "sector": sector, "multiplier": multiplier, "zone": zone}

def test_501():
    print("=== Test 501 ===")
    g = Game(["Martin", "Antoine"], mode="501")

    g.throw(make_throw(60, 20, 3, "triple"))
    g.throw(make_throw(60, 20, 3, "triple"))
    g.throw(make_throw(60, 20, 3, "triple"))  # tour 1 : -180 → 321

    print(g.state_view()["players"][0]["state"])  # score: 321

    g.throw(make_throw(1, 1, 1, "single"))
    g.throw(make_throw(1, 1, 1, "single"))
    g.throw(make_throw(1, 1, 1, "single"))  # tour Antoine

    g.undo()
    print("Après undo Antoine:", g.state_view()["current_player"])  # Antoine

def test_cricket():
    print("\n=== Test Cricket ===")
    g = Game(["Martin", "Antoine"], mode="cricket")

    # Martin ferme le 20 en un coup (triple)
    g.throw(make_throw(60, 20, 3, "triple"))
    g.throw(make_throw(40, 20, 2, "double"))  # 2 marks sur le 19
    res = g.end_turn()
    print("Tour Martin:", res)
    print("Marks Martin:", g.states[0]["marks"])

def test_dartboard():
    print("\n=== Test Dartboard ===")
    from scoring.dartboard import compute_score
    import math

    centre = (225, 225)
    rayon = 225
    offset = math.atan2(1, 0)  # 12h = haut

    # Impact au centre = bull
    r = compute_score((225, 225), centre, rayon, offset)
    print("Centre:", r)

    # Impact à 3/4 du rayon vers le haut = 20 simple
    r = compute_score((225, 50), centre, rayon, offset)
    print("Haut (20?):", r)

if __name__ == "__main__":
    test_dartboard()
    test_501()
    test_cricket()
