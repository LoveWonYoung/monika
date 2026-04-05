import argparse
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from isotp_engine_ctypes import IsoTpEngine, TpConfig


@dataclass
class SimStats:
    cases: int = 0
    req_bytes: int = 0
    resp_bytes: int = 0
    tester_tx_frames: int = 0
    ecu_tx_frames: int = 0
    max_case_sim_ms: int = 0


def random_bytes(rng: random.Random, n: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(n))


def make_response_payload(req: bytes, rsp_len: int) -> bytes:
    if rsp_len <= 0:
        raise ValueError("rsp_len must be > 0")
    if rsp_len == 1:
        return bytes([0x62])
    if len(req) >= 2:
        head = bytes([0x62, req[1]])
    elif len(req) == 1:
        head = bytes([0x62, req[0]])
    else:
        head = bytes([0x62, 0x00])
    tail_len = max(0, rsp_len - len(head))
    tail = bytes((i & 0xFF) for i in range(tail_len))
    return (head + tail)[:rsp_len]


def drain_tx(tp: IsoTpEngine) -> List[Tuple[int, bytes, bool]]:
    return tp.pop_all_tx_can_frames()


def pump_case(
    tester: IsoTpEngine,
    ecu: IsoTpEngine,
    req_payload: bytes,
    rsp_payload: bytes,
    start_ts_ms: int,
    max_case_sim_ms: int,
) -> Tuple[int, int, int]:
    ts = start_ts_ms
    case_deadline = ts + max_case_sim_ms
    ecu_sent_response = False

    tester.tx_uds_msg(req_payload, functional=False, ts_ms=ts)

    tester_frames = 0
    ecu_frames = 0

    while ts <= case_deadline:
        # Host loop for tester side: feed RX -> tick -> drain TX
        for can_id, data, is_fd in drain_tx(tester):
            if len(data) < 8:
                raise AssertionError(f"tester TX frame shorter than 8 bytes: {data.hex(' ')}")
            tester_frames += 1
            ecu.on_can_frame(can_id, data, is_fd, ts_ms=ts)

        # Host loop for ecu side: feed RX -> tick -> drain TX
        for can_id, data, is_fd in drain_tx(ecu):
            if len(data) < 8:
                raise AssertionError(f"ecu TX frame shorter than 8 bytes: {data.hex(' ')}")
            ecu_frames += 1
            tester.on_can_frame(can_id, data, is_fd, ts_ms=ts)

        # ECU application consumes completed request and sends response payload once.
        if not ecu_sent_response:
            ecu_req = ecu.rx_uds_msg()
            if ecu_req is not None:
                if ecu_req != req_payload:
                    raise AssertionError(
                        f"ECU got wrong request len={len(ecu_req)} expected={len(req_payload)}"
                    )
                ecu.tx_uds_msg(rsp_payload, functional=False, ts_ms=ts)
                ecu_sent_response = True

        tester.tick(ts_ms=ts)
        ecu.tick(ts_ms=ts)

        tester_err = tester.pop_error()
        if tester_err is not None:
            raise RuntimeError(f"tester TP error: {tester_err}")
        ecu_err = ecu.pop_error()
        if ecu_err is not None:
            raise RuntimeError(f"ecu TP error: {ecu_err}")

        got_rsp = tester.rx_uds_msg()
        if got_rsp is not None:
            if got_rsp != rsp_payload:
                raise AssertionError(
                    f"tester got wrong response len={len(got_rsp)} expected={len(rsp_payload)}"
                )
            return ts, tester_frames, ecu_frames

        ts += 1

    raise TimeoutError(
        f"case timeout: req_len={len(req_payload)} rsp_len={len(rsp_payload)} max={max_case_sim_ms}ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ISO-TP dual-end stress simulation")
    parser.add_argument("--cases", type=int, default=30000, help="number of request/response cases")
    parser.add_argument("--seed", type=int, default=20260404, help="random seed")
    parser.add_argument("--max-req-len", type=int, default=2048, help="max request payload length")
    parser.add_argument("--max-rsp-len", type=int, default=2048, help="max response payload length")
    parser.add_argument(
        "--max-case-sim-ms",
        type=int,
        default=20000,
        help="max simulated ms per case before timeout",
    )
    args = parser.parse_args()

    if args.cases <= 0:
        raise ValueError("--cases must be > 0")
    if args.max_req_len <= 0:
        raise ValueError("--max-req-len must be > 0")
    if args.max_rsp_len <= 0:
        raise ValueError("--max-rsp-len must be > 0")

    req_id = 0x7E0
    resp_id = 0x7E8
    func_id = 0x7DF

    # Use zero STmin for speed in stress simulation.
    cfg = TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0)

    rng = random.Random(args.seed)
    stats = SimStats()
    ts = 0
    wall_start = time.time()

    with IsoTpEngine(req_id=req_id, resp_id=resp_id, func_id=func_id, is_fd=False, cfg=cfg) as tester, IsoTpEngine(
        req_id=resp_id, resp_id=req_id, func_id=func_id, is_fd=False, cfg=cfg
    ) as ecu:
        for i in range(args.cases):
            req_len = rng.randint(1, args.max_req_len)
            rsp_len = rng.randint(1, args.max_rsp_len)
            req_payload = random_bytes(rng, req_len)
            rsp_payload = make_response_payload(req_payload, rsp_len)

            case_start = ts
            ts, tester_frames, ecu_frames = pump_case(
                tester=tester,
                ecu=ecu,
                req_payload=req_payload,
                rsp_payload=rsp_payload,
                start_ts_ms=ts,
                max_case_sim_ms=args.max_case_sim_ms,
            )

            stats.cases += 1
            stats.req_bytes += req_len
            stats.resp_bytes += rsp_len
            stats.tester_tx_frames += tester_frames
            stats.ecu_tx_frames += ecu_frames
            stats.max_case_sim_ms = max(stats.max_case_sim_ms, ts - case_start)
            ts += 1

            if (i + 1) % 50 == 0 or i + 1 == args.cases:
                print(
                    f"[progress] {i + 1}/{args.cases} "
                    f"req_bytes={stats.req_bytes} resp_bytes={stats.resp_bytes} "
                    f"tester_frames={stats.tester_tx_frames} ecu_frames={stats.ecu_tx_frames}"
                )

    wall_elapsed = time.time() - wall_start
    print("\n=== Stress Result ===")
    print(f"cases: {stats.cases}")
    print(f"request bytes: {stats.req_bytes}")
    print(f"response bytes: {stats.resp_bytes}")
    print(f"tester tx frames: {stats.tester_tx_frames}")
    print(f"ecu tx frames: {stats.ecu_tx_frames}")
    print(f"max case simulated duration: {stats.max_case_sim_ms} ms")
    print(f"wall time: {wall_elapsed:.3f} s")
    print("status: PASS")


if __name__ == "__main__":
    main()
