from __future__ import annotations

from .interface import LinMasterDeviceInterface
from .worker import LinTpWorker
from ..common.backend_registry import BackendRegistry

_BACKENDS = BackendRegistry(
    kind="LIN",
    entries={
        "toomoss": (".backends.toomoss", "ToomossLin"),
    },
)


def get_backend(name: str):
    return _BACKENDS.get(name, __name__)


def available_backends():
    return _BACKENDS.available(__name__)


__all__ = [
    "LinMasterDeviceInterface",
    "LinTpWorker",
    "available_backends",
    "get_backend",
]
