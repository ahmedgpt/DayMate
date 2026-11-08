"""Domain errors. `code` is machine-readable; `message` is safe to show the user."""


class DayMateError(Exception):
    code = "error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(DayMateError):
    code = "validation_error"


class NotFoundError(DayMateError):
    code = "not_found"
