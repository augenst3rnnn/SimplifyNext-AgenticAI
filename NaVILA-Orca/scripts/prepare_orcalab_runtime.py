#!/usr/bin/env python3
"""Prepare OrcaLab's native viewport before the first GUI process starts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import tomllib
from urllib.request import urlopen


ORCALAB_VERSION = "26.7.1"
PYSIDE_URL = (
    "https://orcalab-open.oss-cn-shanghai.aliyuncs.com/"
    "python-project_linux.26.7.1.tar.xz"
)
PYSIDE_SHA256 = "30c50babaa8825be4519c9613166e595d97d2a1ce799f186667bb4c767ecffef"
PAK_URL = (
    "https://orcalab-open.oss-cn-shanghai.aliyuncs.com/"
    "orcalab_linux.26.7.1.pak"
)
PAK_SHA256 = "11f292569ed54f2be5991b3a3f6e60fac2d34a52a384c3cbf97ef9b2f9a6af88"
GLVND_SONAMES = ("libGL.so.1", "libOpenGL.so.0")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected_sha256:
        print(f"[orcalab-runtime] verified cached {destination.name}")
        return

    temporary = destination.with_name(f".{destination.name}.download")
    temporary.unlink(missing_ok=True)
    print(f"[orcalab-runtime] downloading {url}")
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = sha256(temporary)
        if actual != expected_sha256:
            raise RuntimeError(
                f"SHA256 mismatch for {destination.name}: "
                f"found {actual}, expected {expected_sha256}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def runtime_archive(user_root: Path) -> Path:
    """Reuse either the versioned cache or the legacy ``unknown`` cache."""
    candidates = (
        user_root / f"python-project-{ORCALAB_VERSION}.tar.xz",
        user_root / "python-project-unknown.tar.xz",
    )
    for candidate in candidates:
        if candidate.is_file() and sha256(candidate) == PYSIDE_SHA256:
            return candidate
    return candidates[0]


def editable_root(root: Path) -> Path | None:
    candidates = [root, *sorted(path.parent for path in root.rglob("pyproject.toml"))]
    for candidate in candidates:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        with pyproject.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        if (
            project.get("name") == "orcalab-pyside"
            and project.get("version") == ORCALAB_VERSION
        ):
            return candidate
    return None


def extract_runtime(archive: Path, destination: Path) -> Path:
    existing = editable_root(destination) if destination.is_dir() else None
    if existing is not None:
        print(f"[orcalab-runtime] verified extracted {existing}")
        return existing

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".orcalab-pyside-prepare-", dir=destination.parent)
    )
    try:
        with tarfile.open(archive, mode="r:xz") as package:
            package.extractall(staging, filter="data")
        if editable_root(staging) is None:
            raise RuntimeError("official OrcaLab archive contains no 26.7.1 Python project")
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    root = editable_root(destination)
    if root is None:  # pragma: no cover - guarded before the atomic rename
        raise RuntimeError("failed to prepare the OrcaLab Python project")
    return root


def glvnd_dependencies(needed: list[str]) -> tuple[str, ...]:
    """Return the OpenGL ABI entries declared in an ELF dependency list."""
    return tuple(
        item for item in needed if Path(item).name in GLVND_SONAMES
    )


def system_glvnd_library(soname: str) -> Path:
    """Resolve a requested OpenGL ABI from the host GLVND installation."""
    if soname not in GLVND_SONAMES:
        raise ValueError(f"unsupported GLVND library: {soname}")

    ldconfig = shutil.which("ldconfig")
    if ldconfig is None:
        for candidate in (Path("/sbin/ldconfig"), Path("/usr/sbin/ldconfig")):
            if candidate.is_file():
                ldconfig = str(candidate)
                break
    if ldconfig is not None:
        cache = subprocess.check_output([ldconfig, "-p"], text=True)
        for line in cache.splitlines():
            match = re.match(
                rf"\s*{re.escape(soname)}\s+.*=>\s+(\S+)\s*$", line
            )
            if match:
                library = Path(match.group(1))
                if library.is_file():
                    return library

    for pattern in (
        f"/lib/*-linux-gnu/{soname}",
        f"/usr/lib/*-linux-gnu/{soname}",
        f"/lib/{soname}",
        f"/usr/lib/{soname}",
    ):
        candidates = sorted(Path("/").glob(pattern.removeprefix("/")))
        if candidates:
            return candidates[0]
    raise RuntimeError(
        f"system {soname} is missing; run "
        "./NaVILA-Orca/scripts/setup_system_deps.sh"
    )


def native_rpath(
    pyside6: Path, shiboken6: Path, python_lib: Path, dist: Path
) -> str:
    """Build the search path required by the OrcaLab viewport extension."""
    entries = (
        "$ORIGIN",
        str(pyside6),
        str(pyside6 / "Qt" / "lib"),
        str(shiboken6),
        str(python_lib),
        str(dist),
    )
    return ":".join(entries)


def patch_native_runtime(root: Path) -> None:
    native_library = (
        root / "src" / "orcalab_pyside" / "dist" / "OrcaPySide.so"
    )
    patchelf = Path(sys.prefix) / "bin" / "patchelf"
    if not native_library.is_file():
        raise RuntimeError(f"OrcaLab native viewport is missing: {native_library}")
    if not patchelf.is_file():
        raise RuntimeError(f"pinned patchelf executable is missing: {patchelf}")

    import PySide6
    import shiboken6

    pyside6 = Path(PySide6.__file__).resolve().parent
    shiboken6_root = Path(shiboken6.__file__).resolve().parent
    python_lib = Path(sysconfig.get_config_var("LIBDIR")).resolve()
    dist = native_library.parent.resolve()
    qt_lib = pyside6 / "Qt" / "lib"
    if not qt_lib.is_dir():
        raise RuntimeError(f"PySide6 Qt library directory is missing: {qt_lib}")

    # OrcaLab runtime builds have directly linked either libGL.so.1 or
    # libOpenGL.so.0. Bind whichever ABI each ELF actually declares to the
    # matching host GLVND library. This prevents a packaged OpenGL front end
    # from mixing with a different host libGLdispatch.so.0 build and failing
    # with an undefined _glapi_tls_Current symbol.
    opengl_consumers = [native_library, dist / "libPySideGameLauncher.so"]
    host_glvnd_libraries: set[Path] = set()
    for consumer in opengl_consumers:
        if not consumer.is_file():
            raise RuntimeError(f"OrcaLab OpenGL consumer is missing: {consumer}")
        needed = subprocess.check_output(
            [str(patchelf), "--print-needed", str(consumer)], text=True
        ).splitlines()
        declared_glvnd = glvnd_dependencies(needed)
        if not declared_glvnd:
            supported = " or ".join(GLVND_SONAMES)
            raise RuntimeError(
                f"{consumer.name} does not declare {supported}"
            )
        for current in declared_glvnd:
            host_glvnd = system_glvnd_library(Path(current).name)
            host_glvnd_libraries.add(host_glvnd)
            if current != str(host_glvnd):
                subprocess.check_call(
                    [
                        str(patchelf),
                        "--replace-needed",
                        current,
                        str(host_glvnd),
                        str(consumer),
                    ]
                )

    rpath = native_rpath(pyside6, shiboken6_root, python_lib, dist)
    subprocess.check_call(
        [str(patchelf), "--set-rpath", rpath, str(native_library)]
    )
    actual = subprocess.check_output(
        [str(patchelf), "--print-rpath", str(native_library)], text=True
    ).strip()
    if actual != rpath:
        raise RuntimeError(
            f"OrcaLab native viewport RPATH mismatch: {actual!r} != {rpath!r}"
        )
    clean_environment = os.environ.copy()
    clean_environment.pop("LD_LIBRARY_PATH", None)
    linked = subprocess.check_output(
        ["ldd", str(native_library)], text=True, env=clean_environment
    )
    missing_libraries = [
        line.strip() for line in linked.splitlines() if "not found" in line
    ]
    if missing_libraries:
        raise RuntimeError(
            "OrcaLab native viewport has unresolved libraries:\n"
            + "\n".join(missing_libraries)
        )
    unresolved_glvnd = [
        str(library)
        for library in sorted(host_glvnd_libraries)
        if str(library) not in linked
    ]
    if unresolved_glvnd:
        raise RuntimeError(
            "OrcaLab native viewport does not resolve against the complete "
            "host OpenGL stack; missing "
            f"{', '.join(unresolved_glvnd)}:\n{linked}"
        )
    resolved = ", ".join(str(library) for library in sorted(host_glvnd_libraries))
    print(
        "[orcalab-runtime] native viewport RPATH and host OpenGL verified: "
        f"{resolved}"
    )


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("Orca_VLN currently supports the OrcaLab runtime on Linux")
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_orcalab_runtime.py CONSTRAINTS_FILE")

    constraints = Path(sys.argv[1]).resolve()
    if not constraints.is_file():
        raise SystemExit(f"constraints file does not exist: {constraints}")

    from orcalab.project_util import get_cache_folder, project_id

    user_root = (
        Path.home() / "Orca" / "OrcaStudio" / project_id / "user"
    )
    # OrcaLab 26.7.1 loads its external viewport from a versioned directory.
    # Older project setup runs cached the same verified archive under the
    # "unknown" name, so reuse that cache while installing and patching the
    # directory the GUI actually imports.
    url_version = ORCALAB_VERSION
    archive = runtime_archive(user_root)
    destination = user_root / f"orcalab-pyside-{url_version}"
    state_file = user_root / ".orcalab-pyside-install-state.json"
    pak = Path(get_cache_folder()) / Path(PAK_URL).name

    download_verified(PYSIDE_URL, archive, PYSIDE_SHA256)
    root = extract_runtime(archive, destination)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--constraint",
            str(constraints),
            "--editable",
            str(root),
        ]
    )
    patch_native_runtime(root)
    download_verified(PAK_URL, pak, PAK_SHA256)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "installed_url": PYSIDE_URL,
                "installed_path": None,
                "url_version": url_version,
                "installed_at": str(Path.cwd()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("[orcalab-runtime] native viewport and pak are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
