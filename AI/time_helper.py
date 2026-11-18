from datetime import datetime, timezone, timedelta


class TimeHelper:
    @staticmethod
    def process_time(raw: datetime | str, *, require_timezone: bool = False) -> datetime | None:
        """Normalize an ISO timestamp to UTC; legacy naive values mean UTC.

        External telemetry must set require_timezone=True so a missing offset
        is rejected rather than interpreted using the host's local timezone.
        """
        try:
            value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
            if value.tzinfo is None or value.utcoffset() is None:
                if require_timezone:
                    return None
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def format_time(value: datetime | str) -> str:
        """Return YYYY-MM-DDTHH:MM:SS+00:00, without fractional seconds."""
        timestamp = TimeHelper.process_time(value)
        if timestamp is None:
            raise ValueError("Invalid timestamp")
        return timestamp.isoformat(timespec="seconds")

    @staticmethod
    def floor_to_nearest_ten_minutes(datetime: datetime) -> datetime:
        """Convert to UTC before flooring to a 10-minute window."""
        value = TimeHelper.process_time(datetime)
        if value is None:
            raise ValueError("Invalid timestamp")
        return value.replace(minute=(value.minute // 10) * 10, second=0, microsecond=0)

    @staticmethod
    def get_slot(raw: datetime | str, *, offset_windows: int = 0) -> datetime | None:
        """Parse a UTC 10-minute window; return None for invalid timestamps.

        offset_windows=-1 selects the immediately preceding window.
        """
        value = TimeHelper.process_time(raw)
        if value is None or value != value:
            return None
        try:
            return TimeHelper.floor_to_nearest_ten_minutes(value) + timedelta(
                minutes=10 * offset_windows
            )
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def get_current_slot() -> datetime:
        return TimeHelper.floor_to_nearest_ten_minutes(datetime.now(timezone.utc))

    @staticmethod
    def get_current_timestamp_floor() -> str:
        return TimeHelper.format_time(TimeHelper.get_current_slot())

    @staticmethod
    def get_current_timestamp() -> str:
        return TimeHelper.format_time(datetime.now(timezone.utc))

    @staticmethod
    def get_current_minute() -> int:
        return datetime.now(timezone.utc).minute

    @staticmethod
    def is_window_expired(raw: datetime | str, now: datetime | str) -> bool:
        """A window expires when a later window starts."""
        slot = TimeHelper.get_slot(raw)
        current = TimeHelper.process_time(now)
        if slot is None or current is None:
            raise ValueError("Invalid timestamp when checking window expiry")
        return slot < TimeHelper.get_slot(current)
