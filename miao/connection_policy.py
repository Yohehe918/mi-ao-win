"""设备选择与重连策略。

移植自 mi-ao 的 DeviceConnectionPolicy.swift。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class VoiceConnectionMode(enum.Enum):
    ALWAYS_READY = "always_ready"  # 随时就绪
    SMART_SLEEP = "smart_sleep"  # 智能休眠


@dataclass(frozen=True)
class BLEDeviceCandidate:
    identifier: str
    name: str | None
    rssi: int
    advertises_atvv: bool

    def matches_name(self, name_filter: str | None) -> bool:
        if name_filter is None or not name_filter.strip():
            return False
        return self.name is not None and name_filter.strip().casefold() in self.name.casefold()


@dataclass(frozen=True)
class BLEDeviceSelectionPolicy:
    preferred_identifier: str | None = None
    name_filter: str | None = None

    def accepts(self, candidate: BLEDeviceCandidate) -> bool:
        if self.preferred_identifier is not None:
            return candidate.identifier == self.preferred_identifier
        return candidate.advertises_atvv or candidate.matches_name(self.name_filter)

    def best_candidate(self, candidates: list[BLEDeviceCandidate]) -> BLEDeviceCandidate | None:
        accepted = [c for c in candidates if self.accepts(c)]
        if not accepted:
            return None
        return sorted(accepted, key=self._rank_key)[0]

    def _rank_key(self, c: BLEDeviceCandidate):
        preferred = (
            self.preferred_identifier is not None and c.identifier == self.preferred_identifier
        )
        return (
            not preferred,
            not c.matches_name(self.name_filter),
            not c.advertises_atvv,
            -c.rssi,
            c.identifier,
        )


class ReconnectBackoff:
    """指数退避：1→2→4→…→maximumDelay（默认 60 秒）。"""

    def __init__(self, initial_delay: float = 1.0, maximum_delay: float = 20.0) -> None:
        self.initial_delay = initial_delay
        self.maximum_delay = maximum_delay
        self.attempt = 0

    def next_delay(self) -> float:
        exponent = min(self.attempt, 8)
        delay = min(self.maximum_delay, self.initial_delay * 2**exponent)
        self.attempt += 1
        return delay

    def reset(self) -> None:
        self.attempt = 0


@dataclass(frozen=True)
class VoiceReconnectRetry:
    attempt: int
    delay: float


class VoiceSleep(enum.Enum):
    SLEEP = "sleep"


VoiceReconnectDecision = VoiceReconnectRetry | VoiceSleep


class VoiceReconnectPolicy:
    def __init__(
        self,
        mode: VoiceConnectionMode,
        maximum_smart_sleep_attempts: int = 2,
        initial_delay: float = 1.0,
        maximum_delay: float = 60.0,
    ) -> None:
        self.mode = mode
        self.maximum_smart_sleep_attempts = max(0, maximum_smart_sleep_attempts)
        self._backoff = ReconnectBackoff(initial_delay, maximum_delay)

    def next_decision(self) -> VoiceReconnectDecision:
        if self.mode is VoiceConnectionMode.SMART_SLEEP and (
            self._backoff.attempt >= self.maximum_smart_sleep_attempts
        ):
            return VoiceSleep.SLEEP
        delay = self._backoff.next_delay()
        return VoiceReconnectRetry(attempt=self._backoff.attempt, delay=delay)

    def update_mode(self, mode: VoiceConnectionMode) -> None:
        self.mode = mode
        self.reset()

    def reset(self) -> None:
        self._backoff.reset()


class CapabilityNegotiationDecision(enum.Enum):
    RETRY = "retry"
    RECONNECT = "reconnect"


@dataclass(frozen=True)
class CapabilityNegotiationPolicy:
    """GET_CAPS 无响应时重试，最多 maximum_requests 次后断开重连。"""

    retry_delay: float = 1.5
    maximum_requests: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "retry_delay", max(0.1, self.retry_delay))
        object.__setattr__(self, "maximum_requests", max(1, self.maximum_requests))

    def decision_after(self, request_count: int) -> CapabilityNegotiationDecision:
        if request_count < self.maximum_requests:
            return CapabilityNegotiationDecision.RETRY
        return CapabilityNegotiationDecision.RECONNECT
