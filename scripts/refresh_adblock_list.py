"""Download and validate the ad-domain snapshot used by packaged builds."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_URL = (
    "https://pgl.yoyo.org/adservers/serverlist.php?"
    "hostformat=plain&showintro=0&mimetype=plaintext"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MINIMUM_DOMAIN_COUNT = 1_000
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_RETRIES = 3
DOWNLOAD_ATTEMPTS = DOWNLOAD_RETRIES + 1
RETRY_DELAY_SECONDS = 1

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "build_staging" / "adblock"
DOMAIN_FILENAME = "adblock_domains.txt"
METADATA_FILENAME = "adblock_domains.json"

_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class BlocklistError(RuntimeError):
    """Raised when a downloaded blocklist cannot be safely packaged."""


def _is_valid_hostname(hostname: str) -> bool:
    if not hostname.isascii() or len(hostname) > 253:
        return False

    labels = hostname.split(".")
    if len(labels) < 2 or any(not _LABEL_PATTERN.fullmatch(label) for label in labels):
        return False

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return False


def normalize_domains(text: str, *, minimum_count: int = MINIMUM_DOMAIN_COUNT) -> list[str]:
    """Return a sorted, normalized domain list or reject the entire response."""
    prefix = text.lstrip()[:256].lower()
    if prefix.startswith("<!doctype html") or prefix.startswith("<html"):
        raise BlocklistError("the server returned HTML instead of a hostname list")

    domains: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        candidate = raw_line.strip().lower()
        if not candidate or candidate.startswith("#"):
            continue
        if not _is_valid_hostname(candidate):
            raise BlocklistError(
                f"invalid hostname on line {line_number}: {raw_line.strip()!r}"
            )
        domains.add(candidate)

    if len(domains) < minimum_count:
        raise BlocklistError(
            f"only {len(domains)} unique valid hostnames were returned; "
            f"at least {minimum_count} are required"
        )
    return sorted(domains)


def _download_domains_once(*, opener=urlopen) -> list[str]:
    request = Request(
        SOURCE_URL,
        headers={"User-Agent": "UmaLauncher blocklist builder"},
    )
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise BlocklistError(f"the server returned HTTP {status}")

            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise BlocklistError("the server returned an invalid Content-Length") from exc
                if declared_size > MAX_RESPONSE_BYTES:
                    raise BlocklistError(
                        f"response is too large ({declared_size} bytes; "
                        f"maximum is {MAX_RESPONSE_BYTES})"
                    )

            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except BlocklistError:
        raise
    except (HTTPError, URLError, OSError) as exc:
        raise BlocklistError(f"download failed: {exc}") from exc

    if len(payload) > MAX_RESPONSE_BYTES:
        raise BlocklistError(
            f"response exceeds the {MAX_RESPONSE_BYTES}-byte maximum"
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BlocklistError("response is not valid UTF-8") from exc
    return normalize_domains(text)


def download_domains(
    *,
    opener=urlopen,
    sleeper=time.sleep,
    attempts: int = DOWNLOAD_ATTEMPTS,
) -> list[str]:
    """Download the canonical list, retrying briefly without a stale fallback."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_error: BlocklistError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_domains_once(opener=opener)
        except BlocklistError as exc:
            last_error = exc
            if attempt < attempts:
                sleeper(RETRY_DELAY_SECONDS * attempt)

    raise BlocklistError(
        f"refresh failed after {attempts} attempts: {last_error}"
    ) from last_error


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def refresh_snapshot(
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
    *,
    opener=urlopen,
    sleeper=time.sleep,
    now: datetime | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    # A failed refresh must never leave a prior snapshot available for packaging.
    for filename in (DOMAIN_FILENAME, METADATA_FILENAME):
        (output_directory / filename).unlink(missing_ok=True)

    domains = download_domains(opener=opener, sleeper=sleeper)
    domain_bytes = ("\n".join(domains) + "\n").encode("utf-8")
    metadata: dict[str, object] = {
        "source_url": SOURCE_URL,
        "fetched_at_utc": _format_timestamp(now),
        "entry_count": len(domains),
        "sha256": hashlib.sha256(domain_bytes).hexdigest(),
    }
    metadata_bytes = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    domain_path = output_directory / DOMAIN_FILENAME
    metadata_path = output_directory / METADATA_FILENAME
    _atomic_write(domain_path, domain_bytes)
    _atomic_write(metadata_path, metadata_bytes)
    return domain_path, metadata_path, metadata


def _format_timestamp(now: datetime | None) -> str:
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh UmaLauncher's validated build-time ad blocklist."
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=f"staging directory (default: {DEFAULT_OUTPUT_DIRECTORY})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        domain_path, metadata_path, metadata = refresh_snapshot(
            args.output_directory
        )
    except (BlocklistError, OSError) as exc:
        print(f"Blocklist refresh failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {metadata['entry_count']} domains to {domain_path} "
        f"(metadata: {metadata_path})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
