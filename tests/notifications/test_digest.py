"""Tests for DigestAccumulator and seconds_until_next_send."""

from datetime import datetime, timezone

from src.notifications.base import SyncCompleteEvent
from src.notifications.digest import DigestAccumulator, seconds_until_next_send


def _sync_event(trusted: int = 1, cycle: int = 1) -> SyncCompleteEvent:
    return SyncCompleteEvent(
        total_untrusted=5, total_trusted=trusted, total_skipped=0,
        total_no_match=4, total_errors=0,
        provider_names=("ninjaone",), cycle_number=cycle,
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestDigestAccumulator:
    def test_starts_empty(self) -> None:
        assert DigestAccumulator().pending_count == 0

    def test_add_increments_count(self) -> None:
        acc = DigestAccumulator()
        acc.add(_sync_event())
        assert acc.pending_count == 1

    def test_flush_returns_events_and_clears(self) -> None:
        acc = DigestAccumulator()
        acc.add(_sync_event(trusted=2, cycle=1))
        acc.add(_sync_event(trusted=3, cycle=2))
        flushed = acc.flush()
        assert len(flushed) == 2
        assert acc.pending_count == 0

    def test_flush_empty_returns_empty_list(self) -> None:
        assert DigestAccumulator().flush() == []

    def test_second_flush_only_returns_new_events(self) -> None:
        acc = DigestAccumulator()
        acc.add(_sync_event(cycle=1))
        acc.flush()
        acc.add(_sync_event(cycle=2))
        second = acc.flush()
        assert len(second) == 1
        assert second[0].cycle_number == 2


class TestSecondsUntilNextSend:
    def test_returns_positive_and_within_one_day(self) -> None:
        s = seconds_until_next_send("08:00", "UTC")
        assert 0 < s <= 86400

    def test_late_schedule_within_one_day(self) -> None:
        s = seconds_until_next_send("23:59", "UTC")
        assert 0 < s <= 86400

    def test_invalid_tz_falls_back_to_utc(self) -> None:
        s = seconds_until_next_send("08:00", "Invalid/Zone")
        assert s > 0
