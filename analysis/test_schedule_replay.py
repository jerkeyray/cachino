from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from schedule_replay import SCHEDULES, evaluate, totals  # noqa: E402


def rows():
    # Two sessions interleaved; each turn extends its session's previous prompt.
    a = [[1] * 9 + [10 + t] * 4 * t for t in range(1, 4)]
    b = [[2] * 9 + [20 + t] * 4 * t for t in range(1, 4)]
    out = []
    for t in range(3):
        out.append({"session": "a", "turn": t, "ids": a[t]})
        out.append({"session": "b", "turn": t, "ids": b[t]})
    return out


class ScheduleTests(unittest.TestCase):
    def test_every_schedule_keeps_session_turn_order(self):
        data = rows()
        for name, schedule in SCHEDULES.items():
            order = schedule(data, 64, 4)
            self.assertEqual(sorted(order), list(range(len(data))), name)
            for session in ("a", "b"):
                turns = [data[i]["turn"] for i in order if data[i]["session"] == session]
                self.assertEqual(turns, sorted(turns), name)

    def test_serial_runs_sessions_to_completion(self):
        data = rows()
        self.assertEqual([data[i]["session"] for i in SCHEDULES["serial"](data, 64, 4)], list("aaabbb"))

    def test_delay_counts_positions_after_recorded(self):
        data = rows()
        per = evaluate(data, SCHEDULES["serial"](data, 64, 4), 64, 4)
        self.assertEqual(totals(per, "predicted")["max_delay"], 2)


if __name__ == "__main__":
    unittest.main()
