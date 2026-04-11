from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from . import _ffi
from .helpers import monotonic_ms
from .types import LinTpConfig, TpConfig


class IsoTpEngine:
    def __init__(self, req_id: int, resp_id: int, func_id: int, is_fd: bool = False, cfg: Optional[TpConfig] = None):
        code, handle = _ffi._native.isotp_engine_new(
            int(req_id),
            int(resp_id),
            int(func_id),
            bool(is_fd),
            None if cfg is None else cfg.as_tuple(),
        )
        _ffi.raise_if_error(code)
        self._handle = int(handle)
        self.req_id = int(req_id)
        self.resp_id = int(resp_id)
        self.func_id = int(func_id)
        self.is_fd = bool(is_fd)
        self.cfg = cfg or TpConfig(*_ffi._native.isotp_default_config())
        self._closed = False
        self._pending_uds: Deque[bytes] = deque()

    def close(self) -> None:
        if not self._closed:
            _ffi._native.isotp_engine_free(self._handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool = False, ts_ms: Optional[int] = None) -> None:
        code = _ffi._native.isotp_on_can_frame(
            self._handle,
            int(can_id),
            bytes(data),
            bool(is_fd),
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _ffi.raise_if_error(code)

    def on_can_frames(self, frames: list[tuple[int, bytes, bool]], ts_ms: Optional[int] = None) -> int:
        code, processed = _ffi._native.isotp_on_can_frames(
            self._handle,
            [(int(can_id), bytes(data), bool(is_fd)) for can_id, data, is_fd in frames],
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _ffi.raise_if_error(code)
        return int(processed)

    def tx_uds_msg(self, payload: bytes, functional: bool = False, ts_ms: Optional[int] = None, response_timeout_ms: Optional[int] = None) -> None:
        del response_timeout_ms
        code = _ffi._native.isotp_tx_uds_msg(
            self._handle,
            bytes(payload),
            bool(functional),
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _ffi.raise_if_error(code)

    def tick(self, ts_ms: Optional[int] = None) -> None:
        code = _ffi._native.isotp_tick(self._handle, monotonic_ms() if ts_ms is None else int(ts_ms))
        _ffi.raise_if_error(code)

    def pop_tx_can_frame(self) -> Optional[tuple[int, bytes, bool]]:
        code, can_id, is_fd, data = _ffi._native.isotp_pop_tx_can_frame(self._handle)
        _ffi.raise_if_error(code)
        if code == _ffi.FFI_HAS_ITEM:
            return int(can_id), bytes(data), bool(is_fd)
        return None

    def pop_tx_can_frames(self, max_frames: int = 64, buf_cap: Optional[int] = None) -> list[tuple[int, bytes, bool]]:
        del buf_cap
        code, frames = _ffi._native.isotp_pop_tx_can_frames(self._handle, int(max_frames))
        _ffi.raise_if_error(code)
        return [(int(can_id), bytes(data), bool(is_fd)) for can_id, data, is_fd in frames]

    def pop_all_tx_can_frames(self) -> list[tuple[int, bytes, bool]]:
        out: list[tuple[int, bytes, bool]] = []
        while True:
            frame = self.pop_tx_can_frame()
            if frame is None:
                return out
            out.append(frame)

    def rx_uds_msg(self) -> Optional[bytes]:
        if self._pending_uds:
            return self._pending_uds.popleft()
        code, payload = _ffi._native.isotp_rx_uds_msg(self._handle)
        _ffi.raise_if_error(code)
        if code == _ffi.FFI_HAS_ITEM:
            return bytes(payload)
        return None

    def pop_error(self) -> Optional[int]:
        code, err = _ffi._native.isotp_pop_error(self._handle)
        _ffi.raise_if_error(code)
        return int(err) if code == _ffi.FFI_HAS_ITEM else None

    def clear_pending_uds_messages(self) -> None:
        self._pending_uds.clear()
        while self.rx_uds_msg() is not None:
            pass


class LinTpEngine:
    def __init__(self, req_frame_id: int, resp_frame_id: int, req_nad: int, func_nad: int, cfg: Optional[LinTpConfig] = None):
        code, handle = _ffi._native.lintp_engine_new(
            int(req_frame_id),
            int(resp_frame_id),
            int(req_nad),
            int(func_nad),
            None if cfg is None else cfg.as_tuple(),
        )
        _ffi.raise_if_error(code)
        self._handle = int(handle)
        self.req_frame_id = int(req_frame_id)
        self.resp_frame_id = int(resp_frame_id)
        self.req_nad = int(req_nad)
        self.func_nad = int(func_nad)
        self.cfg = cfg or LinTpConfig(*_ffi._native.lintp_default_config())
        self._closed = False
        self._pending_uds: Deque[bytes] = deque()

    def close(self) -> None:
        if not self._closed:
            _ffi._native.lintp_engine_free(self._handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def set_nad(self, req_nad: int, func_nad: Optional[int] = None) -> None:
        func = self.func_nad if func_nad is None else int(func_nad)
        code = _ffi._native.lintp_set_nad(self._handle, int(req_nad), func)
        _ffi.raise_if_error(code)
        self.req_nad = int(req_nad)
        self.func_nad = func

    def on_lin_frame(self, frame_id: int, data: bytes, ts_ms: Optional[int] = None) -> None:
        code = _ffi._native.lintp_on_lin_frame(
            self._handle,
            int(frame_id),
            bytes(data),
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _ffi.raise_if_error(code)

    def tx_uds_msg(self, payload: bytes, functional: bool = False, ts_ms: Optional[int] = None) -> None:
        code = _ffi._native.lintp_tx_uds_msg(
            self._handle,
            bytes(payload),
            bool(functional),
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _ffi.raise_if_error(code)

    def tick(self, ts_ms: Optional[int] = None) -> None:
        code = _ffi._native.lintp_tick(self._handle, monotonic_ms() if ts_ms is None else int(ts_ms))
        _ffi.raise_if_error(code)

    def pop_tx_lin_frame(self) -> Optional[tuple[int, bytes]]:
        code, frame_id, data = _ffi._native.lintp_pop_tx_lin_frame(self._handle)
        _ffi.raise_if_error(code)
        if code == _ffi.FFI_HAS_ITEM:
            return int(frame_id), bytes(data)
        return None

    def pop_all_tx_lin_frames(self) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        while True:
            frame = self.pop_tx_lin_frame()
            if frame is None:
                return out
            out.append(frame)

    def rx_uds_msg(self) -> Optional[bytes]:
        if self._pending_uds:
            return self._pending_uds.popleft()
        code, payload = _ffi._native.lintp_rx_uds_msg(self._handle)
        _ffi.raise_if_error(code)
        if code == _ffi.FFI_HAS_ITEM:
            return bytes(payload)
        return None

    def pop_error(self) -> Optional[int]:
        code, err = _ffi._native.lintp_pop_error(self._handle)
        _ffi.raise_if_error(code)
        return int(err) if code == _ffi.FFI_HAS_ITEM else None
