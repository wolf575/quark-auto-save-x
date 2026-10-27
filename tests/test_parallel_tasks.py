import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import quark_auto_save as qas
from treelib import Tree


class ParallelTaskTests(unittest.TestCase):
    def test_groups_keep_first_occurrence_order(self):
        groups = qas._build_task_groups([
            {"taskname": "A", "id": 0},
            {"taskname": "B", "id": 1},
            {"taskname": "A", "id": 2},
        ])
        self.assertEqual(["A", "B"], [group["name"] for group in groups])
        self.assertEqual([(0, {"taskname": "A", "id": 0}), (2, {"taskname": "A", "id": 2})], groups[0]["tasks"])

    def test_only_first_successful_probe_per_group_is_executed(self):
        tasks = [
            {"taskname": "A", "id": 0},
            {"taskname": "B", "id": 1},
            {"taskname": "A", "id": 2},
        ]
        probe_success = {0: False, 1: True, 2: True}
        executed = []
        lock = threading.Lock()

        def fake_probe(_account, index, task):
            return {
                "index": index,
                "task": task,
                "success": probe_success[index],
                "notify_lines": [],
                "error": None,
            }

        def fake_execute(_account, group_name, candidates):
            with lock:
                executed.append((group_name, [item["index"] for item in candidates]))
            return {
                "group_name": group_name,
                "success": True,
                "task_index": candidates[0]["index"],
                "notify_lines": [f"result-{candidates[0]['index']}"],
                "error": None,
            }

        qas.NOTIFYS.clear()
        with patch.object(qas, "_probe_one_task", side_effect=fake_probe), \
             patch.object(qas, "_execute_task_group", side_effect=fake_execute), \
             patch.object(qas.Quark, "update_savepath_fid", return_value=None):
            qas.do_save_parallel(SimpleNamespace(cookie="cookie", index=1), tasks)

        self.assertEqual(
            sorted(executed),
            [("A", [2]), ("B", [1])],
        )
        self.assertEqual(["result-2", "result-1"], qas.NOTIFYS)
        qas.NOTIFYS.clear()

    def test_probe_mode_is_passed_to_directory_check(self):
        account = qas.Quark("kps=x; sign=y; vcode=z", 0)
        account.savepath_fid["/save"] = "fid"
        task = {
            "taskname": "probe",
            "savepath": "save",
            "shareurl": "https://pan.quark.cn/s/example",
            "pattern": "",
            "replace": "",
        }
        tree = Tree()
        tree.create_node("root", "root")

        with patch.object(account, "extract_url", return_value=("pwd", "", "0", [])), \
             patch.object(account, "get_stoken", return_value=(True, "stoken", None)), \
             patch.object(account, "get_detail", return_value={"list": []}), \
             patch.object(account, "magic_regex_func", return_value=("", "")), \
             patch.object(account, "dir_check_and_save", return_value=tree) as dir_check:
            account.do_save_task(task, probe_only=True)

        self.assertTrue(dir_check.call_args.kwargs.get("probe_only"))

    def test_notification_buffer_is_thread_local(self):
        qas.NOTIFYS.clear()
        local_buffer = []
        qas._set_notify_buffer(local_buffer)
        try:
            qas.add_notify("thread-local line")
        finally:
            qas._clear_notify_buffer()

        self.assertEqual(["thread-local line"], local_buffer)
        self.assertEqual([], qas.NOTIFYS)


if __name__ == "__main__":
    unittest.main()
