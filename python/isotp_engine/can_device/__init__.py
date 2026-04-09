from __future__ import annotations

from .fake import FakeEcu
from .interface import CanDeviceInterface
from .worker import CanTpClient, CanTpWorker
from ..common.backend_registry import BackendRegistry

_BACKENDS = BackendRegistry(
    kind="CAN",
    entries={
        "toomoss": (".backends.toomoss", "Toomoss"),
        "pcan": (".backends.pcan", "Pcan"),
        "vector": (".backends.vector", "Vector"),
        "tsmaster": (".backends.tsmaster", "TSMaster"),
    },
)


def get_backend(name: str):
    return _BACKENDS.get(name, __name__)


def available_backends():
    return _BACKENDS.available(__name__)


__all__ = [
    "CanDeviceInterface",
    "CanTpClient",
    "CanTpWorker",
    "FakeEcu",
    "available_backends",
    "get_backend",
]
