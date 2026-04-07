from dataclasses import dataclass


@dataclass(frozen=True)
class RawCanMsg:
    id: int
    data: bytes
    isfd: bool
