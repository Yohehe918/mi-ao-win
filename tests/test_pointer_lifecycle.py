"""No real mouse injection: direction changes must retire their own worker."""
from miao.executor import WindowsExecutor


def test_direction_change_never_revives_previous_worker(monkeypatch):
    workers = []
    class Worker:
        def __init__(self, target, args, **kwargs):
            self.stop = args[0]
            workers.append(self)
        def start(self):
            pass
    monkeypatch.setattr('miao.executor.threading.Thread', Worker)
    executor = WindowsExecutor()
    executor.start_pointer_move(-1, 0)
    executor.start_pointer_move(1, 0)
    assert len(workers) == 2
    assert workers[0].stop.is_set()
    assert not workers[1].stop.is_set()
    executor.stop_pointer_move()
    assert all(worker.stop.is_set() for worker in workers)
