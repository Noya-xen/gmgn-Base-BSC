import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "src" / "gmgn-dlmm-radar.py"
SPEC = importlib.util.spec_from_file_location("gmgn_bsc_base_radar", SCRIPT)
RADAR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RADAR)


class RadarTests(unittest.TestCase):
    def test_chain_commands_use_evm_gates(self):
        for chain in RADAR.SUPPORTED_CHAINS:
            command = RADAR.trend_command(chain)
            self.assertIn(f"--chain {chain}", command)
            self.assertIn("--min-created 30m", command)
            self.assertIn("--min-marketcap 100000", command)
            self.assertNotIn("--min-gas-fee", command)

    def test_unsupported_chain_is_rejected(self):
        with self.assertRaises(ValueError):
            RADAR.trend_command("unknown")

    def test_chain_selection_accepts_any_distinct_supported_subset(self):
        self.assertEqual(RADAR.parse_chains("arc,bsc"), ("arc", "bsc"))
        self.assertEqual(RADAR.parse_chains("base, arc"), ("base", "arc"))
        self.assertEqual(RADAR.parse_chains("sol"), ("sol",))
        self.assertEqual(
            RADAR.parse_chains("sol,base,arbitrum"),
            ("sol", "base", "arbitrum"),
        )
        self.assertEqual(RADAR.parse_chains(",".join(RADAR.SUPPORTED_CHAINS)), RADAR.SUPPORTED_CHAINS)
        with self.assertRaises(ValueError):
            RADAR.parse_chains("arc,arc")
        with self.assertRaises(ValueError):
            RADAR.parse_chains("")
        with self.assertRaises(ValueError):
            RADAR.parse_chains("sol,unknown")

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

    def test_watch_sender_sends_watch_parts(self):
        sent = []
        original_send = RADAR.send
        original_send_watch = RADAR.SEND_WATCH
        try:
            RADAR.SEND_WATCH = True
            RADAR.send = lambda text, chat_id, thread_id="": sent.append((text, chat_id, thread_id)) or {"ok": True}
            count = RADAR.send_watch_report("<b>WATCH</b>", "-1001", "42")
        finally:
            RADAR.send = original_send
            RADAR.SEND_WATCH = original_send_watch
        self.assertEqual(count, 1)
        self.assertEqual(sent, [("<b>WATCH</b>", "-1001", "42")])

    def test_watch_sender_can_be_disabled(self):
        original_send_watch = RADAR.SEND_WATCH
        try:
            RADAR.SEND_WATCH = False
            self.assertEqual(RADAR.send_watch_report("<b>WATCH</b>", "-1001"), 0)
        finally:
            RADAR.SEND_WATCH = original_send_watch

    def test_volume_spike_uses_last_15m_and_1h_candles(self):
        candles = [
            {"time": 1, "open": "1", "close": "1", "volume": "300000"},
            {"time": 2, "open": "1", "close": "1", "volume": "300000"},
            {"time": 3, "open": "1", "close": "1", "volume": "400000"},
            {"time": 4, "open": "1", "close": "1", "volume": "1000000"},
        ]
        fake = SimpleNamespace(stdout=json.dumps({"list": candles}))
        with patch.object(RADAR.subprocess, "run", return_value=fake) as run:
            data = RADAR.volume_spike_data({"address": "0xabc"}, "base")
        self.assertEqual(data["volume_15m"], 1_000_000)
        self.assertEqual(data["volume_1h"], 2_000_000)
        self.assertEqual(data["spike_ratio"], 2.0)
        command = run.call_args.args[0]
        self.assertIn("--resolution", command)
        self.assertIn("15m", command)

    def test_volume_alert_accepts_either_window_and_shows_both_statuses(self):
        data = {
            "volume_15m": 600_000,
            "volume_1h": 800_000,
            "spike_ratio": 3.0,
            "change_15m": 5.0,
        }
        status = RADAR.volume_threshold_status(data)
        self.assertEqual(status["status"], "15m")
        self.assertTrue(status["pass_15m"])
        self.assertFalse(status["pass_1h"])

        match = {
            "chain": "base",
            "token": {
                "symbol": "TEST",
                "market_cap": 1_250_000,
                "address": "0xabc",
            },
            "data": data,
            **status,
        }
        report = RADAR.build_volume_report([match], ("base",))
        self.assertIn("⚡ 15M PASS", report)
        self.assertIn("MC: $1.2M", report)
        self.assertIn("CA: <code>0xabc</code>", report)
        self.assertNotIn("Chart GMGN", report)
        self.assertIsNone(
            RADAR.volume_threshold_status({"volume_15m": 1, "volume_1h": 1})
        )


if __name__ == "__main__":
    unittest.main()
