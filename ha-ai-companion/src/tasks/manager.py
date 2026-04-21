import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskManager:
    """Persistent scheduled AI tasks — stored as JSON files, run by asyncio loop."""

    def __init__(self, tasks_dir: str):
        self.tasks_dir = tasks_dir
        os.makedirs(tasks_dir, exist_ok=True)
        self._scheduler_task: Optional[asyncio.Task] = None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_task(self, name: str, prompt: str, entity_id: str, schedule: str) -> Dict[str, Any]:
        task: Dict[str, Any] = {
            "id": uuid4().hex[:8],
            "name": name,
            "prompt": prompt,
            "entity_id": entity_id,
            "schedule": schedule,  # "daily HH:MM"
            "last_run": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(task)
        return task

    def list_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        for fname in os.listdir(self.tasks_dir):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(self.tasks_dir, fname)) as f:
                        tasks.append(json.load(f))
                except Exception as e:
                    logger.warning(f"Skipping corrupt task file {fname}: {e}")
        return sorted(tasks, key=lambda t: t.get("created_at", ""))

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.tasks_dir, f"{task_id}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def delete_task(self, task_id: str) -> bool:
        path = os.path.join(self.tasks_dir, f"{task_id}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def _write(self, task: Dict[str, Any]) -> None:
        path = os.path.join(self.tasks_dir, f"{task['id']}.json")
        with open(path, "w") as f:
            json.dump(task, f, indent=2)

    def _mark_run(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task:
            task["last_run"] = datetime.now(timezone.utc).isoformat()
            self._write(task)

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def _is_due(self, task: Dict[str, Any]) -> bool:
        schedule = task.get("schedule", "")
        if not schedule.startswith("daily "):
            return False
        try:
            hour, minute = map(int, schedule[6:].split(":"))
        except ValueError:
            return False

        now = datetime.now()
        if now.hour != hour or now.minute != minute:
            return False

        last_run = task.get("last_run")
        if last_run:
            try:
                lr_dt = datetime.fromisoformat(last_run)
                if lr_dt.tzinfo:
                    lr_dt = lr_dt.astimezone().replace(tzinfo=None)
                if lr_dt.date() >= now.date():
                    return False
            except Exception:
                pass

        return True

    async def start(self, agent_system: Any) -> None:
        self._scheduler_task = asyncio.create_task(self._run_loop(agent_system))
        logger.info(f"Task scheduler started (tasks dir: {self.tasks_dir})")

    async def stop(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self, agent_system: Any) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                await self._run_due(agent_system)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}", exc_info=True)

    async def _run_due(self, agent_system: Any) -> None:
        for task in self.list_tasks():
            if not self._is_due(task):
                continue
            logger.info(f"Running scheduled task '{task['name']}' ({task['id']})")
            try:
                result = await self._run_ai_task(task, agent_system)
                if result and task.get("entity_id"):
                    await agent_system.tools.set_ha_text_entity(
                        entity_id=task["entity_id"],
                        value=result[:255],
                    )
                self._mark_run(task["id"])
            except Exception as e:
                logger.error(f"Task {task['id']} failed: {e}", exc_info=True)

    async def _run_ai_task(self, task: Dict[str, Any], agent_system: Any) -> str:
        system = (
            "You are a Home Assistant AI assistant. "
            "Respond in plain text only (no markdown). "
            "Keep your answer under 255 characters — it will be stored in a Home Assistant text entity."
        )
        params = {
            "model": agent_system.config_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": task["prompt"]},
            ],
            "stream": False,
            "max_tokens": 300,
        }
        response = await agent_system.config_client.chat.completions.create(**params)
        return (response.choices[0].message.content or "").strip()[:255]
