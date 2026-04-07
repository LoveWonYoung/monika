from typing import Optional

from core.types import RawCanMsg

from .isotp_engine_ctypes import CanMsg, IsoTpEngine, TpConfig, send_uds_and_wait_final


def txfunc(can_id: int, data: bytes, is_fd: bool) -> None:
    """
    发送一帧到真实设备（请替换为你的硬件发送实现）。
    """
    raise NotImplementedError


def rxfunc() -> Optional[RawCanMsg]:
    """
    从真实设备非阻塞读取一帧。
    无数据时返回 None。
    """
    raise NotImplementedError


def rxfunc_for_isotp() -> Optional[CanMsg]:
    msg = rxfunc()
    if msg is None:
        return None
    return CanMsg(id=msg.id, data=msg.data, isfd=msg.isfd)


def main() -> None:
    with IsoTpEngine(
        req_id=0x7E0,
        resp_id=0x7E8,
        func_id=0x7DF,
        is_fd=False,
        cfg=TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0),
    ) as tp:
        req = bytes([0x22, 0xF1, 0x90])
        rsp = send_uds_and_wait_final(
            tp=tp,
            payload=req,
            rxfunc=rxfunc_for_isotp,
            txfunc=txfunc,
            functional=False,
            overall_timeout_ms=10000,
            pending_gap_ms=3000,
            poll_interval_ms=1,
        )
        print("final response:", rsp.hex(" "))


if __name__ == "__main__":
    main()
