"""Fast task-cycle preflight and daily task cache.

The cache is intentionally small and independent from the task runner.  It only
answers one question: which tasks should run today?
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


CACHE_VERSION = 1
CACHE_FILENAME = "task_cycle_cache.json"
_CACHE_LOCK = threading.Lock()


def _task_name(task: Dict[str, Any]) -> str:
    return str(task.get("taskname") or task.get("task_name") or "").strip()


def _normalise_weekdays(value: Any) -> List[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result = []
    for item in value:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7 and day not in result:
            result.append(day)
    return result


def _cycle_signature(config_data: Dict[str, Any]) -> str:
    """Signature for fields that can change which tasks are due today."""
    tasks = config_data.get("tasklist") or []
    relevant = {
        "execution_mode": config_data.get("execution_mode", "manual"),
        "tasks": [
            {
                "name": _task_name(task),
                "execution_mode": task.get("execution_mode"),
                "runweek": _normalise_weekdays(task.get("runweek")),
                "has_runweek": "runweek" in task,
                "enddate": task.get("enddate"),
            }
            for task in tasks
        ],
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_metrics_getter() -> Callable[[str], Optional[Dict[str, Any]]]:
    try:
        from app.sdk.db import CalendarDB
    except ImportError:
        from sdk.db import CalendarDB

    calendar_db = CalendarDB()

    def getter(task_name: str) -> Optional[Dict[str, Any]]:
        return calendar_db.get_task_metrics(task_name)

    return getter


def evaluate_task_cycle(
    task: Dict[str, Any],
    today: date,
    *,
    global_execution_mode: str = "manual",
    metrics_getter: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Tuple[bool, str, str]:
    """Return (due, reason, effective_mode) for one task."""
    execution_mode = str(task.get("execution_mode") or global_execution_mode or "manual")
    task_name = _task_name(task)

    if execution_mode == "auto":
        if not task_name:
            execution_mode = "manual"
        else:
            try:
                getter = metrics_getter or _default_metrics_getter()
                metrics = getter(task_name)
                progress = metrics.get("progress_pct") if metrics else None
                if progress is not None:
                    progress = int(progress)
                    if progress >= 100:
                        return False, f"progress={progress}%", "auto"
                    return True, f"progress={progress}%", "auto"
            except Exception as exc:
                logging.warning("读取任务进度失败，回退到自选周期: %s (%s)", task_name or "<unnamed>", exc)
            execution_mode = "manual"

    enddate = str(task.get("enddate") or "").strip()
    if enddate:
        try:
            end_date = datetime.strptime(enddate, "%Y-%m-%d").date()
            if today > end_date:
                return False, f"enddate={enddate}", execution_mode
        except ValueError:
            logging.warning("任务结束日期格式无效，按今天可执行处理: %s (%s)", task_name or "<unnamed>", enddate)

    if "runweek" in task:
        weekdays = _normalise_weekdays(task.get("runweek"))
        if not weekdays or today.isoweekday() not in weekdays:
            return False, f"runweek={weekdays}", execution_mode

    return True, "due", execution_mode


def build_task_cycle_cache(
    config_data: Dict[str, Any],
    *,
    today: Optional[date] = None,
    metrics_getter: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    current_date = today or date.today()
    tasks = config_data.get("tasklist") or []
    global_mode = str(config_data.get("execution_mode") or "manual")
    has_auto_tasks = any(
        str(task.get("execution_mode") or global_mode) == "auto"
        for task in tasks
    )
    if metrics_getter is None and has_auto_tasks:
        try:
            metrics_getter = _default_metrics_getter()
        except Exception as exc:
            logging.warning("初始化任务进度读取器失败，按自选周期判断: %s", exc)
    due_indices: List[int] = []
    items: List[Dict[str, Any]] = []

    for index, task in enumerate(tasks):
        due, reason, effective_mode = evaluate_task_cycle(
            task,
            current_date,
            global_execution_mode=global_mode,
            metrics_getter=metrics_getter,
        )
        item = {
            "index": index,
            "taskname": _task_name(task),
            "due": due,
            "reason": reason,
            "execution_mode": effective_mode,
        }
        items.append(item)
        if due:
            due_indices.append(index)

    return {
        "version": CACHE_VERSION,
        "date": current_date.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signature": _cycle_signature(config_data),
        "total": len(tasks),
        "due_count": len(due_indices),
        "due_indices": due_indices,
        "due_names": [_task_name(tasks[index]) for index in due_indices],
        "items": items,
    }


def cache_path_for_config(config_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), CACHE_FILENAME)


def read_task_cycle_cache(config_path: str) -> Optional[Dict[str, Any]]:
    path = cache_path_for_config(config_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def write_task_cycle_cache(config_path: str, cache: Dict[str, Any]) -> None:
    path = cache_path_for_config(config_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp.{os.getpid()}"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def cache_is_valid(
    cache: Optional[Dict[str, Any]],
    config_data: Dict[str, Any],
    *,
    today: Optional[date] = None,
) -> bool:
    if not cache:
        return False
    current_date = (today or date.today()).isoformat()
    return (
        cache.get("version") == CACHE_VERSION
        and cache.get("date") == current_date
        and cache.get("signature") == _cycle_signature(config_data)
    )


def refresh_task_cycle_cache(
    config_data: Dict[str, Any],
    config_path: str,
    *,
    force: bool = False,
    today: Optional[date] = None,
    metrics_getter: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    current_date = today or date.today()
    with _CACHE_LOCK:
        if not force:
            existing = read_task_cycle_cache(config_path)
            if cache_is_valid(existing, config_data, today=current_date):
                return existing
        cache = build_task_cycle_cache(
            config_data,
            today=current_date,
            metrics_getter=metrics_getter,
        )
        write_task_cycle_cache(config_path, cache)
        logging.info(
            ">>> 任务周期预检完成: 今日 %s/%s 个任务需要执行",
            cache["due_count"],
            cache["total"],
        )
        return cache


def get_due_tasks(
    config_data: Dict[str, Any],
    config_path: str,
    *,
    today: Optional[date] = None,
    metrics_getter: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cache = refresh_task_cycle_cache(
        config_data,
        config_path,
        today=today,
        metrics_getter=metrics_getter,
    )
    tasks = config_data.get("tasklist") or []
    indices = cache.get("due_indices") or []
    if any(not isinstance(index, int) or index < 0 or index >= len(tasks) for index in indices):
        cache = refresh_task_cycle_cache(
            config_data,
            config_path,
            force=True,
            today=today,
            metrics_getter=metrics_getter,
        )
        indices = cache.get("due_indices") or []
    return [tasks[index] for index in indices], cache
