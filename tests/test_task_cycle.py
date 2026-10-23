import os
import tempfile
import unittest
from datetime import date

from app.task_cycle import (
    cache_is_valid,
    evaluate_task_cycle,
    get_due_tasks,
    read_task_cycle_cache,
    refresh_task_cycle_cache,
)


class TaskCycleTests(unittest.TestCase):
    def test_manual_cycle_uses_weekday_and_end_date(self):
        monday = date(2026, 10, 19)
        task = {"taskname": "weekly", "runweek": [1, 3], "enddate": "2026-10-20"}

        due, reason, mode = evaluate_task_cycle(task, monday)
        self.assertTrue(due)
        self.assertEqual("due", reason)
        self.assertEqual("manual", mode)

        not_due, reason, _ = evaluate_task_cycle(task, date(2026, 10, 20))
        self.assertFalse(not_due)
        self.assertIn("runweek", reason)

        expired, reason, _ = evaluate_task_cycle(task, date(2026, 10, 21))
        self.assertFalse(expired)
        self.assertIn("enddate", reason)

    def test_empty_runweek_means_no_weekday(self):
        due, reason, _ = evaluate_task_cycle(
            {"taskname": "disabled-weekdays", "runweek": []},
            date(2026, 10, 19),
        )
        self.assertFalse(due)
        self.assertIn("runweek", reason)

    def test_auto_cycle_uses_progress_and_falls_back_without_metrics(self):
        today = date(2026, 10, 19)
        task = {"taskname": "show", "execution_mode": "auto", "runweek": [1]}

        due, _, mode = evaluate_task_cycle(
            task,
            today,
            metrics_getter=lambda _: {"progress_pct": 50},
        )
        self.assertTrue(due)
        self.assertEqual("auto", mode)

        done, reason, mode = evaluate_task_cycle(
            task,
            today,
            metrics_getter=lambda _: {"progress_pct": 100},
        )
        self.assertFalse(done)
        self.assertEqual("progress=100%", reason)
        self.assertEqual("auto", mode)

        fallback_due, _, mode = evaluate_task_cycle(
            task,
            today,
            metrics_getter=lambda _: None,
        )
        self.assertTrue(fallback_due)
        self.assertEqual("manual", mode)

    def test_cache_round_trip_and_due_task_selection(self):
        today = date(2026, 10, 19)
        config = {
            "execution_mode": "manual",
            "tasklist": [
                {"taskname": "monday", "runweek": [1]},
                {"taskname": "tuesday", "runweek": [2]},
                {"taskname": "automatic", "execution_mode": "auto"},
            ],
        }
        metrics = {"automatic": {"progress_pct": 100}}
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "quark_config.json")
            cache = refresh_task_cycle_cache(
                config,
                config_path,
                today=today,
                metrics_getter=lambda name: metrics.get(name),
            )
            self.assertEqual([0], cache["due_indices"])
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "task_cycle_cache.json")))

            due_tasks, loaded = get_due_tasks(
                config,
                config_path,
                today=today,
                metrics_getter=lambda name: metrics.get(name),
            )
            self.assertEqual(["monday"], [task["taskname"] for task in due_tasks])
            self.assertEqual(cache["signature"], loaded["signature"])

            config["tasklist"][0]["runweek"] = [2]
            self.assertFalse(cache_is_valid(read_task_cycle_cache(config_path), config, today=today))


if __name__ == "__main__":
    unittest.main()
