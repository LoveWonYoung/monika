from abc import ABC, abstractmethod
from typing import Optional
from core.types import RawCanMsg


class MyHwDeviceInterface(ABC):
    @abstractmethod
    def rxfn(self) -> Optional[RawCanMsg]:
        pass

    @abstractmethod
    def txfn(self, can_id: int, data: bytes, is_fd: bool) -> None:
        pass


# Backward compatibility for existing code.
MyHwDeviceInerface = MyHwDeviceInterface