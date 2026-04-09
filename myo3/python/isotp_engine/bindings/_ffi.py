from __future__ import annotations

import importlib

from .types import IsoTpError

_native = importlib.import_module("isotp_engine._native")

FFI_OK = _native.ISOTP_FFI_OK
FFI_HAS_ITEM = _native.ISOTP_FFI_HAS_ITEM
ERR_NULL_PTR = _native.ISOTP_FFI_ERR_NULL_PTR
ERR_BUFFER_TOO_SMALL = _native.ISOTP_FFI_ERR_BUFFER_TOO_SMALL

ERR_MAP = {
    -100: "InvalidConfig",
    -101: "InvalidCanFrame",
    -102: "InvalidPayload",
    -103: "TxBusy",
    -104: "FunctionalMultiFrameNotSupported",
    -105: "TxTimeoutBs",
    -106: "RxTimeoutCr",
    -107: "SequenceMismatch",
    -108: "FlowControlOverflow",
    -109: "UnexpectedFlowStatus",
    -110: "ParseError",
    ERR_NULL_PTR: "NullPtr",
    ERR_BUFFER_TOO_SMALL: "BufferTooSmall",
}


def raise_if_error(code: int) -> None:
    if code not in (FFI_OK, FFI_HAS_ITEM):
        raise IsoTpError(code)
