from __future__ import annotations

from typing import Optional, Protocol

from ..common.types import RawLinMsg


class LinMasterDeviceInterface(Protocol):
    def request_slave_response(self, frame_id: int):
        ...

    def rxfn(self) -> Optional[RawLinMsg]:
        ...

    def txfn(self, frame_id: int, data: bytes) -> None:
        ...
