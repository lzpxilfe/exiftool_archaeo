#!/usr/bin/env python3
"""Frozen-app entry point: bundled ExifTool, GUI/CLI dispatch and build self-test."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback


def configure_runtime() -> Path | None:
    """Prefer the bundled engine by default; keep an explicit user override."""
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys._MEIPASS)
    engine = root / "vendor" / "exiftool" / "exiftool.exe"
    if not engine.is_file() or not (engine.parent / "exiftool_files").is_dir():
        raise RuntimeError("Bundled ExifTool is incomplete. Rebuild with build_windows.bat.")
    if not os.environ.get("EXIFTOOL_PATH", "").strip():
        os.environ["EXIFTOOL_PATH"] = str(engine)
    # PyInstaller windowed builds have None stdout/stderr. CLI progress uses flush.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    return engine


def self_test(report_path: Path, bundled_engine: Path | None) -> int:
    """Exercise the bundled engine and app pipeline, using only synthetic photos."""
    report = {"ok": False}
    try:
        import archaeo_gps as ag
        from PIL import Image
        import tkinter

        engine = Path(ag.find_exiftool())
        if bundled_engine is None or engine.resolve() != bundled_engine.resolve():
            raise RuntimeError("Self-test must use the embedded ExifTool, not an external copy")
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        opts = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
                    check=True, timeout=30, startupinfo=startup)
        report["exiftool_version"] = subprocess.run([str(engine), "-ver"], **opts).stdout.strip()
        report["tcl_version"] = tkinter.Tcl().eval("info patchlevel")
        with tempfile.TemporaryDirectory(prefix="Archaeo-selftest-") as tmp:
            photo = Path(tmp) / "지표 조사 사진.jpg"
            Image.new("RGB", (32, 24)).save(photo)
            # Write GPS only to this synthetic image. No user photo is touched.
            args_file = Path(tmp) / "write-gps.args"
            args_file.write_text(
                "-overwrite_original\n-GPSLatitude=36.5\n-GPSLatitudeRef=N\n"
                "-GPSLongitude=127.5\n-GPSLongitudeRef=E\n" + str(photo) + "\n",
                encoding="utf-8",
            )
            subprocess.run([str(engine), "-charset", "filename=UTF8",
                            "-@", str(args_file)], **opts)
            raw, errors = ag.run_exiftool(str(engine), [str(photo)])
            if errors or len(raw) != 1:
                raise RuntimeError(f"EXIF read failed: {errors}")
            record = ag.parse_record(raw[0])
            if record["GPSValidation"] != "valid" or abs(record["Lat_DD"] - 36.5) > 1e-8:
                raise RuntimeError("Synthetic GPS record did not round-trip")
            if abs(record["Lon_DD"] - 127.5) > 1e-8:
                raise RuntimeError("Synthetic longitude did not round-trip")
            transformer = ag.build_transformers("epsg:5186")
            x, y = ag.transform_coords(transformer, record["Lat_DD"], record["Lon_DD"])
            if x is None or y is None or not (math.isfinite(x) and math.isfinite(y)):
                raise RuntimeError("Bundled PROJ coordinate conversion failed")
            if not ag.extract_thumbnail(str(photo), max_size=24, exiftool_path=str(engine)):
                raise RuntimeError("Bundled Pillow thumbnail generation failed")
            ag.write_csv([record], str(Path(tmp) / "out.csv"), transformer, "epsg:5186")
            if not ag.write_geojson([record], str(Path(tmp) / "out.geojson")):
                raise RuntimeError("GeoJSON export failed")
            if not ag.write_map([record], str(Path(tmp) / "out.html"), thumb_size=0):
                raise RuntimeError("HTML generation failed")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main() -> int:
    bundled_engine = configure_runtime()
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        if len(sys.argv) != 3:
            raise ValueError("Usage: ExifToolArchaeo.exe --self-test report.json")
        return self_test(Path(sys.argv[2]), bundled_engine)
    from archaeo_gps_gui import run_gui, run_cli
    if len(sys.argv) <= 1:
        run_gui()
    else:
        run_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
