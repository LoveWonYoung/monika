from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque, Optional

from ..bindings import CanMsg, IsoTpEngine, IsoTpError, TpConfig, monotonic_ms, step_once
from .interface import CanDeviceInterface

try:
    from udsoncan.connections import BaseConnection
    from udsoncan.exceptions import TimeoutException

    _UDSONCAN_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    BaseConnection = object  # type: ignore[misc, assignment]
    TimeoutException = RuntimeError  # type: ignore[misc, assignment]
    _UDSONCAN_IMPORT_ERROR = exc


class UdsoncanIsoTpConnection(BaseConnection):
    """
    udsoncan BaseConnection adapter backed by the Rust IsoTpEngine.

    This adapter keeps UDS semantics in udsoncan and only handles TP transport
    (ISO-TP segmentation/reassembly + CAN I/O pumping).
    """

    def __init__(
        self,
        hw: CanDeviceInterface,
        req_id: int = 0x7E0,
        resp_id: int = 0x7E8,
        func_id: int = 0x7DF,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        poll_interval_ms: int = 1,
        max_pending_responses: int = 256,
        close_hw_on_close: bool = False,
        name: Optional[str] = None,
    ):
        if _UDSONCAN_IMPORT_ERROR is not None:
            raise ImportError("udsoncan is required to use UdsoncanIsoTpConnection") from _UDSONCAN_IMPORT_ERROR
        BaseConnection.__init__(self, name=name)
        self._hw = hw
        self._tp = IsoTpEngine(
            req_id=req_id,
            resp_id=resp_id,
            func_id=func_id,
            is_fd=is_fd,
            cfg=cfg or TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0),
        )
        self._poll_interval_s = max(0.0, int(poll_interval_ms) / 1000.0)
        self._rx_payloads: Deque[bytes] = deque(maxlen=max(1, int(max_pending_responses)))
        self._pending_errors: Deque[int] = deque()
        self._close_hw_on_close = bool(close_hw_on_close)
        self._opened = False
        self._closed = False
        self._lock = Lock()

    def _rxfunc_for_isotp(self) -> Optional[CanMsg]:
        msg = self._hw.rxfn()
        if msg is None:
            return None
        return CanMsg(id=msg.id, data=msg.data, isfd=msg.isfd)

    def _pump_once_locked(self, ts_ms: Optional[int] = None) -> None:
        now = monotonic_ms() if ts_ms is None else int(ts_ms)
        step_once(tp=self._tp, rxfunc=self._rxfunc_for_isotp, txfunc=self._hw.txfn, ts_ms=now)
        while True:
            payload = self._tp.rx_uds_msg()
            if payload is None:
                break
            self._rx_payloads.append(bytes(payload))
        while True:
            err = self._tp.pop_error()
            if err is None:
                break
            self._pending_errors.append(int(err))

    def _raise_pending_error_locked(self) -> None:
        if self._pending_errors:
            raise IsoTpError(self._pending_errors.popleft())

    def open(self) -> "UdsoncanIsoTpConnection":
        if self._closed:
            raise RuntimeError("Connection is already closed")
        self._opened = True
        self.logger.info("Connection opened")
        return self

    def is_open(self) -> bool:
        return self._opened

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
            self._rx_payloads.clear()
            self._pending_errors.clear()
            self._tp.close()
        self._closed = True
        self._opened = False
        if self._close_hw_on_close and hasattr(self._hw, "close"):
            self._hw.close()  # type: ignore[func-returns-value]
        self.logger.info("Connection closed")

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    def specific_send(self, payload: bytes, timeout: Optional[float] = None) -> None:
        del timeout
        with self._lock:
            self._pump_once_locked()
            self._raise_pending_error_locked()
            self._tp.tx_uds_msg(bytes(payload), functional=False, ts_ms=monotonic_ms())
            # Push first TP frame(s) to CAN immediately.
            self._pump_once_locked()
            self._raise_pending_error_locked()

    def specific_wait_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        deadline = None if timeout is None else (time.monotonic() + max(0.0, float(timeout)))
        while True:
            with self._lock:
                self._pump_once_locked()
                self._raise_pending_error_locked()
                if self._rx_payloads:
                    return self._rx_payloads.popleft()

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutException("Did not receive IsoTP frame in time (timeout=%s sec)" % timeout)

            if self._poll_interval_s > 0:
                time.sleep(self._poll_interval_s)

    def empty_rxqueue(self) -> None:
        with self._lock:
            self._rx_payloads.clear()
            self._pending_errors.clear()
            # Best-effort flush of currently available stale frames.
            for _ in range(8):
                self._pump_once_locked()
                if not self._rx_payloads and not self._pending_errors:
                    break
                self._rx_payloads.clear()
                self._pending_errors.clear()
