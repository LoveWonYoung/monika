from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from .. import _native

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


class IsoTpError(RuntimeError):
    def __init__(self, code: int, message: Optional[str] = None):
        self.code = int(code)
        self.name = ERR_MAP.get(self.code, "UnknownError")
        super().__init__(message or f"{self.name} ({self.code})")


class UdsNegativeResponseError(RuntimeError):
    def __init__(self, response: bytes):
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


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def is_uds_response_pending(payload: bytes) -> bool:
    return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78


def parse_uds_negative_response(payload: bytes) -> tuple[int, int, int]:
    if len(payload) < 3 or payload[0] != 0x7F:
        raise ValueError("not a UDS negative response")
    return payload[0], payload[1], payload[2]


def build_uds_default_matcher(request: bytes) -> Callable[[bytes], bool]:
    req = bytes(request)
    sid = req[0] if req else None

    def matcher(response: bytes) -> bool:
        rsp = bytes(response)
        if not rsp:
            return False
        if is_uds_response_pending(rsp):
            return sid is not None and rsp[1] == sid
        if sid is None:
            return True
        if rsp[0] == 0x7F:
            return len(rsp) >= 2 and rsp[1] == sid
        if rsp[0] != ((sid + 0x40) & 0xFF):
            return False

        # DID-echo style services.
        if sid in (0x22, 0x2E, 0x2F) and len(req) >= 3 and len(rsp) >= 3:
            return rsp[1:3] == req[1:3]

        # RoutineControl echoes sub-function + routine id.
        if sid == 0x31 and len(req) >= 4 and len(rsp) >= 4:
            return rsp[1:4] == req[1:4]

        # Sub-function echo services (e.g. 0x19 ReadDTCInformation).
        if sid in (0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x85, 0x87) and len(req) >= 2 and len(rsp) >= 2:
            return (rsp[1] & 0x7F) == (req[1] & 0x7F)

        return True

    return matcher


def _raise_if_error(code: int) -> None:
    if code not in (FFI_OK, FFI_HAS_ITEM):
        raise IsoTpError(code)


