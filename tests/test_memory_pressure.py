#!/usr/bin/env python3
"""Wake gate for durable-memory fill level.

Why this gate exists: the char limit only guards *writes*. Once a file reaches
it, the agent silently stops recording facts — no exception, nothing in the log.
And the ordinary wake gate asks "is there anything to promote?", so on a quiet
night nobody is awake to notice the ceiling approaching. Reaching the threshold
therefore has to count as work in its own right.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dream-precheck.py"
spec = importlib.util.spec_from_file_location("dream_precheck", SCRIPT)
pc = importlib.util.module_from_spec(spec)
sys.modules["dream_precheck"] = pc
spec.loader.exec_module(pc)


def usage(memory_pct=None, user_pct=None):
    out = {}
    if memory_pct is not None:
        out["memories/MEMORY.md"] = {"chars": 42 * memory_pct, "limit": 4200, "percent": memory_pct}
    if user_pct is not None:
        out["memories/USER.md"] = {"chars": 28 * user_pct, "limit": 2800, "percent": user_pct}
    # A file without a configured limit has no percent — it must never trip the gate.
    out["NOTES.md"] = {"chars": 99999}
    return out


class TestMemoryPressure(unittest.TestCase):
    def test_below_threshold_is_quiet(self):
        self.assertEqual(pc.memory_pressure(usage(64, 59), 85), [])

    def test_at_threshold_counts(self):
        """Exactly at the threshold already counts: waiting for 86% buys nothing."""
        hot = pc.memory_pressure(usage(85), 85)
        self.assertEqual([h["file"] for h in hot], ["memories/MEMORY.md"])

    def test_worst_file_first(self):
        hot = pc.memory_pressure(usage(88, 96), 85)
        self.assertEqual([h["percent"] for h in hot], [96, 88])

    def test_file_without_limit_never_trips(self):
        """NOTES.md is huge but has no limit — it is not memory under budget."""
        self.assertEqual(pc.memory_pressure(usage(10), 85), [])

    def test_threshold_zero_disables_gate(self):
        self.assertEqual(pc.memory_pressure(usage(99), 0), [])

    def test_reports_numbers_for_the_message(self):
        """The agent has to name the file and the figures, not just say «tight»."""
        hot = pc.memory_pressure(usage(90), 85)[0]
        self.assertEqual(hot["limit"], 4200)
        self.assertIn("chars", hot)


class TestGateIntegration(unittest.TestCase):
    """compact_payload is the actual gate: None means the agent stays asleep."""

    def _data(self, stats, mem_pct):
        return {"stats": stats, "memory_usage": usage(mem_pct), "generated_at": "2026-08-24T03:00:00"}

    def test_no_work_no_pressure_sleeps(self):
        self.assertIsNone(pc.compact_payload(self._data({}, 50), full_pct=85))

    def test_pressure_alone_wakes(self):
        """The regression this gate was written for: nothing to promote, memory at 95%."""
        out = pc.compact_payload(self._data({}, 95), full_pct=85)
        self.assertIsNotNone(out, "память под потолком — это работа, а не тишина")
        self.assertTrue(out["memory_pressure"])
        self.assertEqual(out["memory_pressure"][0]["percent"], 95)

    def test_work_without_pressure_still_wakes(self):
        out = pc.compact_payload(self._data({"promotions": 2}, 50), full_pct=85)
        self.assertIsNotNone(out)
        # Empty sections are dropped, so a calm file leaves no flag to misread.
        self.assertNotIn("memory_pressure", out)

    def test_pressure_key_absent_when_calm(self):
        out = pc.compact_payload(self._data({"conflicts": 1}, 64), full_pct=85)
        self.assertNotIn("memory_pressure", out)


class TestConfig(unittest.TestCase):
    def test_default_threshold_is_sane(self):
        self.assertGreater(pc.DEFAULT_MEMORY_FULL_PCT, 50)
        self.assertLess(pc.DEFAULT_MEMORY_FULL_PCT, 100)

    def test_config_returns_three_values(self):
        """_precheck_config gained a third field — callers must be updated with it."""
        keys, max_content, full_pct = pc._precheck_config()
        self.assertIsInstance(keys, tuple)
        self.assertIsInstance(max_content, int)
        self.assertIsInstance(full_pct, int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
