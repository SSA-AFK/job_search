"""Stable errors raised by ingestion providers."""


class ProviderError(Exception):
    def __init__(self, *, code: str, retryable: bool, detail: str | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        super().__init__(code)


class ExtractionError(Exception):
    def __init__(self, *, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class RetryableInfrastructureError(Exception):
    """Sanitized signal for failures that require Celery redelivery."""
