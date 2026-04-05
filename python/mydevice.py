from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Deque, Optional

from isotp_engine_ctypes import (
    CanMsg,
    IsoTpEngine,
    IsoTpEngineWorker,
    TpConfig,
    send_uds_and_wait_final,
)
from real_device_template import RawCanMsg


class MyHwDevice:
    """Hardware adapter interface to be implemented by your real device driver."""

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        """Send one CAN/CAN-FD frame to hardware."""
        raise NotImplementedError

    def rxfn(self) -> Optional[RawCanMsg]:
        """Non-blocking receive: return one frame, or None when no frame."""
        raise NotImplementedError


class MyHwDeviceWithTpEngine:
    """Compose a hardware adapter with ISO-TP engine and expose UDS request API."""

    def __init__(
        self,
        hw: MyHwDevice,
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
            cfg=cfg or TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0),
        )

    def close(self) -> None:
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
        """
        Send one UDS request and wait for final UDS response payload.
        """
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

    def pop_error(self) -> Optional[int]:
        return self._tp.pop_error()


class MyHwDeviceWithTpWorker:
    """
    Compose a hardware adapter with IsoTpEngineWorker.
    A bridge thread pumps HW RX -> worker and worker TX -> HW TX.
    """

    def __init__(
        self,
        hw: MyHwDevice,
        req_id: int = 0x7E0,
        resp_id: int = 0x7E8,
        func_id: int = 0x7DF,
        is_fd: bool = False,
        cfg: Optional[TpConfig] = None,
        tick_period_ms: int = 1,
        bridge_sleep_ms: int = 1,
    ):
        self._hw = hw
        self._worker = IsoTpEngineWorker(
            req_id=req_id,
            resp_id=resp_id,
            func_id=func_id,
            is_fd=is_fd,
            cfg=cfg or TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0),
            tick_period_ms=tick_period_ms,
        )
        self._bridge_sleep_s = max(0.0, bridge_sleep_ms / 1000.0)
        self._stop_evt = threading.Event()
        self._bridge_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._worker.start()
        if self._bridge_thread is not None and self._bridge_thread.is_alive():
            return
        self._stop_evt.clear()
        self._bridge_thread = threading.Thread(target=self._bridge_loop, name="HwTpBridge", daemon=True)
        self._bridge_thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
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
        while not self._stop_evt.is_set():
            has_work = False

            while True:
                msg = self._hw.rxfn()
                if msg is None:
                    break
                has_work = True
                self._worker.on_can_frame(msg.id, msg.data, msg.isfd)

            while True:
                out = self._worker.pop_tx_can_frame(timeout_s=0.0)
                if out is None:
                    break
                has_work = True
                can_id, data, is_fd = out
                self._hw.txfn(can_id, data, is_fd)

            if not has_work and self._bridge_sleep_s > 0:
                time.sleep(self._bridge_sleep_s)

    def uds_request(self, payload: bytes, functional: bool = False, timeout_ms: int = 10000) -> bytes:
        return self._worker.tx_uds_msg(
            payload=payload,
            functional=functional,
            response_timeout_ms=timeout_ms,
            pending_gap_ms=3000,
            poll_interval_ms=1,
        )

    def pop_error(self, timeout_s: float = 0.0) -> Optional[int]:
        return self._worker.pop_error(timeout_s=timeout_s)


@dataclass
class _FakeEcu:
    """
    Demo-only fake hardware:
    - receives tester TX frames from txfn
    - pushes one canned ECU response frame to rx queue
    """

    req_id: int = 0x7E0
    resp_id: int = 0x7E8
    is_fd: bool = False

    def __post_init__(self) -> None:
        self._rx_q: Deque[RawCanMsg] = deque()

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        # Tester sent SF request: 03 22 F1 90 00 00 00 00
        if can_id != self.req_id:
            return
        if len(data) < 4:
            return
        if (data[0] & 0xF0) != 0x00:
            return

        uds_req = data[1 : 1 + (data[0] & 0x0F)]
        if uds_req == bytes([0x22, 0xF1, 0x90]):
            # ECU final positive response: 62 F1 90 12 34
            rsp_payload = bytes([0x62, 0xF1, 0x90, 0x12, 0x34])
            sf = bytes([len(rsp_payload)]) + rsp_payload
            sf = sf + bytes(max(0, 8 - len(sf)))
            self._rx_q.append(RawCanMsg(id=self.resp_id, data=sf, isfd=is_fd or self.is_fd))

    def rxfn(self) -> Optional[RawCanMsg]:
        if not self._rx_q:
            return None
        return self._rx_q.popleft()


def demo() -> None:
    hw = _FakeEcu()
    with MyHwDeviceWithTpEngine(hw=hw, req_id=0x7E0, resp_id=0x7E8, func_id=0x7DF, is_fd=False) as dev:
        req = bytes([0x22, 0xF1, 0x90])
        rsp = dev.uds_request(req)
        print("request:", req.hex(" "))
        print("response:", rsp.hex(" "))


def demo_worker() -> None:
    hw = _FakeEcu()
    with MyHwDeviceWithTpWorker(hw=hw, req_id=0x7E0, resp_id=0x7E8, func_id=0x7DF, is_fd=False) as dev:
        req = bytes([0x22, 0xF1, 0x90])
        rsp = dev.uds_request(req)
        print("worker request:", req.hex(" "))
        print("worker response:", rsp.hex(" "))


if __name__ == "__main__":
    demo()
    demo_worker()