class IsoTpEngine:
    def __init__(self, req_id: int, resp_id: int, func_id: int, is_fd: bool = False, cfg: Optional[TpConfig] = None):
        code, handle = _native.isotp_engine_new(int(req_id), int(resp_id), int(func_id), bool(is_fd), None if cfg is None else cfg.as_tuple())
        _raise_if_error(code)
        self._handle = int(handle)
        self.req_id = int(req_id)
        self.resp_id = int(resp_id)
        self.func_id = int(func_id)
        self.is_fd = bool(is_fd)
        self.cfg = cfg or TpConfig(*_native.isotp_default_config())
        self._closed = False
        self._pending_uds: Deque[bytes] = deque()

    def close(self) -> None:
        if not self._closed:
            _native.isotp_engine_free(self._handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool = False, ts_ms: Optional[int] = None) -> None:
        code = _native.isotp_on_can_frame(self._handle, int(can_id), bytes(data), bool(is_fd), monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def on_can_frames(self, frames: list[tuple[int, bytes, bool]], ts_ms: Optional[int] = None) -> int:
        code, processed = _native.isotp_on_can_frames(
            self._handle,
            [(int(can_id), bytes(data), bool(is_fd)) for can_id, data, is_fd in frames],
            monotonic_ms() if ts_ms is None else int(ts_ms),
        )
        _raise_if_error(code)
        return int(processed)

    def tx_uds_msg(self, payload: bytes, functional: bool = False, ts_ms: Optional[int] = None, response_timeout_ms: Optional[int] = None) -> None:
        del response_timeout_ms
        code = _native.isotp_tx_uds_msg(self._handle, bytes(payload), bool(functional), monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def tick(self, ts_ms: Optional[int] = None) -> None:
        code = _native.isotp_tick(self._handle, monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def pop_tx_can_frame(self) -> Optional[tuple[int, bytes, bool]]:
        code, can_id, is_fd, data = _native.isotp_pop_tx_can_frame(self._handle)
        _raise_if_error(code)
        if code == FFI_HAS_ITEM:
            return int(can_id), bytes(data), bool(is_fd)
        return None

    def pop_tx_can_frames(self, max_frames: int = 64, buf_cap: Optional[int] = None) -> list[tuple[int, bytes, bool]]:
        del buf_cap
        code, frames = _native.isotp_pop_tx_can_frames(self._handle, int(max_frames))
        _raise_if_error(code)
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
        code, payload = _native.isotp_rx_uds_msg(self._handle)
        _raise_if_error(code)
        if code == FFI_HAS_ITEM:
            return bytes(payload)
        return None

    def pop_error(self) -> Optional[int]:
        code, err = _native.isotp_pop_error(self._handle)
        _raise_if_error(code)
        return int(err) if code == FFI_HAS_ITEM else None

    def clear_pending_uds_messages(self) -> None:
        self._pending_uds.clear()
        while self.rx_uds_msg() is not None:
            pass


class LinTpEngine:
    def __init__(self, req_frame_id: int, resp_frame_id: int, req_nad: int, func_nad: int, cfg: Optional[LinTpConfig] = None):
        code, handle = _native.lintp_engine_new(int(req_frame_id), int(resp_frame_id), int(req_nad), int(func_nad), None if cfg is None else cfg.as_tuple())
        _raise_if_error(code)
        self._handle = int(handle)
        self.req_frame_id = int(req_frame_id)
        self.resp_frame_id = int(resp_frame_id)
        self.req_nad = int(req_nad)
        self.func_nad = int(func_nad)
        self.cfg = cfg or LinTpConfig(*_native.lintp_default_config())
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            _native.lintp_engine_free(self._handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def set_nad(self, req_nad: int, func_nad: Optional[int] = None) -> None:
        func = self.func_nad if func_nad is None else int(func_nad)
        code = _native.lintp_set_nad(self._handle, int(req_nad), func)
        _raise_if_error(code)
        self.req_nad = int(req_nad)
        self.func_nad = func

    def on_lin_frame(self, frame_id: int, data: bytes, ts_ms: Optional[int] = None) -> None:
        code = _native.lintp_on_lin_frame(self._handle, int(frame_id), bytes(data), monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def tx_uds_msg(self, payload: bytes, functional: bool = False, ts_ms: Optional[int] = None) -> None:
        code = _native.lintp_tx_uds_msg(self._handle, bytes(payload), bool(functional), monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def tick(self, ts_ms: Optional[int] = None) -> None:
        code = _native.lintp_tick(self._handle, monotonic_ms() if ts_ms is None else int(ts_ms))
        _raise_if_error(code)

    def pop_tx_lin_frame(self) -> Optional[tuple[int, bytes]]:
        code, frame_id, data = _native.lintp_pop_tx_lin_frame(self._handle)
        _raise_if_error(code)
        if code == FFI_HAS_ITEM:
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
        code, payload = _native.lintp_rx_uds_msg(self._handle)
        _raise_if_error(code)
        if code == FFI_HAS_ITEM:
            return bytes(payload)
        return None

    def pop_error(self) -> Optional[int]:
        code, err = _native.lintp_pop_error(self._handle)
        _raise_if_error(code)
        return int(err) if code == FFI_HAS_ITEM else None


def step_once(tp: IsoTpEngine, rxfunc: Callable[[], Optional[CanMsg]], txfunc: Callable[[int, bytes, bool], None], ts_ms: Optional[int] = None) -> None:
    now = monotonic_ms() if ts_ms is None else int(ts_ms)
    while True:
        msg = rxfunc()
        if msg is None:
            break
        tp.on_can_frame(msg.id, msg.data, msg.isfd, ts_ms=now)
    tp.tick(ts_ms=now)
    while True:
        out = tp.pop_tx_can_frame()
        if out is None:
            break
        txfunc(*out)


def step_once_lin(tp: LinTpEngine, rxfunc: Callable[[], Optional[LinMsg]], txfunc: Callable[[int, bytes], None], ts_ms: Optional[int] = None) -> None:
    now = monotonic_ms() if ts_ms is None else int(ts_ms)
    while True:
        msg = rxfunc()
        if msg is None:
            break
        tp.on_lin_frame(msg.id, msg.data, ts_ms=now)
    tp.tick(ts_ms=now)
    while True:
        out = tp.pop_tx_lin_frame()
        if out is None:
            break
        txfunc(*out)


def send_uds_and_wait_final(
    tp: IsoTpEngine,
    payload: bytes,
    rxfunc: Callable[[], Optional[CanMsg]],
    txfunc: Callable[[int, bytes, bool], None],
    functional: bool = False,
    overall_timeout_ms: int = 10000,
    pending_gap_ms: int = 3000,
    poll_interval_ms: int = 1,
    response_matcher: Optional[Callable[[bytes], bool]] = None,
) -> bytes:
    matcher = response_matcher or build_uds_default_matcher(payload)
    deadline = monotonic_ms() + int(overall_timeout_ms)
    next_deadline = deadline
    tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
    while True:
        now = monotonic_ms()
        if now > next_deadline:
            raise IsoTpError(-106)
        step_once(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=now)
        while True:
            msg = tp.rx_uds_msg()
            if msg is None:
                break
            if not matcher(msg):
                tp._pending_uds.append(msg)
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                break
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
        if poll_interval_ms > 0:
            time.sleep(poll_interval_ms / 1000.0)


def send_uds_and_wait_final_lin(
    tp: LinTpEngine,
    payload: bytes,
    rxfunc: Callable[[], Optional[LinMsg]],
    txfunc: Callable[[int, bytes], None],
    functional: bool = False,
    overall_timeout_ms: int = 10000,
    pending_gap_ms: int = 3000,
    poll_interval_ms: int = 1,
    response_matcher: Optional[Callable[[bytes], bool]] = None,
) -> bytes:
    matcher = response_matcher or build_uds_default_matcher(payload)
    deadline = monotonic_ms() + int(overall_timeout_ms)
    next_deadline = deadline
    tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
    while True:
        now = monotonic_ms()
        if now > next_deadline:
            raise IsoTpError(-106)
        step_once_lin(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=now)
        while True:
            msg = tp.rx_uds_msg()
            if msg is None:
                break
            if not matcher(msg):
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                break
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
        if poll_interval_ms > 0:
            time.sleep(poll_interval_ms / 1000.0)


class IsoTpEngineWorker:
    def __init__(self, req_id: int, resp_id: int, func_id: int, is_fd: bool = False, cfg: Optional[TpConfig] = None, tick_period_ms: int = 1):
        self._tp = IsoTpEngine(req_id, resp_id, func_id, is_fd=is_fd, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._rx_frames: queue.Queue[CanMsg] = queue.Queue()
        self._tx_frames: queue.Queue[tuple[int, bytes, bool]] = queue.Queue()
        self._uds_msgs: queue.Queue[bytes] = queue.Queue()
        self._errors: queue.Queue[int] = queue.Queue()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="IsoTpEngineWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        self._tp.close()

    def close(self) -> None:
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            now = monotonic_ms()
            try:
                while True:
                    msg = self._rx_frames.get_nowait()
                    try:
                        self._tp.on_can_frame(msg.id, msg.data, msg.isfd, ts_ms=now)
                    except IsoTpError as exc:
                        self._errors.put(exc.code)
            except queue.Empty:
                pass
            try:
                self._tp.tick(ts_ms=now)
            except IsoTpError as exc:
                self._errors.put(exc.code)
            while True:
                frame = self._tp.pop_tx_can_frame()
                if frame is None:
                    break
                self._tx_frames.put(frame)
            while True:
                msg = self._tp.rx_uds_msg()
                if msg is None:
                    break
                self._uds_msgs.put(msg)
            while True:
                err = self._tp.pop_error()
                if err is None:
                    break
                self._errors.put(err)
            self._stop_evt.wait(self._tick_period_ms / 1000.0)

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool = False) -> None:
        self._rx_frames.put(CanMsg(int(can_id), bytes(data), bool(is_fd)))

    def pop_tx_can_frame(self, timeout_s: float = 0.0) -> Optional[tuple[int, bytes, bool]]:
        try:
            return self._tx_frames.get(timeout=timeout_s) if timeout_s > 0 else self._tx_frames.get_nowait()
        except queue.Empty:
            return None

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        try:
            return self._errors.get(timeout=timeout_s) if timeout_s > 0 else self._errors.get_nowait()
        except queue.Empty:
            return None

    def tx_uds_msg(self, payload: bytes, functional: bool = False, response_timeout_ms: Optional[int] = 10000, pending_gap_ms: int = 3000, poll_interval_ms: int = 1) -> bytes:
        self._tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
        matcher = build_uds_default_matcher(payload)
        if response_timeout_ms is None:
            return b""
        deadline = monotonic_ms() + int(response_timeout_ms)
        next_deadline = deadline
        stash: list[bytes] = []
        while True:
            timeout_left = max(0.0, (next_deadline - monotonic_ms()) / 1000.0)
            if timeout_left <= 0:
                for msg in stash:
                    self._uds_msgs.put(msg)
                raise IsoTpError(-106)
            try:
                msg = self._uds_msgs.get(timeout=min(timeout_left, max(poll_interval_ms, 1) / 1000.0))
            except queue.Empty:
                err = self.pop_error(timeout_s=0.0)
                if err is not None:
                    for item in stash:
                        self._uds_msgs.put(item)
                    raise IsoTpError(err)
                continue
            if not matcher(msg):
                stash.append(msg)
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                continue
            for item in stash:
                self._uds_msgs.put(item)
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg


class LinTpEngineWorker:
    def __init__(self, req_frame_id: int, resp_frame_id: int, req_nad: int, func_nad: int, cfg: Optional[LinTpConfig] = None, tick_period_ms: int = 1):
        self._tp = LinTpEngine(req_frame_id, resp_frame_id, req_nad, func_nad, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._rx_frames: queue.Queue[LinMsg] = queue.Queue()
        self._tx_frames: queue.Queue[tuple[int, bytes]] = queue.Queue()
        self._uds_msgs: queue.Queue[bytes] = queue.Queue()
        self._errors: queue.Queue[int] = queue.Queue()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.req_nad = int(req_nad)
        self.func_nad = int(func_nad)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="LinTpEngineWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        self._tp.close()

    def close(self) -> None:
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            now = monotonic_ms()
            try:
                while True:
                    msg = self._rx_frames.get_nowait()
                    try:
                        self._tp.on_lin_frame(msg.id, msg.data, ts_ms=now)
                    except IsoTpError as exc:
                        self._errors.put(exc.code)
            except queue.Empty:
                pass
            try:
                self._tp.tick(ts_ms=now)
            except IsoTpError as exc:
                self._errors.put(exc.code)
            while True:
                frame = self._tp.pop_tx_lin_frame()
                if frame is None:
                    break
                self._tx_frames.put(frame)
            while True:
                msg = self._tp.rx_uds_msg()
                if msg is None:
                    break
                self._uds_msgs.put(msg)
            while True:
                err = self._tp.pop_error()
                if err is None:
                    break
                self._errors.put(err)
            self._stop_evt.wait(self._tick_period_ms / 1000.0)

    def on_lin_frame(self, frame_id: int, data: bytes) -> None:
        self._rx_frames.put(LinMsg(int(frame_id), bytes(data)))

    def pop_tx_lin_frame(self, timeout_s: float = 0.0) -> Optional[tuple[int, bytes]]:
        try:
            return self._tx_frames.get(timeout=timeout_s) if timeout_s > 0 else self._tx_frames.get_nowait()
        except queue.Empty:
            return None

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        try:
            return self._errors.get(timeout=timeout_s) if timeout_s > 0 else self._errors.get_nowait()
        except queue.Empty:
            return None

    def tx_uds_msg(
        self,
        payload: bytes,
        functional: bool = False,
        req_nad_override: Optional[int] = None,
        func_nad_override: Optional[int] = None,
        response_timeout_ms: Optional[int] = 10000,
        pending_gap_ms: int = 3000,
        poll_interval_ms: int = 1,
    ) -> bytes:
        if req_nad_override is not None or func_nad_override is not None:
            self._tp.set_nad(self.req_nad if req_nad_override is None else int(req_nad_override), self.func_nad if func_nad_override is None else int(func_nad_override))
        self._tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
        matcher = build_uds_default_matcher(payload)
        if response_timeout_ms is None:
            return b""
        deadline = monotonic_ms() + int(response_timeout_ms)
        next_deadline = deadline
        stash: list[bytes] = []
        while True:
            timeout_left = max(0.0, (next_deadline - monotonic_ms()) / 1000.0)
            if timeout_left <= 0:
                raise IsoTpError(-106)
            try:
                msg = self._uds_msgs.get(timeout=min(timeout_left, max(poll_interval_ms, 1) / 1000.0))
            except queue.Empty:
                err = self.pop_error(timeout_s=0.0)
                if err is not None:
                    raise IsoTpError(err)
                continue
            if not matcher(msg):
                stash.append(msg)
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                continue
            for item in stash:
                self._uds_msgs.put(item)
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
