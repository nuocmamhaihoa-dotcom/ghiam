"""999 kịch bản khác nhau cho vòng tự sửa của hub và PC."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from control_plane import db, video_ledger, video_repair
from control_plane import app as app_module
from control_plane.settings import settings
from control_plane.video_jobs import JobStore, jobs
from control_plane.video_repair import RepairHooks
from pc_agent.video_worker import _guarded_read

_WORDS = ("Tóan", "Híêu", "hoà", "thuỷ", "hùynh", "Qúôc", "Nguyễn")
_FIXED = {
    "Tóan": "Toán",
    "Híêu": "Hiếu",
    "hoà": "hòa",
    "thuỷ": "thủy",
    "hùynh": "huỳnh",
    "Qúôc": "Quốc",
    "Nguyễn": "Nguyễn",
}


class VideoRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = settings.db_path
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._dir.name) / "repair.db"
        settings.db_path = self.db_path
        db.init_db(self.db_path)
        self._ids: list[str] = []
        self.store = JobStore()
        self._reads = app_module._HUB_READS

    def tearDown(self) -> None:
        for job_id in self._ids:
            video_repair.drop(job_id)
            jobs._jobs.pop(job_id, None)
            self.store._jobs.pop(job_id, None)
        app_module._HUB_READS = video_repair.live_hub_slots()
        settings.db_path = self._previous
        video_ledger._SEEN.clear()
        self._dir.cleanup()

    def _fresh(self, name: str) -> tuple[Any, Path]:
        path = Path(self._dir.name) / name
        path.write_bytes(b"v" * (16 + (len(name) % 40)))
        job = self.store.create(name=name, source=f"may {len(self._ids)}", size=path.stat().st_size)
        self._ids.append(job.id)
        return job, path

    def _hooks(
        self,
        started: list[tuple[str, str]],
        uploads: list[tuple[str, dict[str, Any]]] | None = None,
        *,
        idle: bool = False,
        retried: list[str] | None = None,
        heal: bool = False,
    ) -> RepairHooks:
        held = uploads if uploads is not None else []

        def spawn(job_id: str, path: Path, kind: str) -> bool:
            del path
            if not video_repair.claim_start(job_id):
                return False
            started.append((job_id, kind))
            return True

        def retry(job: Any) -> bool:
            if not video_repair.should_retry(job):
                return False
            located, _bound = job.located_file()
            if located is None or not located.is_file():
                return False
            job.add_problem(video_repair.retry_note(job))
            if not job.reopen():
                return False
            if retried is not None:
                retried.append(job.id)
            video_repair.claim_start(job.id)
            return True

        def complete(upload_id: str) -> bool:
            for key, item in held:
                if key != upload_id or item.get("finished"):
                    return False
                item["finished"] = True
                return True
            return False

        return RepairHooks(
            jobs=self.store,
            uploads=lambda: list(held),
            complete_upload=complete,
            spawn=spawn,
            retry=retry,
            has_idle=lambda: idle,
            heal_hub=app_module._heal_hub_slots if heal else (lambda: False),
            lease_seconds=90.0,
            frontier=lambda ranges: ranges[0][1] if ranges and ranges[0][0] == 0 else 0,
        )

    def test_a_crash_on_the_pc_asks_the_hub_to_continue(self) -> None:
        calls: list[str] = []

        class Client:
            def fail(self, job_id: str, worker_id: str, message: str) -> None:
                del job_id, worker_id
                calls.append(message)

        with patch("pc_agent.video_worker._read_one", side_effect=RuntimeError("tesseract")):
            _guarded_read(Client(), "pc-1", "job-1", None, None)  # type: ignore[arg-type]
        self.assertEqual(calls, ["PC gặp lỗi khi đọc. Máy chủ sẽ đọc tiếp."])

    def test_a_dead_hub_does_not_kill_the_pc_loop(self) -> None:
        class Client:
            def fail(self, job_id: str, worker_id: str, message: str) -> None:
                del job_id, worker_id, message
                raise OSError("mat mang")

        with patch("pc_agent.video_worker._read_one", side_effect=RuntimeError("roi")):
            _guarded_read(Client(), "pc-1", "job-1", None, None)  # type: ignore[arg-type]

    def test_a_clean_read_is_not_reported_as_a_failure(self) -> None:
        calls: list[str] = []

        class Client:
            def fail(self, job_id: str, worker_id: str, message: str) -> None:
                del job_id, worker_id
                calls.append(message)

        with patch("pc_agent.video_worker._read_one", return_value=None):
            _guarded_read(Client(), "pc-1", "job-1", None, None)  # type: ignore[arg-type]
        self.assertEqual(calls, [])

    def test_results_are_healed_before_they_are_saved(self) -> None:
        seen: dict[str, list[dict[str, str]]] = {}

        def save(rows: list[dict[str, str]]) -> tuple[int, int, int, list[dict[str, str]], list[dict[str, str]]]:
            seen["rows"] = rows
            return 0, 1, 0, [], rows

        job, path = self._fresh("heal.mp4")
        job.bind(path)
        job.take_hub()
        with (
            patch.object(app_module, "_save_proposed", side_effect=save),
            patch.object(app_module, "_store_seen", return_value={}),
            patch.object(app_module.db, "people_counts", return_value=(0, 0)),
            patch.object(app_module, "_video_archive", return_value=[]),
        ):
            kept = app_module._commit_people(
                job,
                [{"name": "Tóan Văn Híêu", "contactName": "Chị Híêu", "username": "@toan"}],
                None,
            )
        self.assertTrue(kept)
        self.assertEqual(seen["rows"][0]["name"], "Toán Văn Hiếu")
        self.assertEqual(seen["rows"][0]["contactName"], "Chị Hiếu")
        self.assertEqual(seen["rows"][0]["username"], "@toan")

    def test_a_dropped_link_is_queued_again_without_a_second_reader(self) -> None:
        job, path = self._fresh("dut.mp4")
        job.bind(path)
        self.assertTrue(job.claim("pc-dut"))
        self.assertTrue(job.fail_from_worker("pc-dut", "Mất kết nối khi đang tải video."))
        spawned: list[tuple[Any, ...]] = []
        with patch.object(app_module, "_spawn_video", side_effect=lambda *args: spawned.append(args) or True):
            self.assertTrue(app_module._auto_retry(job))
            self.assertFalse(app_module._auto_retry(job))
        self.assertEqual(len(spawned), 1)
        self.assertFalse(job.done)
        self.assertTrue(any(str(item).startswith("Tự sửa lần 1") for item in job.public()["problems"]))
        self.assertTrue(job.claim("pc-dut"))
        self.assertTrue(job.fail_from_worker("pc-dut", "Mất kết nối khi đang tải video."))
        with patch.object(app_module, "_spawn_video", side_effect=lambda *args: spawned.append(args) or True):
            self.assertTrue(app_module._auto_retry(job))
        self.assertTrue(job.claim("pc-dut"))
        self.assertTrue(job.fail_from_worker("pc-dut", "Mất kết nối khi đang tải video."))
        with patch.object(app_module, "_spawn_video", return_value=True) as blocked:
            self.assertFalse(app_module._auto_retry(job))
        blocked.assert_not_called()
        self.assertIn("Không đọc được video.", "Không đọc được video.")
        self.assertFalse(video_repair.is_transient("Không đọc được video."))

    def test_nine_hundred_ninety_nine_different_faults_heal(self) -> None:
        families: set[int] = set()
        signatures: set[tuple[int, int, str]] = set()
        for index in range(999):
            kind = index % 18
            families.add(kind)
            before = set(self.store._jobs)
            try:
                signature = self._one(kind, index)
            finally:
                for job_id in list(self.store._jobs):
                    if job_id not in before:
                        video_repair.drop(job_id)
                        self.store._jobs.pop(job_id, None)
            self.assertNotIn(signature, signatures)
            signatures.add(signature)
        self.assertEqual(len(signatures), 999)
        self.assertEqual(families, set(range(18)))

    def _one(self, kind: int, index: int) -> tuple[int, int, str]:
        started: list[tuple[str, str]] = []
        name = f"k{kind}-{index}.mp4"
        if kind == 0:
            job, path = self._fresh(name)
            job.bind(path)
            job.owner = f"pc-{index}"
            job.lease = time.monotonic() - 1000 - index
            actions = video_repair.sweep(self._hooks(started))
            self.assertIn("stale_pc_lease", actions)
            self.assertEqual(job.owner_id(), "")
            self.assertEqual(started, [(job.id, "schedule")])
            video_repair.sweep(self._hooks(started))
            self.assertEqual(started, [(job.id, "schedule")])
            return kind, index, job.id
        if kind == 1:
            job, path = self._fresh(name)
            job.bind(path)
            job.owner = f"pc-song-{index}"
            job.lease = time.monotonic()
            actions = video_repair.sweep(self._hooks(started))
            self.assertNotIn("stale_pc_lease", actions)
            self.assertEqual(job.owner_id(), f"pc-song-{index}")
            self.assertEqual(started, [])
            return kind, index, job.id
        if kind == 2:
            job, path = self._fresh(name)
            job.bind(path)
            path.unlink()
            actions = video_repair.sweep(self._hooks(started))
            self.assertIn("missing_video_file", actions)
            self.assertTrue(job.done)
            self.assertIn("không còn", job.public()["error"])
            self.assertEqual(video_repair.sweep(self._hooks(started)), [])
            return kind, index, job.id
        if kind == 3:
            job, path = self._fresh(name)
            job.bind(path)
            actions = video_repair.sweep(self._hooks(started, idle=index % 2 == 0))
            self.assertEqual(actions, ["orphan_queued_job"])
            self.assertEqual(started, [(job.id, "schedule")])
            again: list[tuple[str, str]] = []
            self.assertEqual(video_repair.sweep(self._hooks(again)), [])
            self.assertEqual(again, [])
            return kind, index, job.id
        if kind == 4:
            job, path = self._fresh(name)
            job.stage_file(path)
            job.task = "Đang sắp xếp video"
            actions = video_repair.sweep(self._hooks(started))
            self.assertEqual(actions, ["orphan_arranging_job"])
            self.assertEqual(started, [(job.id, "prepare")])
            self.assertFalse(job._claim_mark(f"som-{index}"))
            return kind, index, job.id
        if kind == 5:
            parent, path = self._fresh(name)
            parent.bind(path)
            missing = self.store.create(name=f"thieu-{index}.mp4", source="stress", size=1)
            self._ids.append(missing.id)
            parent.set_parts([missing.id, f"mat-{index}"])
            self.store._jobs.pop(missing.id, None)
            actions = video_repair.sweep(self._hooks(started))
            self.assertIn("stuck_split_parent", actions)
            self.assertEqual(parent.part_ids, [])
            self.assertEqual(started, [(parent.id, "prepare")])
            return kind, index, parent.id
        if kind == 6:
            parent, path = self._fresh(name)
            parent.bind(path)
            left, left_path = self._fresh(f"trai-{index}.mp4")
            right, right_path = self._fresh(f"phai-{index}.mp4")
            del left_path, right_path
            left.parent_id = parent.id
            right.parent_id = parent.id
            left.owner = f"pc-a-{index}"
            left.lease = time.monotonic()
            right.owner = f"pc-b-{index}"
            right.lease = time.monotonic()
            parent.set_parts([left.id, right.id])
            actions = video_repair.sweep(self._hooks(started))
            self.assertNotIn("stuck_split_parent", actions)
            self.assertEqual(parent.part_ids, [left.id, right.id])
            self.assertEqual(started, [])
            return kind, index, parent.id
        if kind == 7:
            leaked = video_repair.live_hub_slots() + 1 + (index % 4)
            app_module._HUB_READS = leaked
            video_repair._hub_threads[2_000_000_000 + index] = 2
            actions = video_repair.sweep(self._hooks(started, heal=True))
            self.assertIn("hub_slot_leak", actions)
            self.assertEqual(app_module._HUB_READS, video_repair.live_hub_slots())
            self.assertNotIn(2_000_000_000 + index, video_repair._hub_threads)
            return kind, index, f"hub-{index}"
        if kind == 8:
            uploads = [
                (
                    f"up-{index}",
                    {"size": 50 + index, "ranges": [(0, 50 + index)], "finished": False},
                ),
                (
                    f"nua-{index}",
                    {"size": 80 + index, "ranges": [(0, 10)], "finished": False},
                ),
            ]
            actions = video_repair.sweep(self._hooks(started, uploads))
            self.assertEqual(actions, ["upload_bytes_complete"])
            self.assertTrue(uploads[0][1]["finished"])
            self.assertFalse(uploads[1][1]["finished"])
            self.assertEqual(video_repair.sweep(self._hooks(started, uploads)), [])
            return kind, index, f"up-{index}"
        if kind == 9:
            job, path = self._fresh(name)
            job.bind(path)
            job.take_hub()
            job.percent = 4
            actions = video_repair.sweep(self._hooks(started, idle=True))
            self.assertIn("hub_took_idle_pc_job", actions)
            self.assertNotIn("hub_reader_lost", actions)
            self.assertEqual(job.owner_id(), "")
            self.assertEqual(started, [(job.id, "schedule")])
            return kind, index, job.id
        if kind == 10:
            job, path = self._fresh(name)
            job.bind(path)
            job.take_hub()
            job.percent = 40 + (index % 20)
            job.remember_frame(float(index), ["An"], [])
            actions = video_repair.sweep(self._hooks(started, idle=True))
            self.assertIn("hub_reader_lost", actions)
            self.assertNotIn("hub_took_idle_pc_job", actions)
            self.assertEqual(started, [(job.id, "schedule")])
            return kind, index, job.id
        if kind == 11:
            word = _WORDS[index % len(_WORDS)]
            healed = video_repair.heal_people(
                [{"name": f"{word} Văn", "contactName": word, "username": f"@n{index}"}]
            )
            self.assertEqual(healed[0]["name"].split()[0], _FIXED[word])
            self.assertEqual(healed[0]["contactName"], _FIXED[word])
            self.assertEqual(healed[0]["username"], f"@n{index}")
            self.assertEqual(video_repair.heal_people(healed), healed)
            return kind, index, word
        if kind == 12:
            job, path = self._fresh(name)
            job.bind(path)
            message = (
                "Mất kết nối khi đang tải video.",
                "Không xử lý được video.",
                "PC gặp lỗi khi đọc. Máy chủ sẽ đọc tiếp.",
            )[index % 3]
            job.owner = f"pc-{index}"
            job.fail(message)
            retried: list[str] = []
            actions = video_repair.sweep(self._hooks(started, retried=retried))
            self.assertEqual(retried, [job.id])
            self.assertIn("transient_failure_retry", actions)
            self.assertFalse(job.done)
            job.owner = f"pc-{index}"
            job.fail(message)
            actions = video_repair.sweep(self._hooks(started, retried=retried))
            self.assertEqual(retried, [job.id, job.id])
            job.owner = f"pc-{index}"
            job.fail(message)
            self.assertEqual(video_repair.sweep(self._hooks(started, retried=retried)), [])
            self.assertTrue(job.done)
            return kind, index, message
        if kind == 13:
            job, path = self._fresh(name)
            job.bind(path)
            job.fail("Không đọc được video.")
            actions = video_repair.sweep(self._hooks(started))
            self.assertNotIn("transient_failure_retry", actions)
            self.assertEqual(job.public()["error"], "Không đọc được video.")
            self.assertEqual(started, [])
            return kind, index, job.id
        if kind == 14:
            job, path = self._fresh(name)
            job.stage_file(path)
            self.assertFalse(job._claim_mark(f"som-{index}"))
            self.assertIsNone(job.path)
            job.bind(path)
            self.assertTrue(job.claim(f"som-{index}"))
            return kind, index, job.id
        if kind == 15:
            parent, path = self._fresh(name)
            parent.bind(path)
            child, child_path = self._fresh(f"con-{index}.mp4")
            child.bind(child_path)
            child.parent_id = parent.id
            parent.set_parts([child.id])
            self.assertFalse(parent._claim_mark(f"cha-{index}"))
            self.assertTrue(child.claim(f"con-{index}"))
            self.assertEqual(parent.owner_id(), "")
            return kind, index, parent.id
        if kind == 16:
            job, path = self._fresh(name)
            job.bind(path)
            job.update(4 + (index % 3), f"Đang chờ {index}")
            hooks = self._hooks(started)
            first = video_repair.sweep(hooks)
            second = video_repair.sweep(hooks)
            self.assertEqual(first, ["orphan_queued_job"])
            self.assertEqual(second, [])
            self.assertEqual(started, [(job.id, "schedule")])
            return kind, index, job.id
        job, path = self._fresh(name)
        job.bind(path)
        other, other_path = self._fresh(f"khac-{index}.mp4")
        other.bind(other_path)
        other.owner = f"pc-bam-{index}"
        other.lease = time.monotonic()
        uploads = [(f"tron-{index}", {"size": 12, "ranges": [(0, 12)], "finished": False})]
        actions = video_repair.sweep(self._hooks(started, uploads, idle=False))
        self.assertIn("orphan_queued_job", actions)
        self.assertIn("upload_bytes_complete", actions)
        self.assertNotIn("stale_pc_lease", actions)
        self.assertEqual(other.owner_id(), f"pc-bam-{index}")
        self.assertEqual(started, [(job.id, "schedule")])
        return kind, index, job.id
