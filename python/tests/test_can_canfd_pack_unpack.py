import unittest
from pathlib import Path
import sys

# Ensure `python/` is on sys.path when running from repo root.
THIS_FILE = Path(__file__).resolve()
PYTHON_DIR = THIS_FILE.parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from isotp_engine_ctypes import IsoTpEngine, IsoTpEngineWorker, TpConfig, build_uds_default_matcher


REQ_ID = 0x7E0
RESP_ID = 0x7E8
FUNC_ID = 0x7DF


def _cfg_fast() -> TpConfig:
    # Keep STmin at 0 so MF tests finish in a few simulated ticks.
    return TpConfig(n_bs_ms=1000, n_cr_ms=1000, stmin_ms=0, block_size=0)


class CanCanFdPackUnpackTests(unittest.TestCase):
    def _assert_no_err(self, tp: IsoTpEngine, tag: str) -> None:
        err = tp.pop_error()
        self.assertIsNone(err, f"{tag} pop_error returned: {err}")

    def _pump_and_feed(self, src: IsoTpEngine, dst: IsoTpEngine, ts_ms: int):
        frames = src.pop_all_tx_can_frames()
        for can_id, data, is_fd in frames:
            # Rust side now guarantees min frame len 8.
            self.assertGreaterEqual(len(data), 8)
            dst.on_can_frame(can_id, data, is_fd, ts_ms=ts_ms)
        return frames

    def test_can_single_frame_pack(self):
        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()) as tp:
            payload = bytes([0x22, 0xF1, 0x90])
            tp.tx_uds_msg(payload, functional=False, ts_ms=0)

            frames = tp.pop_all_tx_can_frames()
            self.assertEqual(len(frames), 1)
            can_id, data, is_fd = frames[0]
            self.assertEqual(can_id, REQ_ID)
            self.assertFalse(is_fd)
            self.assertEqual(len(data), 8)
            self.assertEqual(data[:4], bytes([0x03, 0x22, 0xF1, 0x90]))
            self.assertEqual(data[4:], bytes([0x00, 0x00, 0x00, 0x00]))
            self._assert_no_err(tp, "can_sf_pack")

    def test_can_single_frame_unpack(self):
        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()) as tp:
            tp.on_can_frame(
                RESP_ID,
                bytes([0x03, 0x62, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]),
                is_fd=False,
                ts_ms=0,
            )
            got = tp.rx_uds_msg()
            self.assertEqual(got, bytes([0x62, 0xF1, 0x90]))
            self._assert_no_err(tp, "can_sf_unpack")

    def test_can_batch_on_can_frames_unpack(self):
        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()) as tp:
            processed = tp.on_can_frames(
                [
                    (RESP_ID, bytes([0x03, 0x62, 0xF1, 0x90, 0, 0, 0, 0]), False),
                    (RESP_ID, bytes([0x03, 0x62, 0xF1, 0x91, 0, 0, 0, 0]), False),
                ],
                ts_ms=0,
            )
            self.assertEqual(processed, 2)
            self.assertEqual(tp.rx_uds_msg(), bytes([0x62, 0xF1, 0x90]))
            self.assertEqual(tp.rx_uds_msg(), bytes([0x62, 0xF1, 0x91]))
            self._assert_no_err(tp, "can_batch_unpack")

    def test_can_batch_pop_tx_can_frames(self):
        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()) as tp:
            tp.tx_uds_msg(bytes([0x22, 0xF1, 0x90]), functional=False, ts_ms=0)
            tp.tx_uds_msg(bytes([0x19, 0x02]), functional=False, ts_ms=0)

            frames = tp.pop_tx_can_frames(max_frames=8, buf_cap=64)
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0][0], REQ_ID)
            self.assertEqual(frames[1][0], REQ_ID)
            self.assertGreaterEqual(len(frames[0][1]), 8)
            self.assertGreaterEqual(len(frames[1][1]), 8)
            self._assert_no_err(tp, "can_batch_pop_tx")

    def test_can_multi_frame_pack_unpack(self):
        req_payload = bytes((i & 0xFF) for i in range(40))

        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()) as tester, IsoTpEngine(
            RESP_ID, REQ_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast()
        ) as ecu:
            tester.tx_uds_msg(req_payload, functional=False, ts_ms=0)

            tester_sent = []
            ecu_sent = []
            ecu_got = None

            for ts in range(200):
                tester_sent.extend(self._pump_and_feed(tester, ecu, ts))
                ecu_sent.extend(self._pump_and_feed(ecu, tester, ts))

                tester.tick(ts_ms=ts)
                ecu.tick(ts_ms=ts)
                self._assert_no_err(tester, "can_mf_tester")
                self._assert_no_err(ecu, "can_mf_ecu")

                ecu_got = ecu.rx_uds_msg()
                if ecu_got is not None:
                    break

            self.assertEqual(ecu_got, req_payload)
            self.assertTrue(any((data[0] & 0xF0) == 0x10 for _, data, _ in tester_sent))
            self.assertTrue(any((data[0] & 0xF0) == 0x20 for _, data, _ in tester_sent))
            self.assertTrue(any((data[0] & 0xF0) == 0x30 for _, data, _ in ecu_sent))

    def test_canfd_single_frame_pack_unpack(self):
        payload = bytes((i & 0xFF) for i in range(20))

        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=True, cfg=_cfg_fast()) as tp:
            tp.tx_uds_msg(payload, functional=False, ts_ms=0)
            frames = tp.pop_all_tx_can_frames()
            self.assertEqual(len(frames), 1)

            can_id, data, is_fd = frames[0]
            self.assertEqual(can_id, REQ_ID)
            self.assertTrue(is_fd)
            self.assertGreaterEqual(len(data), 8)
            self.assertEqual(data[0], 0x00)  # SF escaped length format
            self.assertEqual(data[1], len(payload))
            self.assertEqual(data[2 : 2 + len(payload)], payload)
            self._assert_no_err(tp, "canfd_sf_pack")

            tp.on_can_frame(RESP_ID, data, is_fd=True, ts_ms=1)
            got = tp.rx_uds_msg()
            self.assertEqual(got, payload)
            self._assert_no_err(tp, "canfd_sf_unpack")

    def test_canfd_multi_frame_pack_unpack(self):
        req_payload = bytes((i & 0xFF) for i in range(180))

        with IsoTpEngine(REQ_ID, RESP_ID, FUNC_ID, is_fd=True, cfg=_cfg_fast()) as tester, IsoTpEngine(
            RESP_ID, REQ_ID, FUNC_ID, is_fd=True, cfg=_cfg_fast()
        ) as ecu:
            tester.tx_uds_msg(req_payload, functional=False, ts_ms=0)

            tester_sent = []
            ecu_sent = []
            ecu_got = None

            for ts in range(200):
                tester_sent.extend(self._pump_and_feed(tester, ecu, ts))
                ecu_sent.extend(self._pump_and_feed(ecu, tester, ts))

                tester.tick(ts_ms=ts)
                ecu.tick(ts_ms=ts)
                self._assert_no_err(tester, "canfd_mf_tester")
                self._assert_no_err(ecu, "canfd_mf_ecu")

                ecu_got = ecu.rx_uds_msg()
                if ecu_got is not None:
                    break

            self.assertEqual(ecu_got, req_payload)
            self.assertTrue(any((data[0] & 0xF0) == 0x10 for _, data, _ in tester_sent))
            self.assertTrue(any((data[0] & 0xF0) == 0x20 for _, data, _ in tester_sent))
            self.assertTrue(any((data[0] & 0xF0) == 0x30 for _, data, _ in ecu_sent))

            for _, data, _ in tester_sent + ecu_sent:
                self.assertGreaterEqual(len(data), 8)
                self.assertLessEqual(len(data), 64)

    def test_default_response_matcher_filters_stale_response(self):
        matcher = build_uds_default_matcher(bytes([0x22, 0xF1, 0x90]))
        self.assertFalse(matcher(bytes([0x62, 0x12, 0x34])))
        self.assertTrue(matcher(bytes([0x62, 0xF1, 0x90])))
        self.assertTrue(matcher(bytes([0x7F, 0x22, 0x78])))

    def test_worker_tx_timeout_mode_ignores_stale_queue_message(self):
        with IsoTpEngineWorker(REQ_ID, RESP_ID, FUNC_ID, is_fd=False, cfg=_cfg_fast(), tick_period_ms=1) as tp:
            # stale response from previous request (different DID)
            tp.on_can_frame(RESP_ID, bytes([0x03, 0x62, 0x12, 0x34, 0, 0, 0, 0]), is_fd=False)
            # current request response
            tp.on_can_frame(RESP_ID, bytes([0x03, 0x62, 0xF1, 0x90, 0, 0, 0, 0]), is_fd=False)

            rsp = tp.tx_uds_msg(
                bytes([0x22, 0xF1, 0x90]),
                response_timeout_ms=1000,
                pending_gap_ms=300,
            )
            self.assertEqual(rsp, bytes([0x62, 0xF1, 0x90]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
