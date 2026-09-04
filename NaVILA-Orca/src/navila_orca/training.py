"""Runtime-alignment checks for the packaged Go2 baseline."""

from __future__ import annotations

from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping, Sequence

from .paths import BUNDLED_GO2_XML


COMPATIBLE_VERSION_SPECS = {
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.x",
    "orca-gym": "26.7.1",
    "orca-lab": "26.7.1",
}


class CompatibilityError(RuntimeError):
    """Raised when the selected Python environment is not the tested stack."""


def _matches_version(distribution: str, installed: str) -> bool:
    expected = COMPATIBLE_VERSION_SPECS[distribution]
    if expected == "5.x":
        return re.fullmatch(r"5(?:\.\d+)+(?:\+[A-Za-z0-9._-]+)?", installed) is not None
    return re.fullmatch(re.escape(expected) + r"(?:\+[A-Za-z0-9._-]+)?", installed) is not None


def compatibility_errors(
    installed_versions: Mapping[str, str | None],
) -> tuple[str, ...]:
    errors: list[str] = []
    for distribution, expected in COMPATIBLE_VERSION_SPECS.items():
        installed = installed_versions.get(distribution)
        if installed is None:
            errors.append(f"{distribution}: not installed (expected {expected})")
        elif not _matches_version(distribution, installed):
            errors.append(f"{distribution}: found {installed}, expected {expected}")
    return tuple(errors)


def ensure_compatible_versions(installed_versions: Mapping[str, str | None]) -> None:
    errors = compatibility_errors(installed_versions)
    if errors:
        raise CompatibilityError("Incompatible OrcaLab environment:\n" + "\n".join(f"  - {item}" for item in errors))


def current_installed_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for distribution in COMPATIBLE_VERSION_SPECS:
        try:
            result[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            result[distribution] = None
    return result


def probe_installed_versions(python: str | os.PathLike[str]) -> dict[str, str | None]:
    python_arg = os.fspath(python)
    if os.path.realpath(python_arg) == os.path.realpath(sys.executable):
        return current_installed_versions()
    names = tuple(COMPATIBLE_VERSION_SPECS)
    source = (
        "import json\nfrom importlib import metadata\n"
        f"names={names!r}\nresult={{}}\n"
        "for name in names:\n"
        "    try: result[name] = metadata.version(name)\n"
        "    except metadata.PackageNotFoundError: result[name] = None\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )
    completed = subprocess.run(
        [python_arg, "-c", source], check=False, capture_output=True, text=True
    )
    if completed.returncode:
        raise CompatibilityError(completed.stderr.strip() or f"could not run {python_arg}")
    return json.loads(completed.stdout)


def _build_parser() -> argparse.ArgumentParser:
    import argparse

    parser = argparse.ArgumentParser(
        description="Check the local MJLab/Go2 baseline alignment; this command does not train a policy."
    )
    parser.add_argument("--python", default=sys.executable)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    versions = probe_installed_versions(args.python)
    ensure_compatible_versions(versions)
    if not BUNDLED_GO2_XML.is_file():
        raise FileNotFoundError(f"bundled Go2 XML is missing: {BUNDLED_GO2_XML}")
    print(json.dumps({"versions": versions, "go2_xml": str(BUNDLED_GO2_XML)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
