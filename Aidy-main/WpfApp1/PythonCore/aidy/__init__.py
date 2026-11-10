"""Aidy core; lightweight imports keep pure logic tests independent of devices."""

def __getattr__(name):
    if name == "Aidy":
        from .assistant import Aidy
        return Aidy
    raise AttributeError(name)
