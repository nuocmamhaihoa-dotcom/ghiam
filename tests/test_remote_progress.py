"""PC gửi quy luật video đã học lên hub bằng kênh riêng, không lẫn vào danh sách vấn đề."""

from __future__ import annotations

import unittest

from pc_agent.video_worker import RemoteProgress


class _Client:
    def __init__(self, fail_first: bool = False) -> None:
        self.sent: list[tuple[list[str], str]] = []
        self._fail = fail_first

    def checkpoint(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def progress(self, job_id: str, worker_id: str, percent: int, task: str, problems: list[str], learned: str = "") -> None:
        del job_id, worker_id, percent, task
        if self._fail:
            self._fail = False
            raise OSError("mạng đứt")
        self.sent.append((list(problems), learned))


class RemoteProgressLearnedTests(unittest.TestCase):
    def test_the_learned_note_goes_in_its_own_field_once(self) -> None:
        client = _Client()
        progress = RemoteProgress(client, "job", "worker")  # type: ignore[arg-type]
        progress.note_learned("Quy luật học được: @ nằm ở dải y 430 đến 459.")
        progress.problem("3 khung không có chữ.")
        progress.close()
        learned = [item for item in client.sent if item[1]]
        self.assertEqual(len(learned), 1)
        self.assertEqual(learned[0][1], "Quy luật học được: @ nằm ở dải y 430 đến 459.")
        problems = [text for sent, _note in client.sent for text in sent]
        self.assertEqual(problems, ["3 khung không có chữ."])

    def test_a_lost_send_keeps_the_note_for_the_next_one(self) -> None:
        client = _Client(fail_first=True)
        progress = RemoteProgress(client, "job", "worker")  # type: ignore[arg-type]
        progress._stop.set()
        progress._thread.join(timeout=2)
        progress.note_learned("Quy luật học được: ghi chú.")
        progress._send()
        self.assertEqual(client.sent, [])
        progress._send()
        self.assertEqual(client.sent[-1][1], "Quy luật học được: ghi chú.")


if __name__ == "__main__":
    unittest.main()
