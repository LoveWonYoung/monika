from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from ..common.types import RawCanMsg


@dataclass
class FakeEcu:
    """Demo-only fake hardware endpoint."""

    req_id: int = 0x7E0
    resp_id: int = 0x7E8
    is_fd: bool = False

    def __post_init__(self) -> None:
        self._rx_q: Deque[RawCanMsg] = deque()

    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        if can_id != self.req_id:
            return
        if len(data) < 4:
            return
        if (data[0] & 0xF0) != 0x00:
            return

        uds_req = data[1 : 1 + (data[0] & 0x0F)]
        if uds_req == bytes([0x22, 0xF1, 0x90]):
            rsp_payload = bytes([0x62, 0xF1, 0x90, 0x12, 0x34])
            sf = bytes([len(rsp_payload)]) + rsp_payload
            sf = sf + bytes(max(0, 8 - len(sf)))
            self._rx_q.append(RawCanMsg(id=self.resp_id, data=sf, isfd=is_fd or self.is_fd))

    def rxfn(self) -> Optional[RawCanMsg]:
        if not self._rx_q:
            return None
        return self._rx_q.popleft()


__all__ = ["FakeEcu"]
