from abc import ABC, abstractmethod
from typing import Optional

from ..common.types import RawCanMsg


class CanDeviceInterface(ABC):
    @abstractmethod
    def rxfn(self) -> Optional[RawCanMsg]:
        raise NotImplementedError

    @abstractmethod
    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        raise NotImplementedError
