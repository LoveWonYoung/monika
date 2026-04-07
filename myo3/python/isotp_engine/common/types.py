from dataclasses import dataclass


@dataclass(frozen=True)
class RawCanMsg:
    id: int
    data: bytes
    isfd: bool


@dataclass(frozen=True)
class RawLinMsg:
    id: int
    data: bytes
