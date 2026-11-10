from datetime import datetime

class TimeHelper:
    @staticmethod
    def get_current_timestamp() -> str:
        """
        Get the current timestamp in ISO 8601 format with timezone information.

        Returns:
            str: Current timestamp in ISO 8601 format.
        """
        time = datetime.now().astimezone()
        time = time.replace(
            minute=(time.minute // 10) * 10,
            second=0,
            microsecond=0
        )
        return time.isoformat(timespec="seconds")