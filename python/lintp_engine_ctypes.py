import ctypes
import time
from ctypes import POINTER, Structure, byref, c_int32, c_size_t, c_uint32, c_uint64, c_uint8, c_void_p
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from lib.isotp_engine_ctypes import (
    FFI_HAS_ITEM,
    FFI_OK,
    IsoTpError,
    UdsNegativeResponseError,
    _default_lib_path,
    build_uds_default_matcher,
    monotonic_ms,
    parse_uds_negative_response,
)


@dataclass
class LinMsg:
    id: int
    data: bytes


class _LinTpConfigC(Structure):
    _fields_ = [
        ("n_cr_ms", c_uint32),
        ("max_pdu_len", c_size_t),
    ]


@dataclass
class LinTpConfig:
    n_cr_ms: int = 1000
    max_pdu_len: int = 4095

    def to_c(self) -> _LinTpConfigC:
        return _LinTpConfigC(
            n_cr_ms=self.n_cr_ms,
            max_pdu_len=self.max_pdu_len,
        )


class LinTpEngine:
    def __init__(
        self,
        req_frame_id: int = 0x3C,
        resp_frame_id: int = 0x3D,
        req_nad: int = 0x01,
        func_nad: int = 0x7F,
        cfg: Optional[LinTpConfig] = None,
        lib_path: Optional[str] = None,
    ):
        self._lib = ctypes.CDLL(lib_path or _default_lib_path())
        self._bind()

        if cfg is None:
            c_cfg = self._lib.lintp_default_config()
        else:
            c_cfg = cfg.to_c()

        engine_ptr = c_void_p()
        rc = self._lib.lintp_engine_new(
            c_uint8(req_frame_id),
            c_uint8(resp_frame_id),
            c_uint8(req_nad),
            c_uint8(func_nad),
            c_cfg,
            byref(engine_ptr),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)

        self._engine = engine_ptr
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._lib.lintp_engine_free(self._engine)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def on_lin_frame(self, frame_id: int, data: bytes, ts_ms: Optional[int] = None) -> None:
        ts = monotonic_ms() if ts_ms is None else ts_ms
        data_buf = (c_uint8 * len(data)).from_buffer_copy(data)
        rc = self._lib.lintp_on_lin_frame(
            self._engine,
            c_uint8(frame_id),
            data_buf,
            c_size_t(len(data)),
            c_uint64(ts),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)

    def tx_uds_msg(
        self,
        payload: bytes,
        functional: bool = False,
        ts_ms: Optional[int] = None,
        response_timeout_ms: Optional[int] = None,
        pending_gap_ms: int = 3000,
        poll_interval_ms: int = 1,
        step_once: Optional[Callable[[], None]] = None,
        response_matcher: Optional[Callable[[bytes], bool]] = None,
        flush_before_send: bool = True,
    ) -> Optional[bytes]:
        if response_timeout_ms is not None and flush_before_send:
            self.clear_pending_uds_messages()

        ts = monotonic_ms() if ts_ms is None else ts_ms
        payload_buf = (c_uint8 * len(payload)).from_buffer_copy(payload)
        rc = self._lib.lintp_tx_uds_msg(
            self._engine,
            payload_buf,
            c_size_t(len(payload)),
            c_uint8(1 if functional else 0),
            c_uint64(ts),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)
        if response_timeout_ms is None:
            return None
        if step_once is None:
            raise ValueError("step_once is required when response_timeout_ms is set")
        matcher = response_matcher or build_uds_default_matcher(payload)
        return self.wait_uds_final_response(
            step_once=step_once,
            overall_timeout_ms=response_timeout_ms,
            pending_gap_ms=pending_gap_ms,
            poll_interval_ms=poll_interval_ms,
            response_matcher=matcher,
        )

    def tick(self, ts_ms: Optional[int] = None) -> None:
        ts = monotonic_ms() if ts_ms is None else ts_ms
        rc = self._lib.lintp_tick(self._engine, c_uint64(ts))
        if rc != FFI_OK:
            raise IsoTpError(rc)

    def pop_tx_lin_frame(self, buf_cap: int = 8) -> Optional[Tuple[int, bytes]]:
        out_id = c_uint8()
        out_len = c_size_t()
        out_buf = (c_uint8 * buf_cap)()
        rc = self._lib.lintp_pop_tx_lin_frame(
            self._engine,
            byref(out_id),
            out_buf,
            c_size_t(buf_cap),
            byref(out_len),
        )
        if rc == FFI_OK:
            return None
        if rc != FFI_HAS_ITEM:
            raise IsoTpError(rc)
        return int(out_id.value), bytes(out_buf[: out_len.value])

    def pop_all_tx_lin_frames(self, buf_cap: int = 8) -> List[Tuple[int, bytes]]:
        frames: List[Tuple[int, bytes]] = []
        while True:
            item = self.pop_tx_lin_frame(buf_cap=buf_cap)
            if item is None:
                break
            frames.append(item)
        return frames

    def rx_uds_msg(self, buf_cap: int = 4095) -> Optional[bytes]:
        out_len = c_size_t()
        out_buf = (c_uint8 * buf_cap)()
        rc = self._lib.lintp_rx_uds_msg(
            self._engine,
            out_buf,
            c_size_t(buf_cap),
            byref(out_len),
        )
        if rc == FFI_OK:
            return None
        if rc != FFI_HAS_ITEM:
            raise IsoTpError(rc)
        return bytes(out_buf[: out_len.value])

    def pop_error(self) -> Optional[int]:
        out_code = c_int32()
        rc = self._lib.lintp_pop_error(self._engine, byref(out_code))
        if rc == FFI_OK:
            return None
        if rc != FFI_HAS_ITEM:
            raise IsoTpError(rc)
        return out_code.value

    def clear_pending_uds_messages(self) -> int:
        count = 0
        while True:
            msg = self.rx_uds_msg()
            if msg is None:
                break
            count += 1
        return count

    def wait_uds_final_response(
        self,
        step_once: Callable[[], None],
        overall_timeout_ms: int = 10000,
        pending_gap_ms: int = 3000,
        poll_interval_ms: int = 1,
        response_matcher: Optional[Callable[[bytes], bool]] = None,
    ) -> bytes:
        if overall_timeout_ms <= 0:
            raise ValueError("overall_timeout_ms must be > 0")
        if pending_gap_ms <= 0:
            raise ValueError("pending_gap_ms must be > 0")
        if poll_interval_ms < 0:
            raise ValueError("poll_interval_ms must be >= 0")

        end_at = monotonic_ms() + overall_timeout_ms
        pending_deadline = end_at
        seen_pending = False

        while monotonic_ms() <= end_at:
            step_once()
            msg = self.rx_uds_msg()
            if msg is None:
                if seen_pending and monotonic_ms() > pending_deadline:
                    raise TimeoutError("UDS pending gap timeout after NRC 0x78")
                if poll_interval_ms > 0:
                    time.sleep(poll_interval_ms / 1000.0)
                continue

            if response_matcher is not None and not response_matcher(msg):
                continue

            parsed = parse_uds_negative_response(msg)
            if parsed is None:
                return msg

            service_id, nrc = parsed
            if nrc == 0x78:
                seen_pending = True
                pending_deadline = monotonic_ms() + pending_gap_ms
                continue

            raise UdsNegativeResponseError(service_id=service_id, nrc=nrc, payload=msg)

        raise TimeoutError("UDS final response timeout")

    def _bind(self) -> None:
        self._lib.lintp_default_config.argtypes = []
        self._lib.lintp_default_config.restype = _LinTpConfigC

        self._lib.lintp_engine_new.argtypes = [
            c_uint8,
            c_uint8,
            c_uint8,
            c_uint8,
            _LinTpConfigC,
            POINTER(c_void_p),
        ]
        self._lib.lintp_engine_new.restype = c_int32

        self._lib.lintp_engine_free.argtypes = [c_void_p]
        self._lib.lintp_engine_free.restype = None

        self._lib.lintp_on_lin_frame.argtypes = [
            c_void_p,
            c_uint8,
            POINTER(c_uint8),
            c_size_t,
            c_uint64,
        ]
        self._lib.lintp_on_lin_frame.restype = c_int32

        self._lib.lintp_tx_uds_msg.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            c_size_t,
            c_uint8,
            c_uint64,
        ]
        self._lib.lintp_tx_uds_msg.restype = c_int32

        self._lib.lintp_tick.argtypes = [c_void_p, c_uint64]
        self._lib.lintp_tick.restype = c_int32

        self._lib.lintp_pop_tx_lin_frame.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            POINTER(c_uint8),
            c_size_t,
            POINTER(c_size_t),
        ]
        self._lib.lintp_pop_tx_lin_frame.restype = c_int32

        self._lib.lintp_rx_uds_msg.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            c_size_t,
            POINTER(c_size_t),
        ]
        self._lib.lintp_rx_uds_msg.restype = c_int32

        self._lib.lintp_pop_error.argtypes = [c_void_p, POINTER(c_int32)]
        self._lib.lintp_pop_error.restype = c_int32


