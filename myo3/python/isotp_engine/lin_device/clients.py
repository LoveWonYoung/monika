import logging
import threading
import time
from typing import Optional, Protocol

from ..bindings.lintp import LinTpConfig, LinTpEngineWorker


logger = logging.getLogger(__name__)


class LinMasterDeviceInterface(Protocol):
    def request_slave_response(self, frame_id: int):
        ...

    def rxfn(self):
        ...

    def txfn(self, frame_id: int, data: bytes) -> None:
        ...


class LinTpWorker:
    """Compose a LIN master hardware adapter with LinTpEngineWorker and a bridge thread."""

    def __init__(
        self,
        hw: LinMasterDeviceInterface,
        req_frame_id: int = 0x3C,
        resp_frame_id: int = 0x3D,
        req_nad: int = 0x01,
        func_nad: int = 0x7F,
        cfg: Optional[LinTpConfig] = None,
        tick_period_ms: int = 1,
        bridge_sleep_ms: int = 1,
        resp_poll_interval_ms: int = 1,
    ):
        if resp_poll_interval_ms <= 0:
            raise ValueError("resp_poll_interval_ms must be > 0")

        self._hw = hw
        self._req_frame_id = req_frame_id
        self._resp_frame_id = resp_frame_id
        self._worker = LinTpEngineWorker(
            req_frame_id=req_frame_id,
            resp_frame_id=resp_frame_id,
            req_nad=req_nad,
            func_nad=func_nad,
            cfg=cfg or LinTpConfig(n_cr_ms=1000, max_pdu_len=4095),
            tick_period_ms=tick_period_ms,
        )
        self._bridge_sleep_s = max(0.0, bridge_sleep_ms / 1000.0)
        self._resp_poll_interval_s = resp_poll_interval_ms / 1000.0
        self._stop_evt = threading.Event()
        self._bridge_thread: Optional[threading.Thread] = None
        self._keep_alive_stop_evt = threading.Event()
        self._keep_alive_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._worker.start()
        if self._bridge_thread is not None and self._bridge_thread.is_alive():
            return
        self._stop_evt.clear()
        self._bridge_thread = threading.Thread(target=self._bridge_loop, name="LinTpBridge", daemon=True)
        self._bridge_thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self.stop_keep_alive(timeout_s=timeout_s)
        self._stop_evt.set()
        if self._bridge_thread is not None:
            self._bridge_thread.join(timeout=timeout_s)
            self._bridge_thread = None
        self._worker.stop(timeout_s=timeout_s)

    def close(self) -> None:
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _bridge_loop(self) -> None:
        next_resp_poll_s = time.monotonic()
        while not self._stop_evt.is_set():
            has_work = False
            now_s = time.monotonic()

            if now_s >= next_resp_poll_s:
                try:
                    # Master mode: periodically request slave response from diagnostic response frame.
                    rx = self._hw.request_slave_response(self._resp_frame_id)
                    if rx is not None:
                        has_work = True
                except Exception:
                    logger.exception("LIN request_slave_response failed")

                while next_resp_poll_s <= now_s:
                    next_resp_poll_s += self._resp_poll_interval_s

            while True:
                msg = self._hw.rxfn()
                if msg is None:
                    break
                has_work = True
                self._worker.on_lin_frame(msg.id, msg.data)

            while True:
                out = self._worker.pop_tx_lin_frame(timeout_s=0.0)
                if out is None:
                    break
                has_work = True
                frame_id, data = out
                self._hw.txfn(frame_id, data)

            if not has_work and self._bridge_sleep_s > 0:
                now_s = time.monotonic()
                sleep_s = min(self._bridge_sleep_s, max(0.0, next_resp_poll_s - now_s))
                if sleep_s > 0:
                    time.sleep(sleep_s)

    def uds_request(
        self,
        payload: bytes,
        functional: bool = False,
        timeout_ms: int = 10000,
        req_nad: Optional[int] = None,
        func_nad: Optional[int] = None,
    ) -> bytes:
        return self._worker.tx_uds_msg(
            payload=payload,
            functional=functional,
            req_nad_override=req_nad,
            func_nad_override=func_nad,
            response_timeout_ms=timeout_ms,
            pending_gap_ms=3000,
            poll_interval_ms=1,
        )

    def keep_alive(
        self,
        interval_s: float = 2.0,
        functional: bool = True,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")

        evt = stop_event or self._keep_alive_stop_evt
        keep_alive_payload = bytes([0x3E, 0x80])
        while not evt.is_set():
            try:
                self._worker.tx_uds_msg(
                    payload=keep_alive_payload,
                    functional=functional,
                    response_timeout_ms=None,
                )
            except Exception:
                logger.exception("lin worker keep_alive error")

            evt.wait(interval_s)

    def start_keep_alive(self, interval_s: float = 2.0, functional: bool = True) -> None:
        if self._keep_alive_thread is not None and self._keep_alive_thread.is_alive():
            return

        self.start()
        self._keep_alive_stop_evt.clear()
        self._keep_alive_thread = threading.Thread(
            target=self.keep_alive,
            kwargs={
                "interval_s": interval_s,
                "functional": functional,
                "stop_event": self._keep_alive_stop_evt,
            },
            name="LinTpWorkerKeepAlive",
            daemon=True,
        )
        self._keep_alive_thread.start()

    def stop_keep_alive(self, timeout_s: float = 1.0) -> None:
        self._keep_alive_stop_evt.set()
        if self._keep_alive_thread is not None:
            self._keep_alive_thread.join(timeout=timeout_s)
            self._keep_alive_thread = None

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        return self._worker.pop_error(timeout_s=timeout_s)
