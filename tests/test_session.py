"""语音会话状态机测试（纯字节驱动，无需 BLE 硬件）。

模拟固件 2671 的完整 v1.0 流程：
协商 → START_SEARCH → MIC_OPEN → AUDIO_START → 音频帧 → AUDIO_STOP。
"""

from miao.atvv import GET_CAPABILITIES_COMMAND
from miao.session import (
    AudioCompleted,
    SendCommand,
    SessionState,
    StateChanged,
    VoiceSession,
)

CAPS_V10 = bytes([0x0B, 0x01, 0x00, 0x03, 0x03, 0x00, 0x78])
START_SEARCH = bytes([0x08])
AUDIO_START = bytes([0x04, 0x00, 0x02, 0x07])  # codec=16k, streamID=7
AUDIO_STOP = bytes([0x00, 0x00])


def _drive_to_streaming(session: VoiceSession):
    actions = session.on_connected()
    assert session.state is SessionState.NEGOTIATING
    assert SendCommand(GET_CAPABILITIES_COMMAND) in actions

    session.handle_control(CAPS_V10)
    assert session.state is SessionState.READY

    actions = session.handle_control(START_SEARCH)
    assert session.state is SessionState.OPENING
    assert SendCommand(bytes([0x0C, 0x00])) in actions  # v1.0 MIC_OPEN

    actions = session.handle_control(AUDIO_START)
    assert session.state is SessionState.STREAMING
    assert any(isinstance(a, StateChanged) and "16 kHz" in a.detail for a in actions)


def test_full_v10_voice_cycle():
    session = VoiceSession()
    _drive_to_streaming(session)

    session.handle_audio(bytes(120))
    session.handle_audio(bytes(120))
    actions = session.handle_control(AUDIO_STOP)

    assert session.state is SessionState.READY
    assert not any(isinstance(a, SendCommand) for a in actions)  # Remote already closed.
    completed = [a for a in actions if isinstance(a, AudioCompleted)]
    assert len(completed) == 1
    assert completed[0].reason == "remote-release"
    assert completed[0].sample_rate == 16_000
    assert len(completed[0].samples) == 480  # 两帧 × 240 样本


def test_second_start_search_ends_recording():
    session = VoiceSession()
    _drive_to_streaming(session)
    session.handle_audio(bytes(120))
    actions = session.handle_control(START_SEARCH)

    assert session.state is SessionState.READY
    completed = [a for a in actions if isinstance(a, AudioCompleted)]
    assert completed and completed[0].reason == "second-press"


def test_silence_timeout_finalizes_recording():
    clock = [1000.0]
    session = VoiceSession(silence_timeout=1.5, silence_threshold=35.0, now=lambda: clock[0])
    _drive_to_streaming(session)
    session.handle_audio(bytes(120))  # 全零帧 RMS=0，无声

    clock[0] += 1.0
    assert session.tick() == []
    clock[0] += 0.6
    actions = session.tick()

    completed = [a for a in actions if isinstance(a, AudioCompleted)]
    assert completed and completed[0].reason == "silence"
    assert session.state is SessionState.READY


def test_speech_resets_silence_clock():
    clock = [1000.0]
    session = VoiceSession(silence_timeout=1.5, silence_threshold=35.0, now=lambda: clock[0])
    _drive_to_streaming(session)
    session.handle_audio(bytes(120))  # 无声
    clock[0] += 1.2
    session.handle_audio(bytes([0x77] * 120))  # 非零帧，RMS 超阈值
    clock[0] += 1.2
    assert session.tick() == []  # 有声帧刷新了计时，不触发静音收口


def test_keep_alive_only_during_recording():
    clock = [1000.0]
    session = VoiceSession(silence_timeout=0, now=lambda: clock[0])
    session.on_connected()
    session.handle_control(CAPS_V10)
    assert session.tick() == []  # READY 状态不发 keep-alive

    session.handle_control(START_SEARCH)
    session.handle_control(AUDIO_START)
    clock[0] += 10.0
    actions = session.tick()
    assert SendCommand(bytes([0x0E, 0x07])) in actions  # v1.0 keep-alive + streamID


def test_negotiation_retries_then_disconnects():
    session = VoiceSession()
    session.on_connected()

    actions = session.negotiation_timed_out()  # 第 1 次超时 → 重发
    assert SendCommand(GET_CAPABILITIES_COMMAND) in actions
    actions = session.negotiation_timed_out()  # 第 2 次超时 → 重发
    assert SendCommand(GET_CAPABILITIES_COMMAND) in actions
    actions = session.negotiation_timed_out()  # 第 3 次 → 断开重连
    assert session.state is SessionState.DISCONNECTED
    assert any(isinstance(a, StateChanged) and "协商超时" in a.detail for a in actions)


def test_frame_drop_detected():
    from miao.atvv import AudioSync
    from miao.session import FrameDropped

    session = VoiceSession()
    _drive_to_streaming(session)
    session.handle_control(bytes([0x0A, 0x02, 0x00, 0x0A, 0x00, 0x00, 0x00]))  # sync seq=10
    session.handle_audio(bytes(120))  # seq 10
    actions = session.handle_audio(bytes(120))  # seq 11，连续
    assert not [a for a in actions if isinstance(a, FrameDropped)]

    session.handle_control(bytes([0x0A, 0x02, 0x00, 0x14, 0x00, 0x00, 0x00]))  # 重新同步 seq=20
    session.handle_audio(bytes(120))  # seq 20
    actions = session.handle_audio(bytes(120))  # seq 21
    assert not [a for a in actions if isinstance(a, FrameDropped)]


def test_physical_hold_does_not_end_on_a_quiet_pause():
    clock = [1000.0]
    session = VoiceSession(now=lambda: clock[0])
    session.on_connected()
    session.handle_control(CAPS_V10)
    session.handle_control(bytes([0x04, 0x03, 0x02, 0x07]))
    session.handle_audio(bytes(120))
    clock[0] += 3.0
    assert session.tick() == []
    assert session.state is SessionState.STREAMING
    session.handle_control(bytes([0x00, 0x02]))
    assert session.state is SessionState.READY


def test_new_utterance_resets_decoder_and_sequence():
    session = VoiceSession(silence_timeout=0)
    session.on_connected()
    session.handle_control(CAPS_V10)
    session.handle_control(AUDIO_START)
    session.handle_audio(bytes([0x77] * 120))  # Leave a large predictor/index.
    session.handle_control(AUDIO_STOP)
    session.handle_control(AUDIO_START)
    session.handle_audio(bytes(120))
    completed = [a for a in session.handle_control(AUDIO_STOP) if isinstance(a, AudioCompleted)]
    assert completed[0].samples == [0] * 240
    assert session._last_sequence == 0
