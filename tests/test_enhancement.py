"""Integrated worker control: no UAC, driver loading, or real key injection."""
import json
import socket
import threading
import time

from miao.enhancement import EnhancementManager, ProbeWorker, _read, _send, run_worker_channel


def wait_for(predicate):
    limit = time.monotonic() + 2
    while not predicate() and time.monotonic() < limit:
        time.sleep(.005)
    assert predicate()


def test_authenticated_control_pause_and_exit():
    launched = []
    manager = EnhancementManager(launcher=launched.append)
    manager.start()
    client = socket.create_connection(manager._server.getsockname(), timeout=2)
    stream = client.makefile('rb')
    try:
        _send(client, {'type': 'hello', 'token': manager.token, 'parent_pid': manager.parent_pid})
        assert not _read(stream)['enabled']
        manager.set_enabled(True)
        _send(client, {'type': 'status', 'state': 'running', 'detail': 'ready'})
        command = _read(stream)
        assert command['enabled'] and command['generation'] == 1
        assert launched == []  # Existing elevated worker is reused.
        manager.set_enabled(False)
        _send(client, {'type': 'status', 'state': 'paused', 'detail': 'paused'})
        assert not _read(stream)['enabled']
        manager.stop()
        assert stream.readline() == b''
    finally:
        stream.close()
        client.close()
        manager.stop()


def test_wrong_control_token_or_parent_cannot_connect():
    manager = EnhancementManager()
    manager.start()
    try:
        assert not manager._authorized({'type': 'hello', 'token': '0'*64, 'parent_pid': manager.parent_pid})
        assert not manager._authorized({'type': 'hello', 'token': 'é'*64, 'parent_pid': manager.parent_pid})
        assert not manager._authorized({'type': 'hello', 'token': manager.token, 'parent_pid': -1})
        with socket.create_connection(manager._server.getsockname(), timeout=2) as client:
            _send(client, {'type': 'hello', 'token': '0'*64, 'parent_pid': manager.parent_pid})
            assert client.recv(1) == b''
        assert manager._client is None
    finally:
        manager.stop()


def test_enable_uses_same_application_worker_and_cancel_reports_error():
    launched = []
    def cancel(args):
        launched.extend(args)
        raise RuntimeError('cancelled')
    manager = EnhancementManager(launcher=cancel)
    try:
        manager.set_enabled(True)
        wait_for(lambda: manager.state == 'error')
        assert launched[0] == '--enhanced-worker'
        assert manager.detail == 'cancelled'
        assert not manager._launching
    finally:
        manager.stop()


def test_worker_pauses_during_start_without_blocking_heartbeat():
    starting, release = threading.Event(), threading.Event()
    probes = []
    class Probe:
        def __init__(self, emit):
            self.emit, self.stopped = emit, False
            probes.append(self)
        def start(self):
            starting.set()
            release.wait(2)
            self.emit({'event': 'armed'})
        def stop(self): self.stopped = True
    worker = ProbeWorker(Probe)
    worker.apply({'enabled': True, 'generation': 1})
    assert starting.wait(1)
    before = time.monotonic()
    worker.apply({'enabled': False, 'generation': 2})
    assert time.monotonic() - before < .1
    assert worker.status()['state'] == 'pausing'
    release.set()
    worker.starter.join(1)
    worker.apply({'enabled': False, 'generation': 2})
    assert worker.status()['state'] == 'paused'
    assert probes[0].stopped
    worker.apply({'enabled': True, 'generation': 3})
    worker.starter.join(1)
    assert worker.status()['state'] == 'running'
    worker.close()
    assert all(probe.stopped for probe in probes)


def test_worker_failure_is_not_retried_without_a_new_request():
    probes = []
    class Probe:
        def __init__(self, emit): probes.append(self)
        def start(self): raise RuntimeError('not ready')
        def stop(self): pass
    worker = ProbeWorker(Probe)
    command = {'enabled': True, 'generation': 1}
    worker.apply(command)
    worker.starter.join(1)
    assert worker.status()['state'] == 'error'
    worker.apply(command)
    assert len(probes) == 1
    worker.close()


def test_headless_channel_pause_resume_and_app_exit_clean_up():
    probes = []
    class Probe:
        def __init__(self, emit):
            self.emit, self.stopped = emit, False
            probes.append(self)
        def start(self): self.emit({'event': 'armed'})
        def stop(self): self.stopped = True
    manager = EnhancementManager(launcher=lambda _: None)
    manager.start()
    worker = ProbeWorker(Probe)
    thread = threading.Thread(target=run_worker_channel, args=(manager.parent_pid,
        manager._server.getsockname()[1], manager.token, worker, lambda: True, .01))
    thread.start()
    try:
        wait_for(lambda: manager._client is not None)
        manager.set_enabled(True)
        wait_for(lambda: manager.state == 'running')
        manager.set_enabled(False)
        wait_for(lambda: manager.state == 'paused')
        assert probes[0].stopped
        manager.set_enabled(True)
        wait_for(lambda: manager.state == 'running')
        manager.stop()
        thread.join(2)
        assert not thread.is_alive()
        assert len(probes) == 2 and all(p.stopped for p in probes)
    finally:
        manager.stop()
        thread.join(2)
