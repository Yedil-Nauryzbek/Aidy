from typing import TYPE_CHECKING

__all__ = ["Aidy"]

if TYPE_CHECKING:
    from .assistant import Aidy as Aidy


def __getattr__(name: str):
    if name == "Aidy":
        from .assistant import Aidy as _Aidy

        return _Aidy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