def step_once_lin(
    tp: "LinTpEngine",
    rxfunc: Callable[[], Optional[LinMsg]],
    txfunc: Callable[[int, bytes], None],
    ts_ms: Optional[int] = None,
    on_error: Optional[Callable[[int], None]] = None,
) -> None:
    now = monotonic_ms() if ts_ms is None else ts_ms

    while True:
        msg = rxfunc()
        if msg is None:
            break
        tp.on_lin_frame(frame_id=msg.id, data=msg.data, ts_ms=now)

    tp.tick(ts_ms=now)

    for out in tp.pop_all_tx_lin_frames():
        frame_id, data = out
        txfunc(frame_id, data)

    while True:
        err = tp.pop_error()
        if err is None:
            break
        if on_error is None:
            raise IsoTpError(err)
        on_error(err)


def send_uds_and_wait_final_lin(
    tp: "LinTpEngine",
    payload: bytes,
    rxfunc: Callable[[], Optional[LinMsg]],
    txfunc: Callable[[int, bytes], None],
    functional: bool = False,
    overall_timeout_ms: int = 10000,
    pending_gap_ms: int = 3000,
    poll_interval_ms: int = 1,
    on_error: Optional[Callable[[int], None]] = None,
    response_matcher: Optional[Callable[[bytes], bool]] = None,
    flush_before_send: bool = True,
) -> bytes:
    resp = tp.tx_uds_msg(
        payload=payload,
        functional=functional,
        ts_ms=monotonic_ms(),
        response_timeout_ms=overall_timeout_ms,
        pending_gap_ms=pending_gap_ms,
        poll_interval_ms=poll_interval_ms,
        step_once=lambda: step_once_lin(tp, rxfunc=rxfunc, txfunc=txfunc, on_error=on_error),
        response_matcher=response_matcher,
        flush_before_send=flush_before_send,
    )
    if resp is None:
        raise TimeoutError("response_timeout_ms was not set")
    return resp
