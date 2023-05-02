from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, sin, sqrt
from typing import Any, Callable, overload, Set, TypeVar

T = TypeVar("T")

Subscriber = Callable[[Any], None]
Unsubscriber = Callable[[], None]


class Observable:
    def __init__(self):
        self._observers: Set[Subscriber] = set()

    def subscribe(self, fn: Subscriber):
        self._observers.add(fn)

    def unregister(self, fn: Subscriber):
        self._observers.remove(fn)


class Broadcast(Observable):
    def __init__(self):
        super().__init__()

    def broadcast(self, payload: Any):
        for fn in self._observers:
            try:
                fn(payload)
            except:
                pass


@dataclass
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, rhs: Vec2) -> Vec2:
        return Vec2(self.x + rhs.x, self.y + rhs.y)

    def __sub__(self, rhs: Vec2) -> Vec2:
        return Vec2(self.x - rhs.x, self.y - rhs.y)

    @overload
    def __mul__(self, rhs: float) -> Vec2:
        ...

    @overload
    def __mul__(self, rhs: Vec2) -> float:
        ...

    def __mul__(self, rhs: float | Vec2) -> float | Vec2:
        if isinstance(rhs, Vec2):
            return self.x * rhs.x + self.y * rhs.y
        else:
            return Vec2(self.x * rhs, self.y * rhs)

    def __rmul__(self, lhs: float) -> Vec2:
        return self * lhs

    def __str__(self) -> str:
        return "Vec2({:.2f}, {:.2f})".format(self.x, self.y)

    def angle(self) -> float:
        return atan2(self.y, self.x)

    @overload
    def clip(self, bound: float, /) -> Vec2:
        ...

    @overload
    def clip(self, min_value: float, max_value: float, /) -> Vec2:
        ...

    def clip(self, min_value: float, max_value: float | None = None) -> Vec2:
        if max_value is None:
            max_value = abs(min_value)
            min_value = -max_value

        return Vec2(
            clip(self.x, min_value, max_value), clip(self.y, min_value, max_value)
        )

    def limit_mag(self, mag: float) -> Vec2:
        if self.mag() >= mag:
            return self.set_mag(mag)

        return self

    def mag(self) -> float:
        return sqrt(self * self)

    def rotate(self, angle: float) -> Vec2:
        s, c = sin(angle), cos(angle)
        return Vec2(self.x * c - self.y * s, self.x * s + self.y * c)

    def set_mag(self, mag: float) -> Vec2:
        return (mag / self.mag()) * self


def clip(value: float, min_value: float, max_value: float) -> float:
    return min(max(value, min_value), max_value)
