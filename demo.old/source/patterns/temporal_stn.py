"""Small exact-integer simple temporal network with replayable path certificates.

Edge(left, right, upper) means left - right <= upper. Strict inequalities
compile to <= -1 only because the bounded-time profile declares a discrete grid.
Internal sums use Python integers; no floating point or saturation arithmetic.
"""
from dataclasses import dataclass

ZERO = '$zero'


@dataclass(frozen=True)
class Edge:
    id: str
    left: str
    right: str
    upper: int


class Network:
    def __init__(self, variables, edges):
        self.names = sorted(set(variables) | {ZERO})
        self.index = {name: i for i, name in enumerate(self.names)}
        self.edges = tuple(edges)
        assert len({e.id for e in edges}) == len(edges), 'Duplicate edge identifier'
        n = len(self.names)
        self.distance = [[None] * n for _ in range(n)]
        self.paths = [[[] for _ in range(n)] for _ in range(n)]
        self.negative_cycle = None
        for i in range(n):
            self.distance[i][i] = 0
        for edge in edges:
            i, j = self.index[edge.right], self.index[edge.left]
            old = self.distance[i][j]
            if old is None or edge.upper < old:
                self.distance[i][j] = edge.upper
                self.paths[i][j] = [edge.id]
        for i in range(n):
            if self.distance[i][i] < 0:
                self.negative_cycle = self.paths[i][i]
                return
        for k in range(n):
            for i in range(n):
                if self.distance[i][k] is None:
                    continue
                for j in range(n):
                    if self.distance[k][j] is None:
                        continue
                    candidate = self.distance[i][k] + self.distance[k][j]
                    old = self.distance[i][j]
                    if old is None or candidate < old:
                        self.distance[i][j] = candidate
                        self.paths[i][j] = self.paths[i][k] + self.paths[k][j]
                        if i == j and candidate < 0:
                            self.negative_cycle = self.paths[i][j]
                            return

    @property
    def feasible(self):
        return self.negative_cycle is None

    def bound(self, left, right):
        assert self.feasible
        i, j = self.index[right], self.index[left]
        return self.distance[i][j], self.paths[i][j]

    def witness(self):
        """Shortest-path potentials shifted so the distinguished origin is zero."""
        assert self.feasible
        potentials = {name: min(row[j] for row in self.distance if row[j] is not None)
                      for j, name in enumerate(self.names)}
        origin = potentials[ZERO]
        return {name: value - origin for name, value in potentials.items() if name != ZERO}


def satisfies(assignment, edge):
    values = {**assignment, ZERO: 0}
    return values[edge.left] - values[edge.right] <= edge.upper
