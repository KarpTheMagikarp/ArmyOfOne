"""
How far one of the Mods drawer's steps has got, as the percent on its line.

A step is a few parts of different work, and a count of any one of them says
little about the rest: four hundred files hashed in a second, then the same
four hundred deflated for a minute. So each part is weighed up front by what
it costs, and the percent is how much of the whole is done, whichever parts
that's in. A part that turns out to have less to do than it was weighed at is
credited when that's known, rather than in one leap when its turn comes. The
number only climbs, and it says a hundred once the whole step is through
rather than when its last part is, since what comes after that isn't nothing.
"""


class Meter:
    """`say(done, of)` hears each whole percent once, out of a hundred, starting at 0"""

    def __init__(self, say=None):
        self._say = say
        self._costs, self._done = {}, {}
        self._total = 0.0
        self._shown = -1
        self._tell(0)

    def split(self, **costs) -> None:
        """each part of the step by what it costs, all in one unit"""
        self._costs = {name: max(0.0, cost) for name, cost in costs.items()}
        self._done = dict.fromkeys(self._costs, 0.0)
        self._total = sum(self._costs.values())

    def add(self, name: str, cost: float) -> None:
        """that much more of one part done"""
        self._set(name, self._done[name] + cost)

    def at(self, name: str, done: float, of: float) -> None:
        """a part whoever's doing it reports as `done` of `of`. Nothing to do is all of it"""
        self._set(name, self._costs[name] * (min(max(done / of, 0.0), 1.0) if of > 0 else 1.0))

    def end(self) -> None:
        self._tell(100)

    def _set(self, name: str, done: float) -> None:
        self._done[name] = min(max(self._done[name], done), self._costs[name])
        if self._total:
            self._tell(min(99, int(100 * sum(self._done.values()) / self._total)))

    def _tell(self, whole: int) -> None:
        if self._say is not None and whole > self._shown:
            self._shown = whole
            self._say(whole, 100)
