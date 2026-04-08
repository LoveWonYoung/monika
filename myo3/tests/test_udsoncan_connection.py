import unittest
from collections import deque
from typing import Optional

from isotp_engine.common.types import RawCanMsg

try:
    import udsoncan.configs
    from udsoncan.client import Client
except Exception:
    _HAS_UDSONCAN = False
else:
    _HAS_UDSONCAN = True

from isotp_engine.can_device.udsoncan_connection import UdsoncanIsoTpConnection


REQ_ID = 0x7E0
RESP_ID = 0x7E8
FUNC_ID = 0x7DF


class _FakeCanHw:
    def __init__(self):
        self._rx_q: deque[RawCanMsg] = deque()

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        if can_id != REQ_ID:
            return
        if len(data) < 4:
            return
        if (data[0] & 0xF0) != 0x00:
            return

        uds_len = data[0] & 0x0F
        uds_req = bytes(data[1 : 1 + uds_len])

        if uds_req == bytes([0x11, 0x01]):
            pending = bytes([0x03, 0x7F, 0x11, 0x78, 0x00, 0x00, 0x00, 0x00])
            final = bytes([0x02, 0x51, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
            self._rx_q.append(RawCanMsg(id=RESP_ID, data=pending, isfd=is_fd))
            self._rx_q.append(RawCanMsg(id=RESP_ID, data=final, isfd=is_fd))

    def rxfn(self) -> Optional[RawCanMsg]:
        if not self._rx_q:
            return None
        return self._rx_q.popleft()


@unittest.skipUnless(_HAS_UDSONCAN, "udsoncan is not installed")
class UdsoncanIsoTpConnectionTests(unittest.TestCase):
    def test_wait_frame_exposes_pending_then_final(self):
        hw = _FakeCanHw()
        conn = UdsoncanIsoTpConnection(hw=hw, req_id=REQ_ID, resp_id=RESP_ID, func_id=FUNC_ID, is_fd=False)
        with conn.open():
            conn.send(bytes([0x11, 0x01]))
            pending = conn.wait_frame(timeout=0.5, exception=True)
            final = conn.wait_frame(timeout=0.5, exception=True)
            self.assertEqual(pending, bytes([0x7F, 0x11, 0x78]))
            self.assertEqual(final, bytes([0x51, 0x01]))

    def test_udsoncan_client_can_complete_request(self):
        hw = _FakeCanHw()
        conn = UdsoncanIsoTpConnection(hw=hw, req_id=REQ_ID, resp_id=RESP_ID, func_id=FUNC_ID, is_fd=False)

        cfg = udsoncan.configs.default_client_config.copy()
        cfg["request_timeout"] = 1.0
        cfg["p2_timeout"] = 0.2
        cfg["p2_star_timeout"] = 0.6

        with Client(conn, config=cfg) as client:
            rsp = client.ecu_reset(1)
            self.assertEqual(rsp.service_data.reset_type_echo, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
