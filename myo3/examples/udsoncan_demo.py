from isotp_engine.can_device import Toomoss, UdsoncanIsoTpConnection

import udsoncan.configs
from udsoncan.client import Client


def main() -> None:
    if Toomoss is None:
        raise RuntimeError("Toomoss CAN backend is unavailable on this platform/runtime")
    if UdsoncanIsoTpConnection is None:
        raise RuntimeError("UdsoncanIsoTpConnection is unavailable. Please install udsoncan first.")

    cfg = udsoncan.configs.default_client_config.copy()
    cfg["request_timeout"] = 5.0

    with Toomoss() as hw:
        conn = UdsoncanIsoTpConnection(
            hw=hw,
            req_id=0x5B1,
            resp_id=0x5B9,
            func_id=0x7DF,
            is_fd=True,
            name="monika-rust-isotp",
        )
        with Client(conn, config=cfg) as client:
            rsp = client.read_data_by_identifier(0xF194)
            print(rsp)


if __name__ == "__main__":
    main()
