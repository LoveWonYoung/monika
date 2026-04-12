from __future__ import annotations

from collections import deque
import queue
import threading
from collections.abc import Callable
from typing import Optional

from .engine import IsoTpEngine, LinTpEngine
from .helpers import build_uds_default_matcher, is_uds_response_pending, monotonic_ms
from .types import CanMsg, IsoTpError, LinMsg, LinTpConfig, TpConfig, UdsNegativeResponseError


ERR_QUEUE_OVERFLOW = -120


def _queue_put_drop_oldest(q: queue.Queue, item: object) -> bool:
    try:
        q.put_nowait(item)
        return False
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            return True
        return True


def _restore_stash_front(pending_uds: deque[bytes], stash: list[bytes]) -> None:
    if stash:
        pending_uds.extendleft(reversed(stash))


def _wait_for_matching_response(
    *,
    payload: bytes,
    uds_queue: queue.Queue[bytes],
    pending_uds: deque[bytes],
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
    try:
        while True:
            timeout_left = max(0.0, (next_deadline - monotonic_ms()) / 1000.0)
            if timeout_left <= 0:
                raise IsoTpError(-106)

            wait_s = min(timeout_left, poll_timeout_s)

            if pending_uds:
                msg = pending_uds.popleft()
            else:
                try:
                    msg = uds_queue.get(timeout=wait_s)
                except queue.Empty:
                    err = pop_error(0.0)
                    if err is not None:
                        raise IsoTpError(err)
                    continue

            if not matcher(msg):
                stash.append(msg)
                continue

            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + pending_gap_ms)
                continue

            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
    finally:
        _restore_stash_front(pending_uds, stash)


