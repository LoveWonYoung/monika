from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from .types import CanMsg, IsoTpError, LinMsg, UdsNegativeResponseError

if TYPE_CHECKING:
    from .engine import IsoTpEngine, LinTpEngine


def monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def is_uds_response_pending(payload: bytes) -> bool:
    return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78


def parse_uds_negative_response(payload: bytes) -> tuple[int, int, int]:
    if len(payload) < 3 or payload[0] != 0x7F:
        raise ValueError("not a UDS negative response")
    return payload[0], payload[1], payload[2]


def build_uds_default_matcher(request: bytes) -> Callable[[bytes], bool]:
    req = request if isinstance(request, bytes) else bytes(request)
    if not req:
        raise ValueError("request payload must not be empty")
    sid = req[0]
    pos_sid = (sid + 0x40) & 0xFF
    has_did = sid in (0x22, 0x2E, 0x2F) and len(req) >= 3
    did = req[1:3] if has_did else b""
    has_routine = sid == 0x31 and len(req) >= 4
    routine = req[1:4] if has_routine else b""
    has_subfunc = sid in (0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x85, 0x87) and len(req) >= 2
    subfunc = req[1] & 0x7F if has_subfunc else 0

    def matcher(response: bytes) -> bool:
        rsp = response if isinstance(response, bytes) else bytes(response)
        if not rsp:
            return False
        if is_uds_response_pending(rsp):
            return rsp[1] == sid if len(rsp) >= 2 else False
        if rsp[0] == 0x7F:
            return len(rsp) >= 2 and rsp[1] == sid
        if rsp[0] != pos_sid:
            return False

        if has_did and len(rsp) >= 3:
            return rsp[1:3] == did

        if has_routine and len(rsp) >= 4:
            return rsp[1:4] == routine

        if has_subfunc and len(rsp) >= 2:
            return (rsp[1] & 0x7F) == subfunc

        return True

    return matcher


def step_once(tp: IsoTpEngine, rxfunc: Callable[[], Optional[CanMsg]], txfunc: Callable[[int, bytes, bool], None], ts_ms: Optional[int] = None) -> None:
    now = monotonic_ms() if ts_ms is None else int(ts_ms)
    rx_batch: list[tuple[int, bytes, bool]] = []
    while True:
        msg = rxfunc()
        if msg is None:
            break
        rx_batch.append((msg.id, msg.data, msg.isfd))
    if rx_batch:
        tp.on_can_frames(rx_batch, ts_ms=now)
    tp.tick(ts_ms=now)
    while True:
        out_batch = tp.pop_tx_can_frames(max_frames=128)
        if not out_batch:
            break
        for out in out_batch:
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


def _drain_rx_uds_to_pending(rx_uds_msg: Callable[[], Optional[bytes]], stash_pending_uds: Callable[[bytes], None]) -> None:
    while True:
        msg = rx_uds_msg()
        if msg is None:
            return
        stash_pending_uds(msg)


def _send_uds_and_wait_final_core(
    *,
    payload: bytes,
    tx_uds_msg: Callable[[bytes, bool, int], None],
    step_fn: Callable[[int], None],
    rx_uds_msg: Callable[[], Optional[bytes]],
    stash_pending_uds: Callable[[bytes], None],
    functional: bool,
    overall_timeout_ms: int,
    pending_gap_ms: int,
    poll_interval_ms: int,
    response_matcher: Optional[Callable[[bytes], bool]],
) -> bytes:
    req_payload = payload if isinstance(payload, bytes) else bytes(payload)
    matcher = response_matcher or build_uds_default_matcher(req_payload)
    now = monotonic_ms()
    deadline = now + int(overall_timeout_ms)
    next_deadline = deadline
    pending_gap_ms = int(pending_gap_ms)
    poll_interval_ms = max(0, int(poll_interval_ms))

    tx_uds_msg(req_payload, functional, now)

    while True:
        now = monotonic_ms()
        if now > next_deadline:
            raise IsoTpError(-106)

        step_fn(now)

        while True:
            msg = rx_uds_msg()
            if msg is None:
                break

            if not matcher(msg):
                stash_pending_uds(msg)
                continue

            # Preserve message ordering for subsequent requests.
            _drain_rx_uds_to_pending(rx_uds_msg, stash_pending_uds)

            if is_uds_response_pending(msg):
                next_deadline = min(deadline, monotonic_ms() + pending_gap_ms)
                break
            if msg[:1] == b"\x7f":
                raise UdsNegativeResponseError(msg)
            return msg

        if poll_interval_ms > 0:
            sleep_ms = min(poll_interval_ms, max(0, next_deadline - monotonic_ms()))
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)


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
    return _send_uds_and_wait_final_core(
        payload=payload,
        tx_uds_msg=lambda req_payload, is_functional, ts_ms: tp.tx_uds_msg(req_payload, functional=is_functional, ts_ms=ts_ms),
        step_fn=lambda ts_ms: step_once(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=ts_ms),
        rx_uds_msg=tp.rx_uds_msg,
        stash_pending_uds=tp.stash_pending_uds,
        functional=functional,
        overall_timeout_ms=overall_timeout_ms,
        pending_gap_ms=pending_gap_ms,
        poll_interval_ms=poll_interval_ms,
        response_matcher=response_matcher,
    )


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
    return _send_uds_and_wait_final_core(
        payload=payload,
        tx_uds_msg=lambda req_payload, is_functional, ts_ms: tp.tx_uds_msg(req_payload, functional=is_functional, ts_ms=ts_ms),
        step_fn=lambda ts_ms: step_once_lin(tp=tp, rxfunc=rxfunc, txfunc=txfunc, ts_ms=ts_ms),
        rx_uds_msg=tp.rx_uds_msg,
        stash_pending_uds=tp.stash_pending_uds,
        functional=functional,
        overall_timeout_ms=overall_timeout_ms,
        pending_gap_ms=pending_gap_ms,
        poll_interval_ms=poll_interval_ms,
        response_matcher=response_matcher,
    )
