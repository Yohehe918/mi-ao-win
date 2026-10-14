"""Native speech routing without calling Doubao or injecting physical input."""
from types import SimpleNamespace
import threading

from miao.app import MiAoApp
from miao.config import Config
from miao.doubao_voice import DoubaoVoiceController
from miao.executor import WindowsExecutor


class FakeAPI:
    def __init__(self, fail=None):
        self.calls = []
        self.threads = []
        self.fail = fail

    def send(self, message, mode=0):
        self.calls.append((message, mode))
        self.threads.append(threading.get_ident())
        if message == self.fail:
            raise RuntimeError("test error")


def test_native_worker_orders_modes_deduplicates_and_stops_on_close():
    api = FakeAPI()
    controller = DoubaoVoiceController(lambda: api)
    controller.start_voice("hold")
    controller.start_voice("hold")
    controller.start_voice("handsfree")
    controller.close()
    assert api.calls == [(1007, 0), (1012, 0), (1008, 0), (1007, 0), (1012, 1), (1008, 0)]
    assert set(api.threads) != {threading.get_ident()}
    assert len(set(api.threads)) == 1
    controller.start_voice("hold")
    assert len(api.calls) == 6


def test_failed_show_is_stopped_and_reported():
    api = FakeAPI(fail=1012)
    errors = []
    controller = DoubaoVoiceController(lambda: api, errors.append)
    controller.start_voice("hold")
    assert controller.flush()
    controller.close()
    assert api.calls == [(1007, 0), (1012, 0), (1008, 0)]
    assert errors == ["test error"]


def test_speech_stop_waits_for_audio_tail_on_the_worker():
    order = []
    class API:
        def send(self, message, mode=0):
            order.append(message)
    controller = DoubaoVoiceController(API, before_stop=lambda: order.append('drain'))
    controller.start_voice('hold')
    controller.stop_voice()
    assert controller.flush()
    controller.close()
    assert order == [1007, 1012, 'drain', 1008]


def test_shortcut_bridge_uses_original_release_and_leaves_other_shortcuts(monkeypatch):
    app = MiAoApp.__new__(MiAoApp)
    app.config = Config(doubao_hotkey_vk=66, doubao_hotkey_modifiers=["right_alt"])
    app._native_doubao_keys = None
    app._native_handsfree = False
    app._handsfree_requested = False
    calls = []
    app._doubao_native = SimpleNamespace(start_voice=lambda mode:calls.append(mode), stop_voice=lambda:calls.append("stop"))
    sent = []
    monkeypatch.setattr("miao.executor._send", sent.append)
    executor = WindowsExecutor(shortcut_bridge=app._bridge_voice_shortcut)
    executor.keys_down(66, (165,))
    app.config.doubao_hotkey_vk = 67
    app.config.doubao_native_control = False
    executor.keys_up(66, (165,))
    assert calls == ["hold", "stop"] and not sent
    executor.press_shortcut(65, (17,))
    assert len(sent) == 1 and len(sent[0]) == 4
    app.config.doubao_native_control = True
    executor.press_shortcut(32, (165,))
    app._handsfree_active = True
    app._finish_handsfree()
    assert calls[-2:] == ["handsfree", "stop"]
    assert len(sent) == 1


def test_voice_release_does_not_stop_tv_session():
    app = MiAoApp.__new__(MiAoApp)
    app._native_doubao_keys = (66, (165,))
    app._native_handsfree = True
    app._doubao_native = SimpleNamespace(stop_voice=lambda: (_ for _ in ()).throw(AssertionError("stopped TV")))
    assert app._bridge_voice_shortcut("up", 66, (165,))
    assert app._native_doubao_keys is None