class IsoTpEngineWorker:
    def __init__(
        self,
        req_id: int,
        resp_id: int,
        func_id: int,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        tick_period_ms: int = 1,
        queue_size: int = 1024,
    ):
        self._tp = IsoTpEngine(req_id, resp_id, func_id, is_fd=is_fd, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._queue_size = max(1, int(queue_size))
        self._rx_frames: queue.Queue[CanMsg] = queue.Queue(maxsize=self._queue_size)
        self._tx_cmd_queue: queue.Queue[tuple[bytes, bool]] = queue.Queue(maxsize=self._queue_size)
        self._tx_frames: queue.Queue[tuple[int, bytes, bool]] = queue.Queue(maxsize=self._queue_size)
        self._uds_msgs: queue.Queue[bytes] = queue.Queue(maxsize=self._queue_size)
        self._errors: queue.Queue[int] = queue.Queue(maxsize=self._queue_size)
        self._pending_uds_wait: deque[bytes] = deque()
        self._request_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _push_error(self, code: int) -> None:
        _queue_put_drop_oldest(self._errors, int(code))

    def _push_rx_frame(self, msg: CanMsg) -> None:
        if _queue_put_drop_oldest(self._rx_frames, msg):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def _push_tx_frame(self, frame: tuple[int, bytes, bool]) -> None:
        if _queue_put_drop_oldest(self._tx_frames, frame):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def _push_uds_msg(self, msg: bytes) -> None:
        if _queue_put_drop_oldest(self._uds_msgs, msg):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="IsoTpEngineWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop_evt.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                raise TimeoutError(f"IsoTpEngineWorker thread did not stop within {timeout_s} seconds")
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
                        self._push_error(exc.code)
            except queue.Empty:
                pass
            try:
                while True:
                    msg = self._rx_frames.get_nowait()
                    try:
                        self._tp.on_can_frame(msg.id, msg.data, msg.isfd, ts_ms=now)
                    except IsoTpError as exc:
                        self._push_error(exc.code)
            except queue.Empty:
                pass
            try:
                self._tp.tick(ts_ms=now)
            except IsoTpError as exc:
                self._push_error(exc.code)
            while True:
                frames = self._tp.pop_tx_can_frames(max_frames=128)
                if not frames:
                    break
                for frame in frames:
                    self._push_tx_frame(frame)
            while True:
                msg = self._tp.rx_uds_msg()
                if msg is None:
                    break
                self._push_uds_msg(msg)
            while True:
                err = self._tp.pop_error()
                if err is None:
                    break
                self._push_error(err)
            self._stop_evt.wait(self._tick_period_ms / 1000.0)

    def on_can_frame(self, can_id: int, data: bytes, is_fd: bool = False) -> None:
        self._push_rx_frame(CanMsg(int(can_id), bytes(data), bool(is_fd)))

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
        with self._request_lock:
            try:
                self._tx_cmd_queue.put_nowait((req_payload, bool(functional)))
            except queue.Full:
                raise IsoTpError(ERR_QUEUE_OVERFLOW)
            return _wait_for_matching_response(
                payload=req_payload,
                uds_queue=self._uds_msgs,
                pending_uds=self._pending_uds_wait,
                pop_error=self.pop_error,
                response_timeout_ms=response_timeout_ms,
                pending_gap_ms=pending_gap_ms,
                poll_interval_ms=poll_interval_ms,
            )


class LinTpEngineWorker:
    def __init__(
        self,
        req_frame_id: int,
        resp_frame_id: int,
        req_nad: int,
        func_nad: int,
        cfg: Optional[LinTpConfig] = None,
        tick_period_ms: int = 1,
        queue_size: int = 1024,
    ):
        self._tp = LinTpEngine(req_frame_id, resp_frame_id, req_nad, func_nad, cfg=cfg)
        self._tick_period_ms = max(1, int(tick_period_ms))
        self._queue_size = max(1, int(queue_size))
        self._rx_frames: queue.Queue[LinMsg] = queue.Queue(maxsize=self._queue_size)
        # (payload, functional, req_nad_override_or_None, func_nad_override_or_None)
        self._tx_cmd_queue: queue.Queue[tuple[bytes, bool, Optional[int], Optional[int]]] = queue.Queue(maxsize=self._queue_size)
        self._tx_frames: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=self._queue_size)
        self._uds_msgs: queue.Queue[bytes] = queue.Queue(maxsize=self._queue_size)
        self._errors: queue.Queue[int] = queue.Queue(maxsize=self._queue_size)
        self._pending_uds_wait: deque[bytes] = deque()
        self._request_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.req_nad = int(req_nad)
        self.func_nad = int(func_nad)

    def _push_error(self, code: int) -> None:
        _queue_put_drop_oldest(self._errors, int(code))

    def _push_rx_frame(self, msg: LinMsg) -> None:
        if _queue_put_drop_oldest(self._rx_frames, msg):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def _push_tx_frame(self, frame: tuple[int, bytes]) -> None:
        if _queue_put_drop_oldest(self._tx_frames, frame):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def _push_uds_msg(self, msg: bytes) -> None:
        if _queue_put_drop_oldest(self._uds_msgs, msg):
            self._push_error(ERR_QUEUE_OVERFLOW)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="LinTpEngineWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop_evt.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                raise TimeoutError(f"LinTpEngineWorker thread did not stop within {timeout_s} seconds")
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
                        self._push_error(exc.code)
            except queue.Empty:
                pass
            try:
                while True:
                    msg = self._rx_frames.get_nowait()
                    try:
                        self._tp.on_lin_frame(msg.id, msg.data, ts_ms=now)
                    except IsoTpError as exc:
                        self._push_error(exc.code)
            except queue.Empty:
                pass
            try:
                self._tp.tick(ts_ms=now)
            except IsoTpError as exc:
                self._push_error(exc.code)
            while True:
                frame = self._tp.pop_tx_lin_frame()
                if frame is None:
                    break
                self._push_tx_frame(frame)
            while True:
                msg = self._tp.rx_uds_msg()
                if msg is None:
                    break
                self._push_uds_msg(msg)
            while True:
                err = self._tp.pop_error()
                if err is None:
                    break
                self._push_error(err)
            self._stop_evt.wait(self._tick_period_ms / 1000.0)

    def on_lin_frame(self, frame_id: int, data: bytes) -> None:
        self._push_rx_frame(LinMsg(int(frame_id), bytes(data)))

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
        with self._request_lock:
            try:
                self._tx_cmd_queue.put_nowait((req_payload, bool(functional), req_nad, func_nad))
            except queue.Full:
                raise IsoTpError(ERR_QUEUE_OVERFLOW)
            return _wait_for_matching_response(
                payload=req_payload,
                uds_queue=self._uds_msgs,
                pending_uds=self._pending_uds_wait,
                pop_error=self.pop_error,
                response_timeout_ms=response_timeout_ms,
                pending_gap_ms=pending_gap_ms,
                poll_interval_ms=poll_interval_ms,
            )
