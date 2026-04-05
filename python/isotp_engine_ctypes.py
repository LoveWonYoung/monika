import ctypes
import os
import platform
import queue
import threading
import time
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_int32,
    c_size_t,
    c_uint32,
    c_uint64,
    c_uint8,
    c_void_p,
)
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


FFI_OK = 0
FFI_HAS_ITEM = 1

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
}


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


class IsoTpError(RuntimeError):
    def __init__(self, code: int):
        name = ERR_MAP.get(code, f"UnknownError({code})")
        super().__init__(f"IsoTpEngine error: {name}")
        self.code = code


class UdsNegativeResponseError(RuntimeError):
    def __init__(self, service_id: int, nrc: int, payload: bytes):
        super().__init__(
            f"UDS negative response: SID=0x{service_id:02X}, NRC=0x{nrc:02X}, payload={payload.hex(' ')}"
        )
        self.service_id = service_id
        self.nrc = nrc
        self.payload = payload


@dataclass
class CanMsg:
    id: int
    data: bytes
    isfd: bool


def parse_uds_negative_response(payload: bytes) -> Optional[Tuple[int, int]]:
    if len(payload) >= 3 and payload[0] == 0x7F:
        return payload[1], payload[2]
    return None


def is_uds_response_pending(payload: bytes) -> bool:
    parsed = parse_uds_negative_response(payload)
    return parsed is not None and parsed[1] == 0x78


def build_uds_default_matcher(request_payload: bytes) -> Callable[[bytes], bool]:
    req = bytes(request_payload)
    if len(req) == 0:
        return lambda _resp: True

    req_sid = req[0]
    positive_sid = (req_sid + 0x40) & 0xFF

    def matcher(resp: bytes) -> bool:
        if len(resp) == 0:
            return False

        neg = parse_uds_negative_response(resp)
        if neg is not None:
            service_id, _nrc = neg
            return service_id == req_sid

        if resp[0] != positive_sid:
            return False

        # DID/RID echo services.
        if req_sid in (0x22, 0x2E, 0x2F, 0x31) and len(req) >= 3 and len(resp) >= 3:
            return resp[1:3] == req[1:3]

        # Sub-function echo services.
        if req_sid in (0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x85, 0x87) and len(req) >= 2 and len(resp) >= 2:
            return (resp[1] & 0x7F) == (req[1] & 0x7F)

        return True

    return matcher


def step_once(
    tp: "IsoTpEngine",
    rxfunc: Callable[[], Optional[CanMsg]],
    txfunc: Callable[[int, bytes, bool], None],
    ts_ms: Optional[int] = None,
    on_error: Optional[Callable[[int], None]] = None,
) -> None:
    now = monotonic_ms() if ts_ms is None else ts_ms

    rx_batch: List[Tuple[int, bytes, bool]] = []
    while True:
        msg = rxfunc()
        if msg is None:
            break
        rx_batch.append((msg.id, msg.data, msg.isfd))
    if rx_batch:
        tp.on_can_frames(rx_batch, ts_ms=now)

    tp.tick(ts_ms=now)

    for out in tp.pop_all_tx_can_frames():
        can_id, data, is_fd = out
        txfunc(can_id, data, is_fd)

    while True:
        err = tp.pop_error()
        if err is None:
            break
        if on_error is None:
            raise IsoTpError(err)
        on_error(err)


def send_uds_and_wait_final(
    tp: "IsoTpEngine",
    payload: bytes,
    rxfunc: Callable[[], Optional[CanMsg]],
    txfunc: Callable[[int, bytes, bool], None],
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
        step_once=lambda: step_once(tp, rxfunc=rxfunc, txfunc=txfunc, on_error=on_error),
        response_matcher=response_matcher,
        flush_before_send=flush_before_send,
    )
    if resp is None:
        raise TimeoutError("response_timeout_ms was not set")
    return resp


class _IsoTpConfigC(Structure):
    _fields_ = [
        ("n_bs_ms", c_uint32),
        ("n_cr_ms", c_uint32),
        ("stmin_ms", c_uint8),
        ("block_size", c_uint8),
    ]


class _IsoTpCanFrameInC(Structure):
    _fields_ = [
        ("id", c_uint32),
        ("is_fd", c_uint8),
        ("data_ptr", POINTER(c_uint8)),
        ("data_len", c_size_t),
    ]


@dataclass
class TpConfig:
    n_bs_ms: int = 1000
    n_cr_ms: int = 1000
    stmin_ms: int = 20
    block_size: int = 0

    def to_c(self) -> _IsoTpConfigC:
        return _IsoTpConfigC(
            n_bs_ms=self.n_bs_ms,
            n_cr_ms=self.n_cr_ms,
            stmin_ms=self.stmin_ms,
            block_size=self.block_size,
        )


