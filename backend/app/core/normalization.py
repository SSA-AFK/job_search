import unicodedata
from urllib.parse import urlsplit, urlunsplit


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(normalized.split())


def normalize_url(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    parts = urlsplit(normalized)
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("URL must use HTTP or HTTPS and include a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL must not include credentials")

    host = parts.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and parts.port == 80) or (
        scheme == "https" and parts.port == 443
    )
    port = "" if parts.port is None or default_port else f":{parts.port}"
    return urlunsplit((scheme, f"{host}{port}", parts.path, parts.query, ""))
