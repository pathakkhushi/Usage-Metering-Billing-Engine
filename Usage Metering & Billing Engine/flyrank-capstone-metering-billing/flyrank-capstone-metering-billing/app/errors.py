class ApiError(Exception):
    """Business error that maps to a clean 4xx with a machine-readable code."""

    def __init__(self, status: int, code: str, message: str, headers: dict | None = None, **extra):
        self.status, self.code, self.message = status, code, message
        self.headers, self.extra = headers or {}, extra
        super().__init__(message)
