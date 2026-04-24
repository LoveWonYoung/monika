import logging
import threading
import time
from typing import Optional

from ..bindings.isotp import (
    CanMsg,
    IsoTpEngine,
    IsoTpEngineWorker,
    TpConfig,
    monotonic_ms,
    send_uds_and_wait_final,
    step_once,
)

from .interface import CanDeviceInterface


logger = logging.getLogger(__name__)


class CanTpClient:
    """Compose a CAN hardware adapter with IsoTpEngine and expose UDS request API."""

    def __init__(
        self,
        hw: CanDeviceInterface,
        req_id: int = 0x7E0,
        resp_id: int = 0x7E8,
        func_id: int = 0x7DF,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
    ):
        self._hw = hw
        self._tp = IsoTpEngine(
            req_id=req_id,
            resp_id=resp_id,
            func_id=func_id,
            is_fd=is_fd,
            cfg=cfg or TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=20, block_size=0),
        )
        self._tp_lock = threading.Lock()
        self._keep_alive_stop_evt = threading.Event()
        self._keep_alive_thread: Optional[threading.Thread] = None

    def close(self) -> None:
        self.stop_keep_alive()
        with self._tp_lock:
            self._tp.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _rxfunc_for_isotp(self) -> Optional[CanMsg]:
        msg = self._hw.rxfn()
        if msg is None:
            return None
        return CanMsg(id=msg.id, data=msg.data, isfd=msg.isfd)

    def uds_request(self, payload: bytes, functional: bool = False, timeout_ms: int = 10000) -> bytes:
        with self._tp_lock:
            return send_uds_and_wait_final(
                tp=self._tp,
                payload=payload,
                rxfunc=self._rxfunc_for_isotp,
                txfunc=self._hw.txfn,
                functional=functional,
                overall_timeout_ms=timeout_ms,
                pending_gap_ms=3000,
                poll_interval_ms=1,
            )

    def _build_tester_present_sf(self, functional: bool) -> tuple[int, bytes, bool]:
        """Build a raw ISO-TP single-frame TesterPresent (0x3E 0x80) without
        touching the TP engine, so it can be sent from any thread at any time."""
        can_id = self._tp.func_id if functional else self._tp.req_id
        # ISO-TP SF PCI: high nibble = 0 (SF), low nibble = length (2)
        frame = bytes([0x02, 0x3E, 0x80])
        if not self._tp.is_fd:
            frame = frame.ljust(8, b"\x00")
        return can_id, frame, self._tp.is_fd

    def keep_alive(
        self,
        interval_s: float = 2.0,
        functional: bool = True,
        locking: bool = True,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Periodically send TesterPresent (0x3E 0x80).

        locking=True  (default): acquire _tp_lock before each send. The
            TesterPresent is serialized with uds_request, so it never lands
            inside a consecutive-frame window. The trade-off is that it may
            be delayed until the current multi-frame exchange completes.

        locking=False: bypass _tp_lock entirely by constructing the ISO-TP
            single frame manually and writing it straight to the hardware.
            The frame can be injected between consecutive frames, which keeps
            the ECU session alive even during long multi-frame transfers, but
            may violate the N_Cs timing contract perceived by the ECU.
        """
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")

        evt = stop_event or self._keep_alive_stop_evt
        keep_alive_payload = bytes([0x3E, 0x80])
        while not evt.is_set():
            try:
                if locking:
                    with self._tp_lock:
                        self._tp.tx_uds_msg(
                            payload=keep_alive_payload,
                            functional=functional,
                            ts_ms=monotonic_ms(),
                            response_timeout_ms=None,
                        )
                        step_once(tp=self._tp, rxfunc=self._rxfunc_for_isotp, txfunc=self._hw.txfn)
                        self._tp.clear_pending_uds_messages()
                else:
                    can_id, frame, is_fd = self._build_tester_present_sf(functional)
                    self._hw.txfn(can_id, frame, is_fd)
            except Exception:
                logger.exception("keep_alive error")

            evt.wait(interval_s)

    def start_keep_alive(
        self,
        interval_s: float = 2.0,
        functional: bool = True,
        locking: bool = True,
    ) -> None:
        if self._keep_alive_thread is not None and self._keep_alive_thread.is_alive():
            return

        self._keep_alive_stop_evt.clear()
        self._keep_alive_thread = threading.Thread(
            target=self.keep_alive,
            kwargs={
                "interval_s": interval_s,
                "functional": functional,
                "locking": locking,
                "stop_event": self._keep_alive_stop_evt,
            },
            name="CanTpKeepAlive",
            daemon=True,
        )
        self._keep_alive_thread.start()

    def stop_keep_alive(self, timeout_s: float = 1.0) -> None:
        self._keep_alive_stop_evt.set()
        if self._keep_alive_thread is not None:
            self._keep_alive_thread.join(timeout=timeout_s)
            self._keep_alive_thread = None

    def pop_error(self) -> Optional[int]:
        return self._tp.pop_error()


class CanTpWorker:
    """Compose a CAN hardware adapter with IsoTpEngineWorker and a bridge thread."""

    def __init__(
        self,
        hw: CanDeviceInterface,
        req_id: int = 0x7E0,
        resp_id: int = 0x7E8,
        func_id: int = 0x7DF,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        tick_period_ms: int = 1,
        bridge_sleep_ms: int = 1,
        bridge_busy_sleep_ms: int = 0,
        worker_queue_size: int = 1024,
        log_request_ms: bool = False,
    ):
        self._hw = hw
        self._req_id = req_id
        self._func_id = func_id
        self._is_fd = is_fd
        self._worker = IsoTpEngineWorker(
            req_id=req_id,
            resp_id=resp_id,
            func_id=func_id,
            is_fd=is_fd,
            cfg=cfg or TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=20, block_size=0),
            tick_period_ms=tick_period_ms,
            queue_size=worker_queue_size,
        )
        self._bridge_sleep_s = max(0.0, bridge_sleep_ms / 1000.0)
        self._bridge_busy_sleep_s = max(0.0, bridge_busy_sleep_ms / 1000.0)
        self._log_request_ms = log_request_ms
        self._stop_evt = threading.Event()
        self._bridge_thread: Optional[threading.Thread] = None
        self._keep_alive_stop_evt = threading.Event()
        self._keep_alive_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._worker.start()
        if self._bridge_thread is not None and self._bridge_thread.is_alive():
            return
        self._stop_evt.clear()
        self._bridge_thread = threading.Thread(target=self._bridge_loop, name="CanTpBridge", daemon=True)
        self._bridge_thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self.stop_keep_alive(timeout_s=timeout_s)
        self._stop_evt.set()
        bridge_thread = self._bridge_thread
        if bridge_thread is not None:
            bridge_thread.join(timeout=timeout_s)
            if bridge_thread.is_alive():
                raise TimeoutError(f"CanTpBridge thread did not stop within {timeout_s} seconds")
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
        while not self._stop_evt.is_set():
            has_work = False

            while True:
                msg = self._hw.rxfn()
                if msg is None:
                    break
                has_work = True
                self._worker.on_can_frame(msg.id, msg.data, msg.isfd)

            while True:
                out_batch = self._worker.pop_tx_can_frames(max_frames=128, timeout_s=0.0)
                if not out_batch:
                    break
                has_work = True
                for can_id, data, is_fd in out_batch:
                    self._hw.txfn(can_id, data, is_fd)

            if has_work and self._bridge_busy_sleep_s > 0:
                time.sleep(self._bridge_busy_sleep_s)
            elif not has_work and self._bridge_sleep_s > 0:
                time.sleep(self._bridge_sleep_s)

    def uds_request(
        self,
        payload: bytes,
        functional: bool = False,
        timeout_ms: int = 10000,
    ) -> bytes:
        if self._bridge_thread is None or not self._bridge_thread.is_alive():
            raise RuntimeError("CanTpWorker is not running; call start() first")
        t0 = time.perf_counter()
        try:
            return self._worker.tx_uds_msg(
                payload=payload,
                functional=functional,
                response_timeout_ms=timeout_ms,
                pending_gap_ms=3000,
                poll_interval_ms=1,
            )
        finally:
            if self._log_request_ms:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                logger.info(
                    "uds_request elapsed %.2f ms (functional=%s, timeout_ms=%s, payload_len=%d)",
                    elapsed_ms,
                    functional,
                    timeout_ms,
                    len(payload),
                )

    def _build_tester_present_sf(self, functional: bool) -> tuple[int, bytes, bool]:
        """Build a raw ISO-TP single-frame TesterPresent (0x3E 0x80) without
        touching the TP engine, so it can be sent from any thread at any time."""
        can_id = self._func_id if functional else self._req_id
        frame = bytes([0x02, 0x3E, 0x80])
        if not self._is_fd:
            frame = frame.ljust(8, b"\x00")
        return can_id, frame, self._is_fd

    def keep_alive(
        self,
        interval_s: float = 2.0,
        functional: bool = True,
        locking: bool = True,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Periodically send TesterPresent (0x3E 0x80).

        locking=True  (default): route through IsoTpEngineWorker.tx_uds_msg,
            which is serialized with the internal worker lock. TesterPresent
            is queued after the current exchange completes; no CF interruption.

        locking=False: construct the ISO-TP single frame manually and write
            it directly to hardware via the bridge's txfn. The frame bypasses
            the worker queue and can be injected between consecutive frames.
        """
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")

        evt = stop_event or self._keep_alive_stop_evt
        keep_alive_payload = bytes([0x3E, 0x80])
        while not evt.is_set():
            try:
                if locking:
                    self._worker.tx_uds_msg(
                        payload=keep_alive_payload,
                        functional=functional,
                        response_timeout_ms=None,
                    )
                else:
                    can_id, frame, is_fd = self._build_tester_present_sf(functional)
                    self._hw.txfn(can_id, frame, is_fd)
            except Exception:
                logger.exception("worker keep_alive error")

            evt.wait(interval_s)

    def start_keep_alive(
        self,
        interval_s: float = 2.0,
        functional: bool = True,
        locking: bool = True,
    ) -> None:
        if self._keep_alive_thread is not None and self._keep_alive_thread.is_alive():
            return

        self.start()
        self._keep_alive_stop_evt.clear()
        self._keep_alive_thread = threading.Thread(
            target=self.keep_alive,
            kwargs={
                "interval_s": interval_s,
                "functional": functional,
                "locking": locking,
                "stop_event": self._keep_alive_stop_evt,
            },
            name="CanTpWorkerKeepAlive",
            daemon=True,
        )
        self._keep_alive_thread.start()

    def stop_keep_alive(self, timeout_s: float = 1.0) -> None:
        self._keep_alive_stop_evt.set()
        keep_alive_thread = self._keep_alive_thread
        if keep_alive_thread is not None:
            keep_alive_thread.join(timeout=timeout_s)
            if keep_alive_thread.is_alive():
                raise TimeoutError(f"CanTpWorkerKeepAlive thread did not stop within {timeout_s} seconds")
            self._keep_alive_thread = None

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        return self._worker.pop_error(timeout_s=timeout_s)
