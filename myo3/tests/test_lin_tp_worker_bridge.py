import unittest
from collections import deque
from typing import Optional


from isotp_engine.core.types import RawLinMsg
from isotp_engine.lin_device.worker import LinTpWorker


class _FakeLinMasterHw:
    def __init__(self, req_frame_id: int, resp_frame_id: int):
        self.req_frame_id = req_frame_id
        self.resp_frame_id = resp_frame_id
        self._scheduled_slave_rsp: deque[RawLinMsg] = deque()
        self._rx_q: deque[RawLinMsg] = deque()
        self.tx_nad_history: list[int] = []

    def txfn(self, frame_id: int, data: bytes) -> None:
        if frame_id != self.req_frame_id:
            return
        if len(data) != 8:
            return
        self.tx_nad_history.append(int(data[0]) & 0xFF)

        # Handle request single-frame: NAD + PCI(len) + UDS...
        if (data[1] & 0xF0) != 0x00:
            return
        uds_len = data[1] & 0x0F
        if uds_len < 3:
            return
        uds = data[2 : 2 + uds_len]
        if uds == bytes([0x22, 0xF1, 0x90]):
            nad = data[0]
            response = bytes([nad, 0x03, 0x62, 0xF1, 0x90, 0x00, 0x00, 0x00])
            self._scheduled_slave_rsp.append(RawLinMsg(id=self.resp_frame_id, data=response))

    def request_slave_response(self, frame_id: int) -> Optional[RawLinMsg]:
        if frame_id != self.resp_frame_id:
            return None
        if not self._scheduled_slave_rsp:
            return None
        msg = self._scheduled_slave_rsp.popleft()
        self._rx_q.append(msg)
        return msg

    def rxfn(self) -> Optional[RawLinMsg]:
        if not self._rx_q:
            return None
        return self._rx_q.popleft()


class LinTpWorkerBridgeTests(unittest.TestCase):
    def test_lin_tp_worker_roundtrip_single_frame(self):
        hw = _FakeLinMasterHw(req_frame_id=0x3C, resp_frame_id=0x3D)
        with LinTpWorker(
            hw=hw,
            req_frame_id=0x3C,
            resp_frame_id=0x3D,
            req_nad=0x10,
            func_nad=0x7F,
            tick_period_ms=1,
            bridge_sleep_ms=1,
        ) as dev:
            rsp = dev.uds_request(bytes([0x22, 0xF1, 0x90]), timeout_ms=1000)
            self.assertEqual(rsp, bytes([0x62, 0xF1, 0x90]))
            self.assertIsNone(dev.pop_error(timeout_s=0.0))

    def test_lin_tp_worker_can_override_req_nad_per_request(self):
        hw = _FakeLinMasterHw(req_frame_id=0x3C, resp_frame_id=0x3D)
        with LinTpWorker(
            hw=hw,
            req_frame_id=0x3C,
            resp_frame_id=0x3D,
            req_nad=0x10,
            func_nad=0x7F,
            tick_period_ms=1,
            bridge_sleep_ms=1,
        ) as dev:
            rsp1 = dev.uds_request(bytes([0x22, 0xF1, 0x90]), timeout_ms=1000, req_nad=0x10)
            rsp2 = dev.uds_request(bytes([0x22, 0xF1, 0x90]), timeout_ms=1000, req_nad=0x22)
            self.assertEqual(rsp1, bytes([0x62, 0xF1, 0x90]))
            self.assertEqual(rsp2, bytes([0x62, 0xF1, 0x90]))

        self.assertIn(0x10, hw.tx_nad_history)
        self.assertIn(0x22, hw.tx_nad_history)


if __name__ == "__main__":
    unittest.main(verbosity=2)
