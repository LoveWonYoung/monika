from .fakes import FakeEcu
from .toomoss import Toomoss
from .tp_clients import MyHwDeviceWithTpEngine, TpWorker


def demo() -> None:
    with Toomoss() as hw:
        with MyHwDeviceWithTpEngine(hw=hw, req_id=0x7E0, resp_id=0x7E8, func_id=0x7DF, is_fd=False) as dev:
            req = bytes([0x22, 0xF1, 0x90])
            rsp = dev.uds_request(req)
            print("request:", req.hex(" "))
            print("response:", rsp.hex(" "))


def demo_worker() -> None:
    with Toomoss() as hw:
        for _ in range(50):
            hw.txfn(0x482, bytes([0] * 8), True)
        with TpWorker(hw=hw, req_id=0x5B1, resp_id=0x5B9, func_id=0x7DF, is_fd=True) as dev:
            req = bytes([0x19, 0x02, 0x09])
            rsp = dev.uds_request(req)
            print("worker request:", req.hex(" "))
            print("worker response:", rsp.hex(" "))


# Backward compatibility aliases.
_FakeEcu = FakeEcu


__all__ = [
    "Toomoss",
    "MyHwDeviceWithTpEngine",
    "TpWorker",
    "FakeEcu",
    "_FakeEcu",
    "demo",
    "demo_worker",
]


if __name__ == "__main__":
    demo_worker()
