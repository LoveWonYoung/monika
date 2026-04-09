from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class IsoTpError(RuntimeError):
    def __init__(self, code: int, message: Optional[str] = None):
        from ._ffi import ERR_MAP

        self.code = int(code)
        self.name = ERR_MAP.get(self.code, "UnknownError")
        super().__init__(message or f"{self.name} ({self.code})")


class UdsNegativeResponseError(RuntimeError):
    def __init__(self, response: bytes):
        from .helpers import parse_uds_negative_response

        self.response = bytes(response)
        _, service_id, nrc = parse_uds_negative_response(response)
        self.service_id = service_id
        self.nrc = nrc
        super().__init__(f"UDS negative response sid=0x{service_id:02X} nrc=0x{nrc:02X}")


@dataclass(frozen=True)
class TpConfig:
    n_bs_ms: int = 1000
    n_cr_ms: int = 1000
    stmin_ms: int = 0
    block_size: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (int(self.n_bs_ms), int(self.n_cr_ms), int(self.stmin_ms), int(self.block_size))


@dataclass(frozen=True)
class LinTpConfig:
    n_cr_ms: int = 1000
    max_pdu_len: int = 4095

    def as_tuple(self) -> tuple[int, int]:
        return (int(self.n_cr_ms), int(self.max_pdu_len))


@dataclass(frozen=True)
class CanMsg:
    id: int
    data: bytes
    isfd: bool = False


@dataclass(frozen=True)
class LinMsg:
    id: int
    data: bytes
