import unittest
from pathlib import Path
import sys

# Ensure `python/` is on sys.path when running from repo root.
THIS_FILE = Path(__file__).resolve()
PYTHON_DIR = THIS_FILE.parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from lintp_engine_ctypes import LinTpConfig, LinTpEngine

REQ_FRAME_ID = 0x3C
RESP_FRAME_ID = 0x3D
REQ_NAD = 0x10
FUNC_NAD = 0x7F


def _cfg_fast() -> LinTpConfig:
    return LinTpConfig(n_cr_ms=1000, max_pdu_len=4095)


class LinPackUnpackTests(unittest.TestCase):
    def _assert_no_err(self, tp: LinTpEngine, tag: str) -> None:
        err = tp.pop_error()
        self.assertIsNone(err, f"{tag} pop_error returned: {err}")

    def _pump_and_feed(self, src: LinTpEngine, dst: LinTpEngine, ts_ms: int):
        frames = src.pop_all_tx_lin_frames()
        for frame_id, data in frames:
            self.assertEqual(len(data), 8)
            dst.on_lin_frame(frame_id, data, ts_ms=ts_ms)
        return frames

    def test_lin_single_frame_pack_unpack(self):
        with LinTpEngine(REQ_FRAME_ID, RESP_FRAME_ID, REQ_NAD, FUNC_NAD, cfg=_cfg_fast()) as tp:
            payload = bytes([0x22, 0xF1, 0x90])
            tp.tx_uds_msg(payload, functional=False, ts_ms=0)

            frames = tp.pop_all_tx_lin_frames()
            self.assertEqual(len(frames), 1)
            frame_id, data = frames[0]
            self.assertEqual(frame_id, REQ_FRAME_ID)
            self.assertEqual(data, bytes([REQ_NAD, 0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00]))
            self._assert_no_err(tp, "lin_sf_pack")

            tp.on_lin_frame(RESP_FRAME_ID, bytes([REQ_NAD, 0x03, 0x62, 0xF1, 0x90, 0, 0, 0]), ts_ms=1)
            got = tp.rx_uds_msg()
            self.assertEqual(got, bytes([0x62, 0xF1, 0x90]))
            self._assert_no_err(tp, "lin_sf_unpack")

    def test_lin_multi_frame_pack_unpack(self):
        req_payload = bytes((i & 0xFF) for i in range(30))

        with LinTpEngine(0x3C, 0x3D, 0x10, 0x7F, cfg=_cfg_fast()) as tester, LinTpEngine(
            0x3D, 0x3C, 0x22, 0x7F, cfg=_cfg_fast()
        ) as ecu:
            tester.tx_uds_msg(req_payload, functional=False, ts_ms=0)

            tester_sent = []
            ecu_got = None

            for ts in range(200):
                tester_sent.extend(self._pump_and_feed(tester, ecu, ts))
                self._pump_and_feed(ecu, tester, ts)

                tester.tick(ts_ms=ts)
                ecu.tick(ts_ms=ts)
                self._assert_no_err(tester, "lin_mf_tester")
                self._assert_no_err(ecu, "lin_mf_ecu")

                ecu_got = ecu.rx_uds_msg()
                if ecu_got is not None:
                    break

            self.assertEqual(ecu_got, req_payload)
            self.assertTrue(any((data[1] & 0xF0) == 0x10 for _, data in tester_sent))
            self.assertTrue(any((data[1] & 0xF0) == 0x20 for _, data in tester_sent))


if __name__ == "__main__":
    unittest.main(verbosity=2)
