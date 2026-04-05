import time

from isotp_engine_ctypes import IsoTpEngineWorker


def drain_tx(tp_worker: IsoTpEngineWorker, duration_s: float = 0.05):
    deadline = time.monotonic() + duration_s
    frames = []
    while time.monotonic() < deadline:
        item = tp_worker.pop_tx_can_frame(timeout_s=0.001)
        if item is None:
            continue
        frames.append(item)
    return frames


def main() -> None:
    with IsoTpEngineWorker(req_id=0x7E0, resp_id=0x7E8, func_id=0x7DF, is_fd=True, tick_period_ms=1) as tp:
        # 8-byte UDS payload on classic CAN -> multi-frame TX (FF + CF...)
        tp.tx_uds_msg(bytes([0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00, 0x00]), functional=False)

        first_batch = drain_tx(tp, duration_s=0.03)
        print("Before FC, TX frames:")
        for can_id, data, is_fd in first_batch:
            print(f"  id=0x{can_id:X}, is_fd={is_fd}, data={data.hex(' ')}")

        # ECU sends FC(CTS): now engine can continue with CF
        tp.on_can_frame(0x7E8, bytes([0x30, 0x00, 0x00,0,0,0,0,0]), is_fd=True)
        second_batch = drain_tx(tp, duration_s=0.03)
        print("After FC, TX frames:")
        for can_id, data, is_fd in second_batch:
            print(f"  id=0x{can_id:X}, is_fd={is_fd}, data={data.hex(' ')}")

        # Simulate ECU negative response pending: 7F 22 78
        tp.on_can_frame(0x7E8, bytes([0x03, 0x7F, 0x22, 0x78, 0x00, 0x00, 0x00, 0x00]), is_fd=True)
        time.sleep(0.01)

        # Then final positive response: 62 F1 90
        tp.on_can_frame(0x7E8, bytes([0x03, 0x62, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]), is_fd=True)
        uds = tp.wait_uds_final_response(overall_timeout_ms=2000, pending_gap_ms=500, poll_interval_ms=1)
        print("Final UDS RX:", uds.hex(" "))

        err = tp.pop_error(timeout_s=0.01)
        print("ERR:", err)


if __name__ == "__main__":
    main()
