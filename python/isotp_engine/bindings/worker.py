from __future__ import annotations

import queue
import threading
from typing import Optional

from .engine import IsoTpEngine, LinTpEngine
from .helpers import build_uds_default_matcher, is_uds_response_pending, monotonic_ms
from .types import CanMsg, IsoTpError, LinMsg, LinTpConfig, TpConfig, UdsNegativeResponseError


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
