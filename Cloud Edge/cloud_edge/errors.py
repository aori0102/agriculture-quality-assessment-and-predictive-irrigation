"""Public errors that never embed request URLs or credentials."""


class CloudError(Exception):
    """A Cloud transport or lifecycle failure with safe diagnostic metadata.

    category is a machine-readable failure label; status_code is the HTTP
    status when available, otherwise None. str(error) is a sanitized message.
    Uploads raise this exception; background MQTT failures are exposed through
    CloudClient.last_command_error. Invalid caller arguments use ValueError.
    """

    def __init__(self, message: str, *, category: str, status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
