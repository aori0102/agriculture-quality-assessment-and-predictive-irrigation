from datetime import datetime, timezone

class TimeHelper:
    @staticmethod
    def floor_to_nearest_ten_minutes(datetime: datetime) -> datetime:
        """
        Floor the given datetime to the nearest 10-minute mark.
        
        Args:
            datetime (datetime): The datetime object to be floored.
        
        Returns:
            datetime: A new datetime object floored to the nearest 10-minute mark.
        """
        return datetime.replace(
            minute=(datetime.minute // 10) * 10,
            second=0,
            microsecond=0
        )
        
        
    @staticmethod
    def process_time(raw: str) -> datetime | None:
        """
        Convert a raw timestamp string into a datetime object. The raw timestamp is 
        expected to be in ISO 8601 format. If the conversion fails, the function will 
        print an error message and return None.
        """
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            print(f"Invalid timestamp format: {raw}")
            return None
    
    
    @staticmethod
    def get_current_slot() -> datetime:
        """Return the start of the current 10-minute slot in UTC."""
        return TimeHelper.floor_to_nearest_ten_minutes(datetime.now(timezone.utc))

    @staticmethod
    def get_current_timestamp() -> str:
        """
        Get the current UTC slot in ISO 8601 format.

        Returns:
            str: Current timestamp in ISO 8601 format.
        """
        return TimeHelper.get_current_slot().isoformat(timespec="seconds")
    
    
    @staticmethod
    def get_current_minute() -> int:
        return datetime.now(timezone.utc).minute