from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from .types import CanMsg, IsoTpError, LinMsg, UdsNegativeResponseError

if TYPE_CHECKING:
    from .engine import IsoTpEngine, LinTpEngine


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def is_uds_response_pending(payload: bytes) -> bool:
    return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78


def parse_uds_negative_response(payload: bytes) -> tuple[int, int, int]:
    if len(payload) < 3 or payload[0] != 0x7F:
        raise ValueError("not a UDS negative response")
    return payload[0], payload[1], payload[2]


def build_uds_default_matcher(request: bytes) -> Callable[[bytes], bool]:
    req = bytes(request)
    sid = req[0] if req else None

    def matcher(response: bytes) -> bool:
        rsp = bytes(response)
        if not rsp:
            return False
        if is_uds_response_pending(rsp):
            return sid is not None and rsp[1] == sid
        if sid is None:
            return True
        if rsp[0] == 0x7F:
            return len(rsp) >= 2 and rsp[1] == sid
        if rsp[0] != ((sid + 0x40) & 0xFF):
            return False

        if sid in (0x22, 0x2E, 0x2F) and len(req) >= 3 and len(rsp) >= 3:
            return rsp[1:3] == req[1:3]

        if sid == 0x31 and len(req) >= 4 and len(rsp) >= 4:
            return rsp[1:4] == req[1:4]

        if sid in (0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x85, 0x87) and len(req) >= 2 and len(rsp) >= 2:
            return (rsp[1] & 0x7F) == (req[1] & 0x7F)

        return True

    return matcher


def step_once(tp: IsoTpEngine, rxfunc: Callable[[], Optional[CanMsg]], txfunc: Callable[[int, bytes, bool], None], ts_ms: Optional[int] = None) -> None:
    now = monotonic_ms() if ts_ms is None else int(ts_ms)
    while True:
        msg = rxfunc()
        if msg is None:
            break
        tp.on_can_frame(msg.id, msg.data, msg.isfd, ts_ms=now)
    tp.tick(ts_ms=now)
    while True:
        out = tp.pop_tx_can_frame()
        if out is None:
            break
        txfunc(*out)


def step_once_lin(tp: LinTpEngine, rxfunc: Callable[[], Optional[LinMsg]], txfunc: Callable[[int, bytes], None], ts_ms: Optional[int] = None) -> None:
    now = monotonic_ms() if ts_ms is None else int(ts_ms)
    while True:
        msg = rxfunc()
        if msg is None:
            break
        tp.on_lin_frame(msg.id, msg.data, ts_ms=now)
    tp.tick(ts_ms=now)
    while True:
        out = tp.pop_tx_lin_frame()
        if out is None:
            break
        txfunc(*out)


def send_uds_and_wait_final(
    tp: IsoTpEngine,
    payload: bytes,
    rxfunc: Callable[[], Optional[CanMsg]],
    txfunc: Callable[[int, bytes, bool], None],
    functional: bool = False,
    overall_timeout_ms: int = 10000,
    pending_gap_ms: int = 3000,
    poll_interval_ms: int = 1,
    response_matcher: Optional[Callable[[bytes], bool]] = None,
) -> bytes:
    matcher = response_matcher or build_uds_default_matcher(payload)
    deadline = monotonic_ms() + int(overall_timeout_ms)
    next_deadline = deadline
    tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
    while True:
        now = monotonic_ms()
        if now > next_deadline:
            raise IsoTpError(-106)
        step_once(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=now)
        while True:
            msg = tp.rx_uds_msg()
            if msg is None:
                break
            if not matcher(msg):
                tp._pending_uds.append(msg)
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                break
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
        if poll_interval_ms > 0:
            time.sleep(poll_interval_ms / 1000.0)


def send_uds_and_wait_final_lin(
    tp: LinTpEngine,
    payload: bytes,
    rxfunc: Callable[[], Optional[LinMsg]],
    txfunc: Callable[[int, bytes], None],
    functional: bool = False,
    overall_timeout_ms: int = 10000,
    pending_gap_ms: int = 3000,
    poll_interval_ms: int = 1,
    response_matcher: Optional[Callable[[bytes], bool]] = None,
) -> bytes:
    matcher = response_matcher or build_uds_default_matcher(payload)
    deadline = monotonic_ms() + int(overall_timeout_ms)
    next_deadline = deadline
    tp.tx_uds_msg(payload, functional=functional, ts_ms=monotonic_ms())
    while True:
        now = monotonic_ms()
        if now > next_deadline:
            raise IsoTpError(-106)
        step_once_lin(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=now)
        while True:
            msg = tp.rx_uds_msg()
            if msg is None:
                break
            if not matcher(msg):
                tp._pending_uds.append(msg)
                continue
            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + int(pending_gap_ms))
                break
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg
        if poll_interval_ms > 0:
            time.sleep(poll_interval_ms / 1000.0)
