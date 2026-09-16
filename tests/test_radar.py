import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "src" / "gmgn-dlmm-radar.py"
SPEC = importlib.util.spec_from_file_location("gmgn_bsc_base_radar", SCRIPT)
RADAR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RADAR)


class RadarTests(unittest.TestCase):
    def test_chain_commands_use_evm_gates(self):
        for chain in ("arc", "bsc", "base"):
            command = RADAR.trend_command(chain)
            self.assertIn(f"--chain {chain}", command)
            self.assertIn("--min-created 30m", command)
            self.assertIn("--min-marketcap 100000", command)
            self.assertNotIn("--min-gas-fee", command)

    def test_unsupported_chain_is_rejected(self):
        with self.assertRaises(ValueError):
            RADAR.trend_command("sol")

    def test_chain_selection_requires_two_supported_chains(self):
        self.assertEqual(RADAR.parse_chains("arc,bsc"), ("arc", "bsc"))
        self.assertEqual(RADAR.parse_chains("base, arc"), ("base", "arc"))
        with self.assertRaises(ValueError):
            RADAR.parse_chains("arc")
        with self.assertRaises(ValueError):
            RADAR.parse_chains("arc,arc")
        with self.assertRaises(ValueError):
            RADAR.parse_chains("sol,bsc")

    def test_money_formatting(self):
        self.assertEqual(RADAR.money(1_250_000), "1.2M")
        self.assertEqual(RADAR.money(12_500), "12k")
        self.assertEqual(RADAR.money(12), "12")

    def test_wash_trading_filter(self):
        self.assertTrue(RADAR.safe_for_dlmm({"symbol": "OK"}))
        self.assertFalse(RADAR.safe_for_dlmm({"is_wash_trading": True}))

    def test_split_message_preserves_pre_tag(self):
        chunks = RADAR.split_message("<pre>one\ntwo\nthree</pre>", limit=4)
        self.assertTrue(all(chunk.startswith("<pre>") and chunk.endswith("</pre>") for chunk in chunks))

    def test_signal_sender_sends_only_signal_parts(self):
        sent = []
        original_send = RADAR.send
        try:
            RADAR.send = lambda text, chat_id, thread_id="": sent.append((text, chat_id, thread_id)) or {"ok": True}
            count = RADAR.send_signal_report("<pre>SIGNAL</pre>", "-1001", "42")
        finally:
            RADAR.send = original_send
        self.assertEqual(count, 1)
        self.assertEqual(sent, [("<pre>SIGNAL</pre>", "-1001", "42")])


if __name__ == "__main__":
    unittest.main()