def _default_lib_path() -> str:
    sys_name = platform.system().lower()
    if "darwin" in sys_name:
        filename = "libisotp_engine.dylib"
    elif "windows" in sys_name:
        filename = "isotp_engine.dll"
    else:
        filename = "libisotp_engine.so"
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "target", "release", filename)


class IsoTpEngine:
    def __init__(
        self,
        req_id: int,
        resp_id: int,
        func_id: int,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        lib_path: Optional[str] = None,
    ):
        self._lib = ctypes.CDLL(lib_path or _default_lib_path())
        self._bind()

        if cfg is None:
            c_cfg = self._lib.isotp_default_config()
        else:
            c_cfg = cfg.to_c()

        engine_ptr = c_void_p()
        rc = self._lib.isotp_engine_new(
            c_uint32(req_id),
            c_uint32(resp_id),
            c_uint32(func_id),
            c_uint8(1 if is_fd else 0),
            c_cfg,
            byref(engine_ptr),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)

        self._engine = engine_ptr
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._lib.isotp_engine_free(self._engine)
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

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool, ts_ms: Optional[int] = None) -> None:
        ts = monotonic_ms() if ts_ms is None else ts_ms
        data_buf = (c_uint8 * len(data)).from_buffer_copy(data)
        rc = self._lib.isotp_on_can_frame(
            self._engine,
            c_uint32(can_id),
            data_buf,
            c_size_t(len(data)),
            c_uint8(1 if is_fd else 0),
            c_uint64(ts),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)

    def on_can_frames(
        self,
        frames: List[Tuple[int, bytes, bool]],
        ts_ms: Optional[int] = None,
    ) -> int:
        if not frames:
            return 0
        ts = monotonic_ms() if ts_ms is None else ts_ms

        if not self._has_batch_on_can_frames:
            for can_id, data, is_fd in frames:
                self.on_can_frame(can_id=can_id, data=data, is_fd=is_fd, ts_ms=ts)
            return len(frames)

        frame_array = (_IsoTpCanFrameInC * len(frames))()
        data_bufs = []
        for idx, (can_id, data, is_fd) in enumerate(frames):
            payload = bytes(data)
            data_ptr = None
            if payload:
                data_buf = (c_uint8 * len(payload)).from_buffer_copy(payload)
                data_bufs.append(data_buf)
                data_ptr = ctypes.cast(data_buf, POINTER(c_uint8))

            frame_array[idx].id = can_id
            frame_array[idx].is_fd = 1 if is_fd else 0
            frame_array[idx].data_ptr = data_ptr
            frame_array[idx].data_len = len(payload)

        out_processed = c_size_t()
        rc = self._lib.isotp_on_can_frames(
            self._engine,
            frame_array,
            c_size_t(len(frames)),
            c_uint64(ts),
            byref(out_processed),
        )
        if rc != FFI_OK:
            raise IsoTpError(rc)
        return int(out_processed.value)

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
        rc = self._lib.isotp_tx_uds_msg(
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
        rc = self._lib.isotp_tick(self._engine, c_uint64(ts))
        if rc != FFI_OK:
            raise IsoTpError(rc)

    def pop_tx_can_frame(self, buf_cap: int = 64) -> Optional[Tuple[int, bytes, bool]]:
        out_id = c_uint32()
        out_is_fd = c_uint8()
        out_len = c_size_t()
        out_buf = (c_uint8 * buf_cap)()

        rc = self._lib.isotp_pop_tx_can_frame(
            self._engine,
            byref(out_id),
            byref(out_is_fd),
            out_buf,
            c_size_t(buf_cap),
            byref(out_len),
        )
        if rc == FFI_OK:
            return None
        if rc != FFI_HAS_ITEM:
            raise IsoTpError(rc)
        data = bytes(out_buf[: out_len.value])
        return out_id.value, data, bool(out_is_fd.value)

    def pop_tx_can_frames(
        self,
        max_frames: int = 64,
        buf_cap: int = 64,
    ) -> List[Tuple[int, bytes, bool]]:
        if max_frames <= 0:
            return []
        if buf_cap <= 0:
            raise ValueError("buf_cap must be > 0")

        if not self._has_batch_pop_tx_can_frames:
            frames: List[Tuple[int, bytes, bool]] = []
            for _ in range(max_frames):
                item = self.pop_tx_can_frame(buf_cap=buf_cap)
                if item is None:
                    break
                frames.append(item)
            return frames

        out_ids = (c_uint32 * max_frames)()
        out_is_fd = (c_uint8 * max_frames)()
        out_lens = (c_size_t * max_frames)()
        out_buf = (c_uint8 * (max_frames * buf_cap))()
        out_count = c_size_t()
        rc = self._lib.isotp_pop_tx_can_frames(
            self._engine,
            out_ids,
            out_is_fd,
            out_buf,
            c_size_t(buf_cap),
            out_lens,
            c_size_t(max_frames),
            byref(out_count),
        )
        if rc == FFI_OK:
            return []
        if rc != FFI_HAS_ITEM:
            raise IsoTpError(rc)

        count = int(out_count.value)
        frames: List[Tuple[int, bytes, bool]] = []
        for idx in range(count):
            data_len = int(out_lens[idx])
            start = idx * buf_cap
            end = start + data_len
            frames.append((int(out_ids[idx]), bytes(out_buf[start:end]), bool(out_is_fd[idx])))
        return frames

    def pop_all_tx_can_frames(self, buf_cap: int = 64, batch_size: int = 64) -> List[Tuple[int, bytes, bool]]:
        frames: List[Tuple[int, bytes, bool]] = []
        while True:
            chunk = self.pop_tx_can_frames(max_frames=batch_size, buf_cap=buf_cap)
            if not chunk:
                break
            frames.extend(chunk)
        return frames

    def rx_uds_msg(self, buf_cap: int = 8192) -> Optional[bytes]:
        out_len = c_size_t()
        out_buf = (c_uint8 * buf_cap)()
        rc = self._lib.isotp_rx_uds_msg(
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
        rc = self._lib.isotp_pop_error(self._engine, byref(out_code))
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
        self._has_batch_on_can_frames = False
        self._has_batch_pop_tx_can_frames = False

        self._lib.isotp_default_config.argtypes = []
        self._lib.isotp_default_config.restype = _IsoTpConfigC

        self._lib.isotp_engine_new.argtypes = [
            c_uint32,
            c_uint32,
            c_uint32,
            c_uint8,
            _IsoTpConfigC,
            POINTER(c_void_p),
        ]
        self._lib.isotp_engine_new.restype = c_int32

        self._lib.isotp_engine_free.argtypes = [c_void_p]
        self._lib.isotp_engine_free.restype = None

        self._lib.isotp_on_can_frame.argtypes = [
            c_void_p,
            c_uint32,
            POINTER(c_uint8),
            c_size_t,
            c_uint8,
            c_uint64,
        ]
        self._lib.isotp_on_can_frame.restype = c_int32
        if hasattr(self._lib, "isotp_on_can_frames"):
            self._lib.isotp_on_can_frames.argtypes = [
                c_void_p,
                POINTER(_IsoTpCanFrameInC),
                c_size_t,
                c_uint64,
                POINTER(c_size_t),
            ]
            self._lib.isotp_on_can_frames.restype = c_int32
            self._has_batch_on_can_frames = True

        self._lib.isotp_tx_uds_msg.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            c_size_t,
            c_uint8,
            c_uint64,
        ]
        self._lib.isotp_tx_uds_msg.restype = c_int32

        self._lib.isotp_tick.argtypes = [c_void_p, c_uint64]
        self._lib.isotp_tick.restype = c_int32

        self._lib.isotp_pop_tx_can_frame.argtypes = [
            c_void_p,
            POINTER(c_uint32),
            POINTER(c_uint8),
            POINTER(c_uint8),
            c_size_t,
            POINTER(c_size_t),
        ]
        self._lib.isotp_pop_tx_can_frame.restype = c_int32
        if hasattr(self._lib, "isotp_pop_tx_can_frames"):
            self._lib.isotp_pop_tx_can_frames.argtypes = [
                c_void_p,
                POINTER(c_uint32),
                POINTER(c_uint8),
                POINTER(c_uint8),
                c_size_t,
                POINTER(c_size_t),
                c_size_t,
                POINTER(c_size_t),
            ]
            self._lib.isotp_pop_tx_can_frames.restype = c_int32
            self._has_batch_pop_tx_can_frames = True

        self._lib.isotp_rx_uds_msg.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            c_size_t,
            POINTER(c_size_t),
        ]
        self._lib.isotp_rx_uds_msg.restype = c_int32

        self._lib.isotp_pop_error.argtypes = [c_void_p, POINTER(c_int32)]
        self._lib.isotp_pop_error.restype = c_int32


