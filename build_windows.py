#!/usr/bin/env python3
"""Build a self-contained Windows app; invoked by build_windows.bat.

Only the official, hash-pinned ExifTool archive is accepted. No system PATH,
registry, global Python packages, or photo files are modified by this builder.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import traceback
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parent
EXIFTOOL_VERSION = "13.59"
EXIFTOOL_ARCHIVE = f"exiftool-{EXIFTOOL_VERSION}_64.zip"
# Official manifest: https://exiftool.sourceforge.net/checksums.txt
EXIFTOOL_SHA256 = "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec"
EXIFTOOL_URLS = (
    f"https://downloads.sourceforge.net/project/exiftool/{EXIFTOOL_ARCHIVE}",
    f"https://sourceforge.net/projects/exiftool/files/{EXIFTOOL_ARCHIVE}/download",
)
MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_archive(cache: Path) -> Path:
    """Reuse a verified ZIP or download one, never execute unchecked bytes."""
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / EXIFTOOL_ARCHIVE
    if archive.is_file() and sha256_file(archive) == EXIFTOOL_SHA256:
        print("[ExifTool] Verified cached archive.", flush=True)
        return archive
    if archive.exists():
        raise RuntimeError(
            f"Cached ZIP checksum mismatch: {archive}\n"
            "Remove that ZIP and retry. The unverified file was NOT executed."
        )
    failures = []
    for url in EXIFTOOL_URLS:
        part = cache / (EXIFTOOL_ARCHIVE + ".part")
        try:
            print(f"[ExifTool] Downloading {url}", flush=True)
            request = Request(url, headers={"User-Agent": "ExifToolArchaeo-WindowsBuild/1.0"})
            with urlopen(request, timeout=45) as response, part.open("wb") as output:
                if urlparse(response.geturl()).scheme != "https":
                    raise RuntimeError("Refusing a non-HTTPS download redirect")
                size = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_DOWNLOAD:
                        raise RuntimeError("Download exceeds the size limit")
                    output.write(block)
            if sha256_file(part) != EXIFTOOL_SHA256:
                raise RuntimeError("Official SHA-256 mismatch (possibly an HTML error page)")
            part.replace(archive)
            return archive
        except (OSError, RuntimeError) as exc:
            failures.append(f"{url}: {exc}")
        finally:
            part.unlink(missing_ok=True)
    raise RuntimeError(
        "Could not download the verified ExifTool ZIP.\n" + "\n".join(failures)
        + f"\nAlternatively, save the official {EXIFTOOL_ARCHIVE} in {cache} and rerun."
    )


def safe_extract(archive: Path, destination: Path) -> None:
    """Reject traversal, Windows drive/ADS paths and links before extraction."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 20000 or sum(entry.file_size for entry in entries) > MAX_UNPACKED:
            raise ValueError("ExifTool archive exceeds extraction limits")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts
                    or ":" in name or "\x00" in name
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1):
                raise ValueError(f"Unsafe ZIP member: {entry.filename!r}")
            target = root.joinpath(*path.parts).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"ZIP member escapes extraction folder: {name!r}")
        for entry in entries:
            target = root.joinpath(*PurePosixPath(entry.filename.replace("\\", "/")).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def validate_engine_layout(directory: Path) -> Path:
    executable = directory / "exiftool.exe"
    libraries = directory / "exiftool_files"
    if not executable.is_file() or not libraries.is_dir():
        raise ValueError("ExifTool executable AND exiftool_files folder are required")
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Not a Windows ExifTool executable")
    if not any(libraries.glob("perl5*.dll")):
        raise ValueError("ExifTool Perl runtime DLL is missing")
    return executable


def prepare_engine(archive: Path, destination: Path) -> Path:
    """Re-extract the verified ZIP each build, preserving bundled notices/files."""
    if sha256_file(archive) != EXIFTOOL_SHA256:
        raise ValueError("ExifTool archive checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exiftool-stage-", dir=destination.parent) as tmp:
        extracted = Path(tmp)
        safe_extract(archive, extracted)
        candidates = list(extracted.rglob("exiftool(-k).exe")) + list(extracted.rglob("exiftool.exe"))
        if len(candidates) != 1:
            raise ValueError("Expected exactly one ExifTool Windows launcher in ZIP")
        launcher = candidates[0]
        package = launcher.parent
        if launcher.name != "exiftool.exe":
            launcher.rename(package / "exiftool.exe")
        validate_engine_layout(package)
        # Only our generated runtime folder is replaced, never the user's engine.
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(package, destination)
    return validate_engine_layout(destination)


def run(command: list[str], *, cwd: Path = ROOT, env=None) -> None:
    print("\n> " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def test_portable_exe(executable: Path, report_copy: Path) -> None:
    """Move ONLY the EXE elsewhere; no source, external engine or PATH fallback."""
    report_copy.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="Archaeo-portable-") as tmp:
        isolated = Path(tmp) / "한글 경로 portable test"
        isolated.mkdir()
        relocated = isolated / executable.name
        shutil.copy2(executable, relocated)
        report = isolated / "self-test.json"
        env = os.environ.copy()
        for key in ("EXIFTOOL_PATH", "PYTHONPATH", "PYTHONHOME", "ARCHAEO_EXIFTOOL_BUNDLE"):
            env.pop(key, None)
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        try:
            subprocess.run([str(relocated), "--self-test", str(report)], cwd=isolated,
                           env=env, check=True, timeout=180)
        finally:
            if report.is_file():
                report_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report, report_copy)
        if not report.is_file():
            raise RuntimeError("Packaged app did not write its self-test report")
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("ok") is not True or result.get("exiftool_version") != EXIFTOOL_VERSION:
            raise RuntimeError(f"Portable EXE self-test failed: {result}")


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Build on Windows; PyInstaller cannot cross-build a Windows EXE here")
    if sys.version_info < (3, 10) or struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("Use 64-bit x86 Python 3.10 or newer on Windows")
    import tkinter  # Fail early if Python was installed without Tcl/Tk.
    print(f"Python: {sys.version.split()[0]}; Tcl: {tkinter.Tcl().eval('info patchlevel')}", flush=True)
    cache = ROOT / ".build-cache"
    engine = prepare_engine(download_archive(cache), cache / "exiftool-runtime")
    result = subprocess.run([str(engine), "-ver"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=True, timeout=30)
    if result.stdout.strip() != EXIFTOOL_VERSION:
        raise RuntimeError("Prepared ExifTool version does not match the pinned archive")
    print(f"[ExifTool] {result.stdout.strip()} verified and ready.", flush=True)

    # This includes the existing application tests and the new packaging tests.
    run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py", "-v"])
    # Build into a temporary output folder. A failure must not replace a good EXE.
    with tempfile.TemporaryDirectory(prefix="archaeo-build-", dir=cache) as stage_name:
        stage = Path(stage_name)
        env = os.environ.copy()
        env["ARCHAEO_EXIFTOOL_BUNDLE"] = str(engine.parent)
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             "--distpath", str(stage / "dist"), "--workpath", str(stage / "work"),
             str(ROOT / "archaeo_gps.spec")], env=env)
        built = stage / "dist" / "ExifToolArchaeo.exe"
        if not built.is_file():
            raise RuntimeError("PyInstaller returned without the expected EXE")
        print("[Check] Testing the EXE alone in an unrelated Korean/spaced path...", flush=True)
        test_portable_exe(built, cache / "self-test.json")
        dist = ROOT / "dist"
        dist.mkdir(exist_ok=True)
        # Same volume, atomic replacement; close a previous EXE before rebuilding.
        shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", dist / "THIRD_PARTY_NOTICES.md")
        built.replace(dist / built.name)
        print(f"\nSUCCESS: {dist / built.name}\nOnly this EXE is needed to run the app.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
