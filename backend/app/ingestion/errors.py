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


class RunClaimError(Exception):
    """Sanitized claim failure carrying only this worker's attempted token."""

    def __init__(self, *, claim_token: str) -> None:
        self.claim_token = claim_token
        super().__init__("run claim failed")


class RetryableInfrastructureError(Exception):
    """Sanitized signal for failures that require Celery redelivery."""

    def __init__(self, *, claim_token: str | None = None) -> None:
        self.claim_token = claim_token
        super().__init__("retryable infrastructure failure")
