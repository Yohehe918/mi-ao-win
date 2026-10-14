"""重连与协商策略测试（移植自 DeviceConnectionPolicy.swift 语义）。"""

from miao.connection_policy import (
    CapabilityNegotiationDecision,
    CapabilityNegotiationPolicy,
    ReconnectBackoff,
    VoiceConnectionMode,
    VoiceReconnectPolicy,
    VoiceReconnectRetry,
    VoiceSleep,
)


def test_reconnect_backoff_doubles_and_caps():
    backoff = ReconnectBackoff(initial_delay=1, maximum_delay=60)
    delays = [backoff.next_delay() for _ in range(8)]
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60]


def test_reconnect_backoff_reset():
    backoff = ReconnectBackoff()
    backoff.next_delay()
    backoff.next_delay()
    backoff.reset()
    assert backoff.attempt == 0
    assert backoff.next_delay() == 1


def test_voice_reconnect_always_ready_keeps_retrying():
    policy = VoiceReconnectPolicy(VoiceConnectionMode.ALWAYS_READY, maximum_delay=60)
    for _ in range(5):
        decision = policy.next_decision()
        assert isinstance(decision, VoiceReconnectRetry)


def test_voice_reconnect_smart_sleep_sleeps_after_two_attempts():
    policy = VoiceReconnectPolicy(VoiceConnectionMode.SMART_SLEEP, maximum_smart_sleep_attempts=2)
    assert isinstance(policy.next_decision(), VoiceReconnectRetry)
    assert isinstance(policy.next_decision(), VoiceReconnectRetry)
    assert policy.next_decision() is VoiceSleep.SLEEP


def test_capability_negotiation_retries_then_reconnects():
    policy = CapabilityNegotiationPolicy()
    assert policy.retry_delay == 1.5
    assert policy.decision_after(1) is CapabilityNegotiationDecision.RETRY
    assert policy.decision_after(2) is CapabilityNegotiationDecision.RETRY
    assert policy.decision_after(3) is CapabilityNegotiationDecision.RECONNECT
