from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Optional

from .engine import IsoTpEngine, LinTpEngine
from .helpers import build_uds_default_matcher, is_uds_response_pending, monotonic_ms
from .types import CanMsg, IsoTpError, LinMsg, LinTpConfig, TpConfig, UdsNegativeResponseError


def _requeue_stash(uds_queue: queue.Queue[bytes], stash: list[bytes]) -> None:
    for msg in stash:
        uds_queue.put(msg)


def _wait_for_matching_response(
    *,
    payload: bytes,
    uds_queue: queue.Queue[bytes],
    pop_error: Callable[[float], Optional[int]],
    response_timeout_ms: Optional[int],
    pending_gap_ms: int,
    poll_interval_ms: int,
) -> bytes:
    if response_timeout_ms is None:
        return b""

    matcher = build_uds_default_matcher(payload)
    deadline = monotonic_ms() + int(response_timeout_ms)
    next_deadline = deadline
    pending_gap_ms = int(pending_gap_ms)
    # Keep periodic wakeups to surface async transport errors promptly.
    poll_timeout_s = max(0.001, int(poll_interval_ms) / 1000.0)

    stash: list[bytes] = []
    while True:
        timeout_left = max(0.0, (next_deadline - monotonic_ms()) / 1000.0)
        if timeout_left <= 0:
            _requeue_stash(uds_queue, stash)
            raise IsoTpError(-106)

        wait_s = min(timeout_left, poll_timeout_s)

        try:
            msg = uds_queue.get(timeout=wait_s)
        except queue.Empty:
            err = pop_error(0.0)
            if err is not None:
                _requeue_stash(uds_queue, stash)
                raise IsoTpError(err)
            continue

        if not matcher(msg):
            stash.append(msg)
            continue

        if is_uds_response_pending(msg):
            next_deadline = min(deadline, monotonic_ms() + pending_gap_ms)
            continue

        _requeue_stash(uds_queue, stash)
        if msg[:1] == b"\x7f":
            raise UdsNegativeResponseError(msg)
        return msg


class IsoTpEngineWorker:
    def __init__(self, req_id: int, resp_id: int, func_id: int, is_fd: bool = False, cfg: Optional[TpConfig] = None, tick_period_ms: int = 1):
        self._tp = IsoTpEngine(req_id, resp_id, func_id, is_fd=is_fd, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._rx_frames: queue.Queue[CanMsg] = queue.Queue()
        self._tx_cmd_queue: queue.Queue[tuple[bytes, bool]] = queue.Queue()
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
                    payload, functional = self._tx_cmd_queue.get_nowait()
                    try:
                        self._tp.tx_uds_msg(payload, functional=functional, ts_ms=now)
                    except IsoTpError as exc:
                        self._errors.put(exc.code)
            except queue.Empty:
                pass
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
                frames = self._tp.pop_tx_can_frames(max_frames=128)
                if not frames:
                    break
                for frame in frames:
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

    def pop_tx_can_frames(self, max_frames: int = 64, timeout_s: float = 0.0) -> list[tuple[int, bytes, bool]]:
        cap = max(1, int(max_frames))
        first = self.pop_tx_can_frame(timeout_s=timeout_s)
        if first is None:
            return []
        out = [first]
        while len(out) < cap:
            try:
                out.append(self._tx_frames.get_nowait())
            except queue.Empty:
                break
        return out

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        try:
            return self._errors.get(timeout=timeout_s) if timeout_s > 0 else self._errors.get_nowait()
        except queue.Empty:
            return None

    def tx_uds_msg(self, payload: bytes, functional: bool = False, response_timeout_ms: Optional[int] = 10000, pending_gap_ms: int = 3000, poll_interval_ms: int = 1) -> bytes:
        req_payload = payload if isinstance(payload, bytes) else bytes(payload)
        self._tx_cmd_queue.put((req_payload, bool(functional)))
        return _wait_for_matching_response(
            payload=req_payload,
            uds_queue=self._uds_msgs,
            pop_error=self.pop_error,
            response_timeout_ms=response_timeout_ms,
            pending_gap_ms=pending_gap_ms,
            poll_interval_ms=poll_interval_ms,
        )


class LinTpEngineWorker:
    def __init__(self, req_frame_id: int, resp_frame_id: int, req_nad: int, func_nad: int, cfg: Optional[LinTpConfig] = None, tick_period_ms: int = 1):
        self._tp = LinTpEngine(req_frame_id, resp_frame_id, req_nad, func_nad, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._rx_frames: queue.Queue[LinMsg] = queue.Queue()
        # (payload, functional, req_nad_override_or_None, func_nad_override_or_None)
        self._tx_cmd_queue: queue.Queue[tuple[bytes, bool, Optional[int], Optional[int]]] = queue.Queue()
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
                    payload, functional, req_nad, func_nad = self._tx_cmd_queue.get_nowait()
                    try:
                        if req_nad is not None or func_nad is not None:
                            new_req = req_nad if req_nad is not None else self._tp.req_nad
                            new_func = func_nad if func_nad is not None else self._tp.func_nad
                            self._tp.set_nad(new_req, new_func)
                            self.req_nad = int(self._tp.req_nad)
                            self.func_nad = int(self._tp.func_nad)
                        self._tp.tx_uds_msg(payload, functional=functional, ts_ms=now)
                    except IsoTpError as exc:
                        self._errors.put(exc.code)
            except queue.Empty:
                pass
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

    def pop_tx_lin_frames(self, max_frames: int = 64, timeout_s: float = 0.0) -> list[tuple[int, bytes]]:
        cap = max(1, int(max_frames))
        first = self.pop_tx_lin_frame(timeout_s=timeout_s)
        if first is None:
            return []
        out = [first]
        while len(out) < cap:
            try:
                out.append(self._tx_frames.get_nowait())
            except queue.Empty:
                break
        return out

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
        req_nad = int(req_nad_override) if req_nad_override is not None else None
        func_nad = int(func_nad_override) if func_nad_override is not None else None
        req_payload = payload if isinstance(payload, bytes) else bytes(payload)
        self._tx_cmd_queue.put((req_payload, bool(functional), req_nad, func_nad))
        return _wait_for_matching_response(
            payload=req_payload,
            uds_queue=self._uds_msgs,
            pop_error=self.pop_error,
            response_timeout_ms=response_timeout_ms,
            pending_gap_ms=pending_gap_ms,
            poll_interval_ms=poll_interval_ms,
        )
