"""Validate that the public source tree and packaged executable stay public-only."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SOURCE_PATHS = {
    "build_global.bat",
    "umalauncher/_assets/icon/global/connected.ico",
    "umalauncher/_assets/icon/global/connecting.ico",
    "umalauncher/_assets/icon/global/default.ico",
    "umalauncher/_assets/trackblazer_scheduler/app.js",
    "umalauncher/_assets/trackblazer_scheduler/index.html",
    "umalauncher/_assets/trackblazer_scheduler/solver-browser.js",
    "umalauncher/_assets/trackblazer_scheduler/styles.css",
    "umalauncher/_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/glpk.wasm",
    "umalauncher/_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/index.js",
    "umalauncher/_assets/training_helper/index.html",
    "umalauncher/_assets/umasim-cli.exe",
    "umalauncher/runtime_extensions.py",
    "umalauncher/threader_global.spec",
}

FORBIDDEN_SOURCE_PATHS = {
    "build.bat",
    "build_jp.bat",
    "build_jp_steam.bat",
    "umalauncher/event_reward_parser.py",
    "umalauncher/global_runtime_hook.py",
    "umalauncher/jp_steam_runtime_hook.py",
    "umalauncher/presence_screens.py",
    "umalauncher/threader.spec",
    "umalauncher/threader_jp_steam.spec",
    "umalauncher/training_sim_worker.py",
}

FORBIDDEN_SOURCE_PREFIXES = (
    "umalauncher/_assets/icon/steam/",
    "umalauncher/external/",
    "umalauncher/umasim/",
    "umalauncher_private/",
)

# These are implementation identifiers from the private event-prediction and
# Python training-simulator overlay. Avoid a generic "prediction" check because
# the public adblock source legitimately contains that English word.
FORBIDDEN_SOURCE_TOKENS = (
    "event_reward_parser",
    "_active_event_prediction",
    "sanitize_event_prediction",
    "display_event_reward_prediction",
    "ul_update_event_rewards",
    "predictionchoices",
    "appendchoiceprediction",
    "packet-prediction",
    "training_sim_worker",
    "attach_training_sim",
    "training_sim",
    "trainingsim",
    "trainingquality",
    "training_quality",
    "private_build",
    "umalauncher-private",
    "umalauncher_private",
)

SCANNED_SOURCE_SUFFIXES = {
    ".bat",
    ".css",
    ".html",
    ".js",
    ".json",
    ".py",
    ".spec",
    ".yaml",
    ".yml",
}

REQUIRED_ARCHIVE_MODULES = {
    "carrotjuicer",
    "helper_table",
    "helper_table_elements",
    "helper_theme",
    "horsium",
    "mdb",
    "runtime_extensions",
    "steam",
    "training_tracker",
    "umaserver",
    "version",
}

REQUIRED_ARCHIVE_FILES = {
    "_assets/adblock_domains.json",
    "_assets/adblock_domains.txt",
    "_assets/icon/global/connected.ico",
    "_assets/icon/global/connecting.ico",
    "_assets/icon/global/default.ico",
    "_assets/trackblazer_scheduler/app.js",
    "_assets/trackblazer_scheduler/epithets.json",
    "_assets/trackblazer_scheduler/favicon.ico",
    "_assets/trackblazer_scheduler/index.html",
    "_assets/trackblazer_scheduler/races.json",
    "_assets/trackblazer_scheduler/solver-browser.js",
    "_assets/trackblazer_scheduler/styles.css",
    "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/glpk.wasm",
    "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/index.js",
    "_assets/training_helper/index.html",
    "_assets/umasim-cli.exe",
    "selenium/webdriver/common/windows/selenium-manager.exe",
}

FORBIDDEN_ARCHIVE_MODULE_ROOTS = {
    "event_reward_parser",
    "external",
    "global_runtime_hook",
    "jp_steam_runtime_hook",
    "presence_screens",
    "race_data_parser",
    "screenstate_utils",
    "training_sim_worker",
    "translation",
    "umasim",
    "umalauncher_private",
    "vpn",
}

FORBIDDEN_ARCHIVE_FILE_PREFIXES = (
    "_assets/icon/steam/",
    "umasim/",
    "umalauncher_private/",
)

FORBIDDEN_ARCHIVE_FILES = {
    "_assets/icon/connected.ico",
    "_assets/icon/connecting.ico",
    "_assets/icon/default.ico",
}


def _tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return {
        name.decode("utf-8").replace("\\", "/").lower()
        for name in result.stdout.split(b"\0")
        if name
    }


def _failures_for_source() -> list[str]:
    tracked = _tracked_paths()
    failures: list[str] = []

    for path in sorted(REQUIRED_SOURCE_PATHS):
        if path.lower() not in tracked:
            failures.append(f"required public source path is missing: {path}")

    for path in sorted(FORBIDDEN_SOURCE_PATHS):
        if path.lower() in tracked:
            failures.append(f"private or retired source path is tracked: {path}")

    for path in sorted(tracked):
        if any(path.startswith(prefix) for prefix in FORBIDDEN_SOURCE_PREFIXES):
            failures.append(f"private or retired source path is tracked: {path}")

    checker_path = "scripts/check_public_boundary.py"
    for relative_path in sorted(tracked):
        if relative_path == checker_path:
            continue
        if Path(relative_path).suffix.lower() not in SCANNED_SOURCE_SUFFIXES:
            continue

        absolute_path = REPO_ROOT / Path(relative_path)
        try:
            content = absolute_path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            failures.append(f"expected text source is not UTF-8: {relative_path}")
            continue

        for token in FORBIDDEN_SOURCE_TOKENS:
            if token in content:
                failures.append(
                    f"private implementation token {token!r} is present in {relative_path}"
                )

    return failures


def _archive_contents(
    archive_path: Path,
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is required for --archive validation; install requirements.txt"
        ) from exc

    reader = CArchiveReader(str(archive_path))
    files = {name.replace("\\", "/").lower() for name in reader.toc}
    modules = {
        name.lower()
        for name, entry in reader.toc.items()
        if entry[-1] in {"m", "M", "s"}
    }
    private_asset_tokens: dict[str, set[str]] = {}

    for embedded_name in reader.toc:
        normalized_name = embedded_name.replace("\\", "/").lower()
        if not normalized_name.startswith("_assets/"):
            continue
        if Path(normalized_name).suffix not in {".css", ".html", ".js", ".json"}:
            continue

        payload = reader.extract(embedded_name).lower()
        matches = {
            token for token in FORBIDDEN_SOURCE_TOKENS if token.encode() in payload
        }
        if matches:
            private_asset_tokens[normalized_name] = matches

    for embedded_name, entry in reader.toc.items():
        if entry[-1] != "z":
            continue
        embedded = reader.open_embedded_archive(embedded_name)
        modules.update(name.lower() for name in embedded.toc)

    return files, modules, private_asset_tokens


def _has_module_root(module: str, root: str) -> bool:
    return module == root or module.startswith(f"{root}.")


def _failures_for_archive(archive_path: Path) -> list[str]:
    failures: list[str] = []
    if archive_path.name != "UmaLauncher-Global.exe":
        failures.append(
            "public artifact must be named UmaLauncher-Global.exe, "
            f"not {archive_path.name}"
        )
    if not archive_path.is_file():
        failures.append(f"public artifact is missing: {archive_path}")
        return failures

    files, modules, private_asset_tokens = _archive_contents(archive_path)

    for module in sorted(REQUIRED_ARCHIVE_MODULES):
        if module.lower() not in modules:
            failures.append(f"required public archive module is missing: {module}")

    for path in sorted(REQUIRED_ARCHIVE_FILES):
        if path.lower() not in files:
            failures.append(f"required public archive file is missing: {path}")

    for root in sorted(FORBIDDEN_ARCHIVE_MODULE_ROOTS):
        matches = sorted(
            module for module in modules if _has_module_root(module, root.lower())
        )
        if matches:
            failures.append(
                f"private or retired archive module root is present: {root} "
                f"({', '.join(matches[:3])})"
            )

    for path in sorted(files):
        if path in FORBIDDEN_ARCHIVE_FILES or any(
            path.startswith(prefix) for prefix in FORBIDDEN_ARCHIVE_FILE_PREFIXES
        ):
            failures.append(f"private or retired archive file is present: {path}")

    for path, tokens in sorted(private_asset_tokens.items()):
        failures.append(
            f"private implementation token is present in archived asset {path}: "
            f"{', '.join(sorted(tokens))}"
        )

    return failures


def _print_result(label: str, failures: list[str]) -> bool:
    if not failures:
        print(f"Public boundary check passed: {label}")
        return True

    print(f"Public boundary check failed: {label}", file=sys.stderr)
    for failure in failures:
        print(f"  - {failure}", file=sys.stderr)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        help="also inspect a built PyInstaller one-file executable",
    )
    args = parser.parse_args()

    passed = _print_result("tracked source", _failures_for_source())
    if args.archive is not None:
        archive_path = args.archive
        if not archive_path.is_absolute():
            archive_path = REPO_ROOT / archive_path
        passed = _print_result(
            f"PyInstaller archive {archive_path.name}",
            _failures_for_archive(archive_path),
        ) and passed

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