@dataclass
class TxRequest:
    payload: bytes
    functional: bool


@dataclass
class RxCanFrame:
    can_id: int
    data: bytes
    is_fd: bool


class IsoTpEngineWorker:
    """
    Single-owner worker thread for IsoTpEngine.
    All Rust calls happen in this thread to avoid concurrent mutable access.
    """

    def __init__(
        self,
        req_id: int,
        resp_id: int,
        func_id: int,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        lib_path: Optional[str] = None,
        tick_period_ms: int = 1,
    ):
        self._req_id = req_id
        self._resp_id = resp_id
        self._func_id = func_id
        self._is_fd = is_fd
        self._cfg = cfg
        self._lib_path = lib_path
        self._tick_period_ms = max(1, tick_period_ms)

        self._tx_req_q: "queue.Queue[TxRequest]" = queue.Queue()
        self._rx_can_q: "queue.Queue[RxCanFrame]" = queue.Queue()
        self._tx_can_q: "queue.Queue[Tuple[int, bytes, bool]]" = queue.Queue()
        self._rx_uds_q: "queue.Queue[bytes]" = queue.Queue()
        self._err_q: "queue.Queue[int]" = queue.Queue()

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="IsoTpEngineWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def tx_uds_msg(
        self,
        payload: bytes,
        functional: bool = False,
        response_timeout_ms: Optional[int] = None,
        pending_gap_ms: int = 3000,
        poll_interval_ms: int = 1,
        response_matcher: Optional[Callable[[bytes], bool]] = None,
        flush_before_send: bool = True,
    ) -> Optional[bytes]:
        if response_timeout_ms is not None and flush_before_send:
            self.clear_pending_uds_messages()

        self._tx_req_q.put(TxRequest(payload=bytes(payload), functional=functional))
        if response_timeout_ms is None:
            return None
        matcher = response_matcher or build_uds_default_matcher(payload)
        return self.wait_uds_final_response(
            overall_timeout_ms=response_timeout_ms,
            pending_gap_ms=pending_gap_ms,
            poll_interval_ms=poll_interval_ms,
            response_matcher=matcher,
        )

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool) -> None:
        self._rx_can_q.put(RxCanFrame(can_id=can_id, data=bytes(data), is_fd=is_fd))

    def pop_tx_can_frame(self, timeout_s: float = 0.0) -> Optional[Tuple[int, bytes, bool]]:
        try:
            return self._tx_can_q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def pop_rx_uds_msg(self, timeout_s: float = 0.0) -> Optional[bytes]:
        try:
            return self._rx_uds_q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        try:
            return self._err_q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def clear_pending_uds_messages(self) -> int:
        count = 0
        while True:
            try:
                self._rx_uds_q.get_nowait()
            except queue.Empty:
                break
            count += 1
        return count

    def wait_uds_final_response(
        self,
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
            err = self.pop_error(timeout_s=0.0)
            if err is not None:
                raise IsoTpError(err)

            timeout_s = poll_interval_ms / 1000.0 if poll_interval_ms > 0 else 0.0
            msg = self.pop_rx_uds_msg(timeout_s=timeout_s)
            if msg is None:
                if seen_pending and monotonic_ms() > pending_deadline:
                    raise TimeoutError("UDS pending gap timeout after NRC 0x78")
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

    def _run(self) -> None:
        try:
            with IsoTpEngine(
                req_id=self._req_id,
                resp_id=self._resp_id,
                func_id=self._func_id,
                is_fd=self._is_fd,
                cfg=self._cfg,
                lib_path=self._lib_path,
            ) as tp:
                while not self._stop_evt.is_set():
                    loop_started = monotonic_ms()

                    while True:
                        try:
                            req = self._tx_req_q.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            tp.tx_uds_msg(req.payload, functional=req.functional, ts_ms=loop_started)
                        except IsoTpError as e:
                            self._err_q.put(e.code)

                    rx_batch: List[Tuple[int, bytes, bool]] = []
                    while True:
                        try:
                            can = self._rx_can_q.get_nowait()
                        except queue.Empty:
                            break
                        rx_batch.append((can.can_id, can.data, can.is_fd))
                    if rx_batch:
                        try:
                            tp.on_can_frames(rx_batch, ts_ms=loop_started)
                        except IsoTpError as e:
                            self._err_q.put(e.code)

                    try:
                        tp.tick(loop_started)
                    except IsoTpError as e:
                        self._err_q.put(e.code)

                    for item in tp.pop_all_tx_can_frames():
                        self._tx_can_q.put(item)

                    while True:
                        item = tp.rx_uds_msg()
                        if item is None:
                            break
                        self._rx_uds_q.put(item)

                    while True:
                        item = tp.pop_error()
                        if item is None:
                            break
                        self._err_q.put(item)

                    elapsed = monotonic_ms() - loop_started
                    sleep_ms = self._tick_period_ms - elapsed
                    if sleep_ms > 0:
                        time.sleep(sleep_ms / 1000.0)
        except Exception:
            self._err_q.put(-9999)
