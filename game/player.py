class Player:
    def __init__(self, name):
        self.name = name
        self.history = []  # liste de listes de throws par tour

    def add_turn(self, throws):
        self.history.append(list(throws))

    def undo_last_turn(self):
        if self.history:
            return self.history.pop()
        return None

    def total_throws(self):
        return sum(len(t) for t in self.history)

    def __repr__(self):
        return f"Player({self.name})"
