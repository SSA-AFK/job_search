"""Safe loading for the reviewed Gate 1 source registry."""

from pathlib import Path

from pydantic import ValidationError

from app.manifest.contracts import SourceRegistry


class SourceRegistryError(ValueError):
    """Raised when the checked-in registry cannot be safely used."""


def load_source_registry(path: Path) -> SourceRegistry:
    """Load a registry without exposing untrusted file contents in errors."""

    try:
        return SourceRegistry.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        raise SourceRegistryError("source registry is invalid") from error
