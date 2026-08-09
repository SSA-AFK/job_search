"""Offline-by-default operator commands for the Gate 1 manifest workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO, NoReturn
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.company_identity.cli import (
    company_identity_audit_payload,
    identity_review_apply_payload,
    identity_review_export_payload,
)
from app.company_identity.contracts import IdentityReviewDecisionInput
from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
from app.company_identity.service import (
    IdentityOwnerChanged,
    IdentityReviewConflict,
    IdentitySearchUnavailable,
)
from app.manifest.candidates import (
    CandidateEvidenceConflict,
    UnregisteredSourceError,
    import_candidate_facts,
)
from app.manifest.contracts import (
    CandidateFactInput,
    DiscoveryStatus,
    EntryDiscoveryResult,
    ManifestCompany,
    RecordDiscoveryCommand,
    ReviewDecisionInput,
    SourceRegistry,
    SourceRole,
)
from app.manifest.identity import (
    ReviewDecisionConflict,
    apply_review_decisions,
    auto_resolve_candidates,
    export_review_queue,
)
from app.manifest.models import (
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)
from app.manifest.registry import SourceRegistryError, load_source_registry
from app.manifest.reporting import ManifestReportError, ManifestReportService
from app.manifest.service import (
    DiscoveryRecordConflict,
    ManifestFreezeError,
    freeze_manifest,
    is_retryable_discovery_observation,
    record_discovery_result,
    transition_retryable_discovery_result,
)

_MAX_INPUT_BYTES = 16 * 1024 * 1024
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class ManifestCommandError(ValueError):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class _DiscoveryState:
    observed_company_ids: frozenset[UUID]
    terminal_company_ids: set[UUID]
    retryable_observation_ids: dict[UUID, UUID]
    stopped_domains: set[str]
    stopped_source_ids: set[str]
    consecutive_rate_limits: Counter[str]


@dataclass(frozen=True)
class _ExternalOutputPath:
    path: Path
    parent_identity: tuple[int, int]


@dataclass(frozen=True)
class _PinnedDirectoryHandle:
    current_path: Callable[[], Path]
    identity: tuple[int, int]
    native_handle: int


@dataclass(frozen=True)
class _AtomicWriteHooks:
    after_parent_pinned: Callable[[], None] | None = None
    before_replace: Callable[[], None] | None = None
    before_cleanup: Callable[[], None] | None = None


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        print("manifest command failed: invalid arguments", file=sys.stderr)
        raise SystemExit(2)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Operate the Gate 1 manifest workflow")
    commands = parser.add_subparsers(dest="command", required=True)

    registry = commands.add_parser("registry-check")
    registry.add_argument("--registry", type=Path)

    candidate_import = commands.add_parser("candidate-import")
    candidate_import.add_argument("candidate_path", type=Path)
    candidate_import.add_argument("--registry", type=Path)

    review_export = commands.add_parser("review-export")
    review_export.add_argument("output", type=Path)

    review_apply = commands.add_parser("review-apply")
    review_apply.add_argument("decisions", type=Path)

    identity_review_export = commands.add_parser("identity-review-export")
    identity_review_export.add_argument("output", type=Path)

    identity_review_apply = commands.add_parser("identity-review-apply")
    identity_review_apply.add_argument("decisions", type=Path)

    company_identity_audit = commands.add_parser("company-identity-audit")
    company_identity_audit.add_argument("output", type=Path)

    manifest_freeze = commands.add_parser("manifest-freeze")
    manifest_freeze.add_argument("--manifest-out", type=Path, required=True)
    manifest_freeze.add_argument("--quota-out", type=Path, required=True)
    manifest_freeze.add_argument("--config-fingerprint")
    manifest_freeze.add_argument("--registry", type=Path)

    discover = commands.add_parser("discover")
    _add_manifest_selector(discover)
    discover.add_argument("--limit", type=_positive_integer)
    discover.add_argument("--resume", action="store_true")
    discover.add_argument("--live", action="store_true")
    discover.add_argument("--registry", type=Path)

    report = commands.add_parser("report")
    _add_manifest_selector(report)
    report.add_argument("--code-commit")
    report.add_argument("--config-fingerprint")
    report.add_argument("--output", type=Path)
    report.add_argument("--format", choices=("json",), default="json")
    return parser


def _add_manifest_selector(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--manifest")
    selector.add_argument("--manifest-file", type=Path)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (Enum, UUID)):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError("value is not JSON serializable")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _print_json(value: dict[str, object]) -> None:
    print(_json_bytes(value).decode("utf-8"))


def _file_identity_from_handle(file: BinaryIO) -> tuple[int, int]:
    status = os.fstat(file.fileno())
    return status.st_dev, status.st_ino


def _file_identity_from_path(path: Path) -> tuple[int, int]:
    status = path.stat()
    return status.st_dev, status.st_ino


def _descriptor_path(descriptor: int) -> Path:
    for descriptor_root in (Path("/proc/self/fd"), Path("/dev/fd")):
        descriptor_path = descriptor_root / str(descriptor)
        try:
            return descriptor_path.resolve(strict=True)
        except OSError:
            continue
    raise OSError("final file path is unavailable")


def _windows_handle_path(handle: int) -> Path:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    required = get_final_path(handle, None, 0, 0)
    if required == 0:
        raise OSError(ctypes.get_last_error(), "final file path is unavailable")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        raise OSError(ctypes.get_last_error(), "final file path is unavailable")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value).resolve(strict=True)


def _windows_handle_identity(handle: int) -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        raise OSError(ctypes.get_last_error(), "file identity is unavailable")
    file_index = information.file_index_high << 32 | information.file_index_low
    return information.volume_serial_number, file_index


def _opened_file_path(file: BinaryIO) -> Path:
    if os.name == "nt":
        import msvcrt

        return _windows_handle_path(msvcrt.get_osfhandle(file.fileno()))
    return _descriptor_path(file.fileno())


@contextmanager
def _pinned_directory(
    path: Path,
) -> Iterator[_PinnedDirectoryHandle]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x80,
            0x1 | 0x2,
            None,
            3,
            0x02000000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "parent directory is unavailable")
        try:
            yield _PinnedDirectoryHandle(
                current_path=lambda: _windows_handle_path(handle),
                identity=_windows_handle_identity(handle),
                native_handle=handle,
            )
        finally:
            _close_windows_handle(handle)
        return

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        status = os.fstat(descriptor)
        yield _PinnedDirectoryHandle(
            current_path=lambda: _descriptor_path(descriptor),
            identity=(status.st_dev, status.st_ino),
            native_handle=descriptor,
        )
    finally:
        os.close(descriptor)


def _directory_identity(path: Path) -> tuple[int, int]:
    with _pinned_directory(path) as directory:
        return directory.identity


def _is_repository_path(path: Path) -> bool:
    return path.is_relative_to(_REPOSITORY_ROOT)


def _windows_create_relative_file(parent_handle: int, name: str) -> int:
    import ctypes
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        )

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(_UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_quality_of_service", wintypes.LPVOID),
        )

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = (("status", wintypes.LPVOID), ("information", ctypes.c_size_t))

    name_buffer = ctypes.create_unicode_buffer(name)
    name_bytes = len(name.encode("utf-16-le"))
    unicode_name = _UnicodeString(
        length=name_bytes,
        maximum_length=ctypes.sizeof(name_buffer),
        buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = _ObjectAttributes(
        length=ctypes.sizeof(_ObjectAttributes),
        root_directory=parent_handle,
        object_name=ctypes.pointer(unicode_name),
        attributes=0x40,
        security_descriptor=None,
        security_quality_of_service=None,
    )
    status_block = _IoStatusBlock()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    create_file.restype = wintypes.LONG
    status = create_file(
        ctypes.byref(handle),
        0x40000000 | 0x00010000 | 0x00100000 | 0x80,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x80,
        0x1 | 0x2 | 0x4,
        2,
        0x40 | 0x20,
        None,
        0,
    )
    if status < 0 or handle.value is None:
        raise OSError(f"native exclusive create failed: 0x{status & 0xFFFFFFFF:08x}")
    return handle.value


def _windows_write_handle(handle: int, content: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    write_file.restype = wintypes.BOOL
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + 1024 * 1024]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not write_file(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise OSError(ctypes.get_last_error(), "artifact write failed")
        if written.value != len(chunk):
            raise OSError("artifact write was incomplete")
        offset += written.value
    flush_file = kernel32.FlushFileBuffers
    flush_file.argtypes = (wintypes.HANDLE,)
    flush_file.restype = wintypes.BOOL
    if not flush_file(handle):
        raise OSError(ctypes.get_last_error(), "artifact flush failed")


def _windows_path_identity(path: Path) -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x80, 0x1 | 0x2 | 0x4, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "file identity is unavailable")
    try:
        return _windows_handle_identity(handle)
    finally:
        _close_windows_handle(handle)


def _windows_rename_handle(
    handle: int,
    parent_handle: int,
    destination_name: str,
) -> None:
    import ctypes
    from ctypes import wintypes

    encoded_name = destination_name.encode("utf-16-le")
    name_code_units = len(encoded_name) // ctypes.sizeof(wintypes.WCHAR)

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = (("status", wintypes.LPVOID), ("information", ctypes.c_size_t))

    class _FileRenameInformation(ctypes.Structure):
        _fields_ = (
            ("replace_if_exists", wintypes.BOOLEAN),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * name_code_units),
        )

    information = _FileRenameInformation()
    information.replace_if_exists = 1
    information.root_directory = parent_handle
    information.file_name_length = len(encoded_name)
    information.file_name = destination_name
    status_block = _IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_int,
    )
    set_information.restype = wintypes.LONG
    status = set_information(
        handle,
        ctypes.byref(status_block),
        ctypes.byref(information),
        ctypes.sizeof(information),
        10,
    )
    if status < 0:
        raise OSError(f"native artifact replace failed: 0x{status & 0xFFFFFFFF:08x}")


def _windows_dispose_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = (("delete_file", ctypes.c_ubyte),)

    information = _FileDispositionInformation(delete_file=1)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    if not set_information(handle, 4, ctypes.byref(information), ctypes.sizeof(information)):
        raise OSError(ctypes.get_last_error(), "owned temp cleanup failed")


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise OSError(ctypes.get_last_error(), "file handle close failed")


def _atomic_write_windows(
    path: Path,
    content: bytes,
    *,
    directory: _PinnedDirectoryHandle,
    parent: Path,
    temporary_name: str,
    require_external: bool,
    hooks: _AtomicWriteHooks,
) -> None:
    handle: int | None = None
    replaced = False
    try:
        if directory.current_path() != parent:
            raise ManifestCommandError("artifact write failed", exit_code=1)
        handle = _windows_create_relative_file(directory.native_handle, temporary_name)
        owned_identity = _windows_handle_identity(handle)
        opened_path = _windows_handle_path(handle)
        if _windows_handle_identity(directory.native_handle) != _directory_identity(
            opened_path.parent
        ) or (require_external and _is_repository_path(opened_path)):
            raise ManifestCommandError("artifact write failed", exit_code=1)
        _windows_write_handle(handle, content)
        if hooks.before_replace is not None:
            hooks.before_replace()
        current_parent = directory.current_path()
        current_temporary = current_parent / temporary_name
        if (
            current_parent != parent
            or _windows_path_identity(current_temporary) != owned_identity
            or (require_external and _is_repository_path(current_parent))
        ):
            raise ManifestCommandError("artifact write failed", exit_code=1)
        _windows_rename_handle(handle, directory.native_handle, path.name)
        replaced = True
    finally:
        if handle is not None:
            try:
                if not replaced:
                    if hooks.before_cleanup is not None:
                        hooks.before_cleanup()
                    _windows_dispose_handle(handle)
            finally:
                _close_windows_handle(handle)


def _posix_relative_identity(directory: int, name: str) -> tuple[int, int]:
    status = os.stat(name, dir_fd=directory, follow_symlinks=False)
    return status.st_dev, status.st_ino


def _posix_write_descriptor(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written == 0:
            raise OSError("artifact write was incomplete")
        offset += written
    os.fsync(descriptor)


def _posix_cleanup_owned_file(
    descriptor: int,
    *,
    directory: _PinnedDirectoryHandle,
    owned_identity: tuple[int, int],
    before_cleanup: Callable[[], None] | None,
) -> None:
    if before_cleanup is not None:
        before_cleanup()
    for _ in range(2):
        try:
            opened_path = _descriptor_path(descriptor)
            if opened_path.parent != directory.current_path():
                return
            if (
                _posix_relative_identity(directory.native_handle, opened_path.name)
                != owned_identity
            ):
                return
            if _descriptor_path(descriptor) != opened_path:
                continue
            os.unlink(opened_path.name, dir_fd=directory.native_handle)
            return
        except OSError:
            return


def _atomic_write_posix(
    path: Path,
    content: bytes,
    *,
    directory: _PinnedDirectoryHandle,
    parent: Path,
    temporary_name: str,
    require_external: bool,
    hooks: _AtomicWriteHooks,
) -> None:
    """Write relative to a pinned, operator-controlled directory descriptor."""
    descriptor: int | None = None
    replaced = False
    owned_identity: tuple[int, int] | None = None
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            temporary_name,
            flags,
            0o666,
            dir_fd=directory.native_handle,
        )
        status = os.fstat(descriptor)
        owned_identity = status.st_dev, status.st_ino
        opened_path = _descriptor_path(descriptor)
        if opened_path.parent != directory.current_path() or (
            require_external and _is_repository_path(opened_path)
        ):
            raise ManifestCommandError("artifact write failed", exit_code=1)
        _posix_write_descriptor(descriptor, content)
        if hooks.before_replace is not None:
            hooks.before_replace()
        current_parent = directory.current_path()
        if (
            current_parent != parent
            or _posix_relative_identity(directory.native_handle, temporary_name) != owned_identity
            or (require_external and _is_repository_path(current_parent))
        ):
            raise ManifestCommandError("artifact write failed", exit_code=1)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory.native_handle,
            dst_dir_fd=directory.native_handle,
        )
        replaced = True
    finally:
        if descriptor is not None:
            try:
                if not replaced and owned_identity is not None:
                    _posix_cleanup_owned_file(
                        descriptor,
                        directory=directory,
                        owned_identity=owned_identity,
                        before_cleanup=hooks.before_cleanup,
                    )
            finally:
                os.close(descriptor)


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    require_external: bool = False,
    expected_parent_identity: tuple[int, int] | None = None,
    hooks: _AtomicWriteHooks | None = None,
) -> None:
    hooks = hooks or _AtomicWriteHooks()
    temporary_name = f"{path.name}.{uuid4().hex}.tmp"
    try:
        requested_parent = path.parent.resolve(strict=True)
        with _pinned_directory(requested_parent) as directory:
            parent = directory.current_path()
            parent_identity = directory.identity
            if (
                parent != requested_parent
                or (
                    expected_parent_identity is not None
                    and parent_identity != expected_parent_identity
                )
                or (require_external and _is_repository_path(parent))
            ):
                raise ManifestCommandError("artifact write failed", exit_code=1)
            if hooks.after_parent_pinned is not None:
                hooks.after_parent_pinned()
            if directory.current_path() != parent:
                raise ManifestCommandError("artifact write failed", exit_code=1)
            writer = _atomic_write_windows if os.name == "nt" else _atomic_write_posix
            writer(
                path,
                content,
                directory=directory,
                parent=parent,
                temporary_name=temporary_name,
                require_external=require_external,
                hooks=hooks,
            )
    except ManifestCommandError:
        raise
    except OSError as error:
        raise ManifestCommandError("artifact write failed", exit_code=1) from error


def _read_bounded(path: Path, *, error_message: str) -> bytes:
    try:
        with path.open("rb") as input_file:
            content = input_file.read(_MAX_INPUT_BYTES + 1)
        if len(content) > _MAX_INPUT_BYTES:
            raise ManifestCommandError(error_message)
        return content
    except ManifestCommandError:
        raise
    except OSError as error:
        raise ManifestCommandError(error_message) from error


def _load_settings() -> Any:
    try:
        from app.core.config import settings
    except (ImportError, ValidationError, ValueError) as error:
        raise ManifestCommandError("configuration is invalid") from error
    return settings


def _registry_path(args: argparse.Namespace, settings: Any) -> Path:
    supplied = getattr(args, "registry", None)
    return supplied if supplied is not None else Path(settings.gate1_source_registry_path)


def _load_registry(path: Path) -> SourceRegistry:
    _read_bounded(path, error_message="source registry is invalid")
    return load_source_registry(path)


def _configuration_fingerprint(settings: Any, registry: SourceRegistry) -> str:
    public_configuration = {
        "domain_min_interval_seconds": settings.gate1_domain_min_interval_seconds,
        "live_discovery_enabled": settings.gate1_live_discovery_enabled,
        "registry": registry.model_dump(mode="json"),
        "zhihu_provider_enabled": settings.zhihu_provider_enabled,
        "zhihu_request_budget": settings.gate1_zhihu_request_budget,
    }
    return sha256(_json_bytes(public_configuration)).hexdigest()


def _session_factory() -> Any:
    try:
        from app.core.database import SessionLocal
    except (ImportError, OSError, SQLAlchemyError, ValidationError, ValueError) as error:
        raise ManifestCommandError("database unavailable", exit_code=1) from error
    return SessionLocal


def _require_external_candidate_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ManifestCommandError("candidate input is invalid") from error
    if resolved.is_relative_to(_REPOSITORY_ROOT):
        raise ManifestCommandError("candidate path must be outside repository")
    return resolved


def _require_external_identity_output(path: Path) -> _ExternalOutputPath:
    try:
        if path.exists() or path.is_symlink():
            resolved = path.resolve(strict=True)
        else:
            resolved = path.parent.resolve(strict=True) / path.name
    except OSError as error:
        raise ManifestCommandError("identity output is invalid") from error
    if resolved.is_relative_to(_REPOSITORY_ROOT):
        raise ManifestCommandError("identity work path must be outside repository")
    return _ExternalOutputPath(
        path=resolved,
        parent_identity=_directory_identity(resolved.parent),
    )


def _candidate_facts(path: Path) -> tuple[CandidateFactInput, ...]:
    content = _read_bounded(path, error_message="candidate input is invalid")
    try:
        lines = tuple(
            line for line in content.decode("utf-8").splitlines() if line.strip()
        )
        if not lines:
            raise ValueError("empty candidate input")
        return tuple(
            CandidateFactInput.model_validate_json(line)
            for line in lines
        )
    except (UnicodeError, ValueError, ValidationError) as error:
        raise ManifestCommandError("candidate input is invalid") from error


def _review_decisions(path: Path) -> tuple[ReviewDecisionInput, ...]:
    content = _read_bounded(path, error_message="review input is invalid")
    try:
        payload = json.loads(content)
        if not isinstance(payload, list):
            raise TypeError("review input must be an array")
        return tuple(ReviewDecisionInput.model_validate(value) for value in payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise ManifestCommandError("review input is invalid") from error


def _identity_review_decisions(path: Path) -> tuple[IdentityReviewDecisionInput, ...]:
    try:
        with path.open("rb") as input_file:
            opened_identity = _file_identity_from_handle(input_file)
            opened_path = _opened_file_path(input_file)
            if _is_repository_path(opened_path):
                raise ManifestCommandError(
                    "identity work path must be outside repository"
                )
            current_path = path.resolve(strict=True)
            if _is_repository_path(current_path):
                raise ManifestCommandError(
                    "identity work path must be outside repository"
                )
            if (
                current_path != opened_path
                or _file_identity_from_path(current_path) != opened_identity
            ):
                raise ManifestCommandError("identity review input is invalid")
            content = input_file.read(_MAX_INPUT_BYTES + 1)
        if len(content) > _MAX_INPUT_BYTES:
            raise ManifestCommandError("identity review input is invalid")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, list):
            raise TypeError("identity review input must be an array")
        return tuple(
            IdentityReviewDecisionInput.model_validate(value) for value in payload
        )
    except ManifestCommandError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError, ValidationError) as error:
        raise ManifestCommandError("identity review input is invalid") from error


def _registry_check(args: argparse.Namespace) -> dict[str, object]:
    settings = _load_settings()
    registry = _load_registry(_registry_path(args, settings))
    return {"entries": len(registry.entries)}


def _candidate_import(args: argparse.Namespace) -> dict[str, object]:
    settings = _load_settings()
    registry = _load_registry(_registry_path(args, settings))
    path = _require_external_candidate_path(args.candidate_path)
    facts = _candidate_facts(path)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        imported = import_candidate_facts(session, facts, registry)
        resolved = auto_resolve_candidates(session)
    return {
        "auto_accepted": resolved.auto_accepted,
        "created": imported.created,
        "replayed": imported.replayed,
        "review_required": resolved.review_required,
    }


def _review_export(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        items = export_review_queue(session)
    _atomic_write(args.output, _json_bytes([asdict(item) for item in items]))
    return {"review_items": len(items)}


def _review_apply(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    decisions = _review_decisions(args.decisions)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        summary = apply_review_decisions(session, decisions)
    return {"applied": summary.applied, "replayed": summary.replayed}


def _identity_review_export(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    output = _require_external_identity_output(args.output)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        items = identity_review_export_payload(session)
    _atomic_write(
        output.path,
        _json_bytes(items),
        require_external=True,
        expected_parent_identity=output.parent_identity,
    )
    return {"exported": len(items), "output": output.path.name}


def _identity_review_apply(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    decisions = _identity_review_decisions(args.decisions)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        summary = identity_review_apply_payload(session, decisions)
    return {"applied": summary.applied, "replayed": summary.replayed}


def _company_identity_audit(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    output = _require_external_identity_output(args.output)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        repository = SqlAlchemyCompanyIdentityRepository(session)
        report = company_identity_audit_payload(session, repository)
    _atomic_write(
        output.path,
        _json_bytes(report),
        require_external=True,
        expected_parent_identity=output.parent_identity,
    )
    return {"findings": len(report.findings), "output": output.path.name}


def _manifest_freeze(args: argparse.Namespace) -> dict[str, object]:
    settings = _load_settings()
    registry = _load_registry(_registry_path(args, settings))
    fingerprint = args.config_fingerprint or _configuration_fingerprint(settings, registry)
    SessionLocal = _session_factory()
    with SessionLocal() as session:
        frozen = freeze_manifest(session, config_fingerprint=fingerprint)
    _atomic_write(args.manifest_out, frozen.manifest_bytes)
    _atomic_write(args.quota_out, frozen.quota_bytes)
    return {
        "manifest_companies": len(frozen.members),
        "manifest_version": frozen.manifest_version,
    }


def _manifest_file_identity(path: Path) -> tuple[str, str]:
    content = _read_bounded(path, error_message="manifest file is invalid")
    try:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise TypeError("manifest is not an object")
        version = payload["manifest_version"]
        fingerprint = payload["config_fingerprint"]
        if not isinstance(version, str) or not isinstance(fingerprint, str):
            raise TypeError("manifest identity is invalid")
        if len(version) != 64 or len(fingerprint) != 64:
            raise ValueError("manifest identity is invalid")
        int(version, 16)
        int(fingerprint, 16)
        return version, fingerprint
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestCommandError("manifest file is invalid") from error


def _selected_manifest(
    args: argparse.Namespace, SessionLocal: Any
) -> tuple[str, str]:
    if args.manifest_file is not None:
        version, file_fingerprint = _manifest_file_identity(args.manifest_file)
    else:
        version = args.manifest
        file_fingerprint = ""

    with SessionLocal() as session:
        if version is None:
            manifests = tuple(session.scalars(select(CompanyManifest).order_by(CompanyManifest.version)))
            if len(manifests) != 1:
                raise ManifestCommandError("exactly one frozen manifest is required")
            manifest = manifests[0]
        else:
            manifest = session.get(CompanyManifest, version)
            if manifest is None:
                raise ManifestCommandError("manifest does not exist")
        if file_fingerprint and manifest.config_fingerprint != file_fingerprint:
            raise ManifestCommandError("manifest file conflicts with database")
        return manifest.version, manifest.config_fingerprint


def _code_commit(supplied: str | None) -> str:
    if supplied is not None:
        return supplied
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ManifestCommandError("code commit is unavailable") from error
    return result.stdout.strip()


def _report(args: argparse.Namespace) -> dict[str, object]:
    _load_settings()
    SessionLocal = _session_factory()
    version, stored_fingerprint = _selected_manifest(args, SessionLocal)
    if (
        args.config_fingerprint is not None
        and args.config_fingerprint != stored_fingerprint
    ):
        raise ManifestCommandError(
            "report fingerprint conflicts with frozen manifest"
        )
    with SessionLocal() as session:
        report = ManifestReportService(session).build(
            version,
            code_commit=_code_commit(args.code_commit),
            config_fingerprint=stored_fingerprint,
        )
    payload = report.model_dump(mode="json")
    if args.output is not None:
        _atomic_write(args.output, _json_bytes(payload))
    return payload


class _ZhihuRequestBudget:
    def __init__(
        self,
        *,
        request_budget: int,
        before_request: Callable[[], Awaitable[None]],
    ) -> None:
        self._request_budget = request_budget
        self._before_request = before_request
        self.requests = 0

    async def before_request(self) -> None:
        from app.ingestion.errors import ProviderError

        if self.requests >= self._request_budget:
            raise ProviderError(code="request_budget_exhausted", retryable=False)
        await self._before_request()
        self.requests += 1


class _ZhihuFallbackDiscoverer:
    def __init__(
        self,
        provider: Any,
        *,
        request_counter: _ZhihuRequestBudget,
        stopped: bool = False,
    ) -> None:
        self._provider = provider
        self._request_counter = request_counter
        self.stopped = stopped

    @property
    def requests(self) -> int:
        return self._request_counter.requests

    async def discover(self, company: ManifestCompany) -> EntryDiscoveryResult:
        from app.ingestion.contracts import ProviderQuery
        from app.ingestion.errors import ProviderError
        from app.manifest.discovery import classify_recruitment_url

        if self.stopped:
            return self._blocked("fallback_source_stopped")
        official_host = (
            ""
            if company.official_website is None
            else (urlsplit(str(company.official_website)).hostname or "").lower().rstrip(".")
        )
        if not official_host:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.NOT_FOUND,
                method="zhihu_global_search",
                source_id="zhihu_global_search",
                error_code="official_website_missing",
            )
        try:
            result = await self._provider.search(
                ProviderQuery(
                    query=f"{company.canonical_name} careers jobs",
                    website=company.official_website,
                    allowed_hosts=frozenset({official_host}),
                    max_results=5,
                )
            )
        except ProviderError as error:
            if error.code in {
                "provider_auth_failed",
                "provider_rate_limited",
                "request_budget_exhausted",
            }:
                self.stopped = True
                return self._blocked(error.code)
            return EntryDiscoveryResult(
                status=DiscoveryStatus.FAILED,
                method="zhihu_global_search",
                source_id="zhihu_global_search",
                error_code="fallback_request_failed",
            )
        urls = sorted({str(document.url) for document in result.documents})
        if not urls:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.NOT_FOUND,
                method="zhihu_global_search",
                source_id="zhihu_global_search",
                error_code="recruitment_entry_not_found",
            )
        if len(urls) > 1:
            return EntryDiscoveryResult(
                status=DiscoveryStatus.REVIEW_REQUIRED,
                method="zhihu_global_search",
                source_id="zhihu_global_search",
                error_code="ambiguous_recruitment_entries",
            )
        url = urls[0]
        return EntryDiscoveryResult.model_validate(
            {
                "status": DiscoveryStatus.ACCEPTED,
                "method": "zhihu_global_search",
                "candidate_url": url,
                "normalized_url": url,
                "source_id": "zhihu_global_search",
                "classification": classify_recruitment_url(url, official_host),
            }
        )

    @staticmethod
    def _blocked(code: str) -> EntryDiscoveryResult:
        return EntryDiscoveryResult(
            status=DiscoveryStatus.BLOCKED,
            method="zhihu_global_search",
            source_id="zhihu_global_search",
            error_code=code,
        )


class _DisabledFallbackDiscoverer:
    async def discover(self, _company: ManifestCompany) -> EntryDiscoveryResult:
        return EntryDiscoveryResult(
            status=DiscoveryStatus.NOT_FOUND,
            method="fallback_disabled",
            error_code="fallback_disabled",
        )


def _discovery_composition(
    settings: Any,
    registry: SourceRegistry,
    *,
    stopped_source_ids: frozenset[str] | set[str] = frozenset(),
) -> tuple[Any, Any | None]:
    from app.ingestion.providers.http import SafeHttpClient
    from app.ingestion.providers.robots import RobotsPolicy
    from app.manifest.discovery import (
        DomainStartLimiter,
        EntryDiscoveryCoordinator,
        OfficialEntryDiscoverer,
    )

    limiter = DomainStartLimiter(interval_seconds=settings.gate1_domain_min_interval_seconds)
    client = SafeHttpClient(before_request=limiter.wait)
    official = OfficialEntryDiscoverer(
        http_client=client,
        robots_policy=RobotsPolicy(http_client=client),
    )
    fallback: Any = _DisabledFallbackDiscoverer()
    budgeted_fallback: _ZhihuFallbackDiscoverer | None = None
    if settings.zhihu_provider_enabled:
        from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider

        try:
            registered = registry.require("zhihu_global_search")
        except KeyError as error:
            raise ManifestCommandError("Zhihu fallback is not registered") from error
        if SourceRole.ENTRY_DISCOVERY_FALLBACK not in registered.roles:
            raise ManifestCommandError("Zhihu fallback is not authorized")
        registered_budget = registered.rehearsal_request_budget
        budget = settings.gate1_zhihu_request_budget
        if registered_budget is not None:
            budget = min(budget, registered_budget)
        if budget < 1:
            raise ManifestCommandError("Zhihu request budget is invalid")
        async def wait_before_request() -> None:
            await limiter.wait(ZhihuGlobalSearchProvider.endpoint)

        request_counter = _ZhihuRequestBudget(
            request_budget=budget,
            before_request=wait_before_request,
        )
        provider = ZhihuGlobalSearchProvider(
            enabled=True,
            access_secret=settings.zhihu_access_secret,
            before_request=request_counter.before_request,
        )
        budgeted_fallback = _ZhihuFallbackDiscoverer(
            provider,
            request_counter=request_counter,
            stopped="zhihu_global_search" in stopped_source_ids,
        )
        fallback = budgeted_fallback
    return (
        EntryDiscoveryCoordinator(
            official_discoverer=official,
            fallback_discoverer=fallback,
        ),
        budgeted_fallback,
    )


def _official_host(company: ManifestCompany) -> str:
    if company.official_website is None:
        return ""
    return (urlsplit(str(company.official_website)).hostname or "").lower().rstrip(".")


def _observation_is_terminal(observation: EntryDiscoveryObservation) -> bool:
    return not is_retryable_discovery_observation(observation)


def _update_domain_state(
    state: _DiscoveryState,
    *,
    official_host: str,
    method: str,
    error_code: str | None,
) -> None:
    if not official_host or method != "official_navigation":
        return
    if error_code in {"provider_access_denied", "source_access_stopped"}:
        state.stopped_domains.add(official_host)
        return
    if error_code == "source_rate_limited":
        state.consecutive_rate_limits[official_host] = 3
        state.stopped_domains.add(official_host)
        return
    if error_code == "provider_rate_limited":
        state.consecutive_rate_limits[official_host] += 1
        if state.consecutive_rate_limits[official_host] >= 3:
            state.stopped_domains.add(official_host)
        return
    state.consecutive_rate_limits[official_host] = 0


def _load_discovery_members(
    SessionLocal: Any, manifest_version: str
) -> tuple[tuple[ManifestCompany, ...], _DiscoveryState]:
    with SessionLocal() as session:
        members = tuple(
            session.scalars(
                select(CompanyManifestMember)
                .where(CompanyManifestMember.manifest_version == manifest_version)
                .order_by(CompanyManifestMember.position)
            )
        )
        observations = tuple(
            session.scalars(
                select(EntryDiscoveryObservation).where(
                    EntryDiscoveryObservation.manifest_version == manifest_version
                ).order_by(
                    EntryDiscoveryObservation.observed_at,
                    EntryDiscoveryObservation.id,
                )
            )
        )
    companies = tuple(
        ManifestCompany(
            company_id=member.company_id,
            canonical_name=member.canonical_name,
            primary_category=member.primary_category,
            official_website=member.official_website,
            recruitment_url=member.recruitment_url,
        )
        for member in members
    )
    companies_by_id = {company.company_id: company for company in companies}
    state = _DiscoveryState(
        observed_company_ids=frozenset(
            observation.company_id for observation in observations
        ),
        terminal_company_ids=set(),
        retryable_observation_ids={},
        stopped_domains=set(),
        stopped_source_ids=set(),
        consecutive_rate_limits=Counter(),
    )
    for observation in observations:
        if _observation_is_terminal(observation):
            state.terminal_company_ids.add(observation.company_id)
            state.retryable_observation_ids.pop(observation.company_id, None)
        else:
            state.terminal_company_ids.discard(observation.company_id)
            state.retryable_observation_ids[observation.company_id] = observation.id
        if observation.source_id and observation.error_code in {
            "fallback_source_stopped",
            "provider_auth_failed",
            "provider_rate_limited",
            "request_budget_exhausted",
        }:
            state.stopped_source_ids.add(observation.source_id)
        company = companies_by_id.get(observation.company_id)
        if company is not None:
            _update_domain_state(
                state,
                official_host=_official_host(company),
                method=observation.method,
                error_code=observation.error_code,
            )
    return companies, state


async def _run_discovery(
    *,
    SessionLocal: Any,
    manifest_version: str,
    companies: tuple[ManifestCompany, ...],
    coordinator: Any,
    limit: int | None,
    state: _DiscoveryState | None = None,
    already_observed: frozenset[UUID] = frozenset(),
) -> Counter[DiscoveryStatus]:
    if state is None:
        state = _DiscoveryState(
            observed_company_ids=already_observed,
            terminal_company_ids=set(already_observed),
            retryable_observation_ids={},
            stopped_domains=set(),
            stopped_source_ids=set(),
            consecutive_rate_limits=Counter(),
        )
    counts: Counter[DiscoveryStatus] = Counter()
    for company in companies:
        if company.company_id in state.terminal_company_ids:
            continue
        if limit is not None and sum(counts.values()) >= limit:
            break
        official_host = _official_host(company)
        if official_host in state.stopped_domains:
            result = EntryDiscoveryResult(
                status=DiscoveryStatus.BLOCKED,
                method="official_navigation",
                error_code=(
                    "source_rate_limited"
                    if state.consecutive_rate_limits[official_host] >= 3
                    else "source_access_stopped"
                ),
            )
        else:
            result = await coordinator.discover(company)
            _update_domain_state(
                state,
                official_host=official_host,
                method=result.method,
                error_code=result.error_code,
            )
        command = RecordDiscoveryCommand(
            manifest_version=manifest_version,
            company_id=company.company_id,
            result=result,
            observed_at=datetime.now(UTC),
        )
        with SessionLocal() as session:
            observation_id = state.retryable_observation_ids.get(company.company_id)
            if observation_id is None:
                record_discovery_result(session, command)
            else:
                transition_retryable_discovery_result(
                    session,
                    observation_id=observation_id,
                    command=command,
                )
        counts[result.status] += 1
    return counts


def _discover(args: argparse.Namespace) -> dict[str, object]:
    settings = _load_settings()
    if not settings.gate1_live_discovery_enabled or not args.live:
        raise ManifestCommandError("live discovery is disabled")
    registry = _load_registry(_registry_path(args, settings))
    SessionLocal = _session_factory()
    version, _fingerprint = _selected_manifest(args, SessionLocal)
    companies, state = _load_discovery_members(SessionLocal, version)
    if state.observed_company_ids and not args.resume:
        raise ManifestCommandError("discovery observations already exist; use --resume")
    coordinator, fallback = _discovery_composition(
        settings,
        registry,
        stopped_source_ids=state.stopped_source_ids,
    )
    counts = asyncio.run(
        _run_discovery(
            SessionLocal=SessionLocal,
            manifest_version=version,
            companies=companies,
            coordinator=coordinator,
            limit=args.limit,
            state=state,
        )
    )
    return {
        "manifest_version": version,
        "processed": sum(counts.values()),
        "skipped": len(state.terminal_company_ids),
        "status_counts": {status.value: counts[status] for status in DiscoveryStatus},
        "zhihu_requests": 0 if fallback is None else fallback.requests,
    }


_COMMANDS = {
    "registry-check": _registry_check,
    "candidate-import": _candidate_import,
    "review-export": _review_export,
    "review-apply": _review_apply,
    "identity-review-export": _identity_review_export,
    "identity-review-apply": _identity_review_apply,
    "company-identity-audit": _company_identity_audit,
    "manifest-freeze": _manifest_freeze,
    "discover": _discover,
    "report": _report,
}


def main() -> int:
    args = _parser().parse_args()
    try:
        result = _COMMANDS[args.command](args)
    except ManifestCommandError as error:
        print(f"manifest command failed: {error}", file=sys.stderr)
        return error.exit_code
    except (
        CandidateEvidenceConflict,
        DiscoveryRecordConflict,
        ManifestFreezeError,
        ManifestReportError,
        ReviewDecisionConflict,
        IdentityOwnerChanged,
        IdentityReviewConflict,
        SourceRegistryError,
        UnregisteredSourceError,
    ) as error:
        print(f"manifest command failed: {error}", file=sys.stderr)
        return 2
    except IdentitySearchUnavailable:
        print("manifest command failed: database unavailable", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError):
        print("manifest command failed: database unavailable", file=sys.stderr)
        return 1
    except ValidationError:
        print("manifest command failed: input is invalid", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - the CLI boundary must never expose tracebacks
        print("manifest command failed: internal error", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
