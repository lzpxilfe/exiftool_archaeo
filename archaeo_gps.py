#!/usr/bin/env python3
"""
archaeo_gps.py — 고고학 현장 사진 GPS·방향 추출 도구
Archaeological field photo GPS & orientation extractor

Usage:
    python archaeo_gps.py -i ./photos -o output.csv
    python archaeo_gps.py -i ./photos -o output.csv --crs epsg:5186 --map map.html
    python archaeo_gps.py -i ./photos -o output.csv --exiftool ./exiftool.exe
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows 콘솔 UTF-8 강제 (이모지·한글 출력)
# console=False exe 에서는 stdout/stderr 가 None 이므로 반드시 None 체크 필요
if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Optional dependencies (graceful degradation)
# ---------------------------------------------------------------------------
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from pyproj import Transformer
    HAS_PYPROJ = True
except ImportError:
    HAS_PYPROJ = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".dng", ".arw", ".cr2", ".nef"}

EXIF_TAGS = [
    "FileName",
    "DateTimeOriginal", "CreateDate",
    "Make", "Model",
    "GPSLatitude", "GPSLatitudeRef",
    "GPSLongitude", "GPSLongitudeRef",
    "GPSAltitude", "GPSAltitudeRef",
    # DJI-specific
    "FlightYawDegree", "FlightPitchDegree", "FlightRollDegree",
    "GimbalYawDegree", "GimbalPitchDegree", "GimbalRollDegree",
    # Standard GPS direction (DSLR + external GPS)
    "GPSImgDirection", "GPSImgDirectionRef",
    "GPSSpeed", "GPSSpeedRef",
    "GPSTrack", "GPSTrackRef",
]

# Cardinal direction labels (16-point compass)
CARDINALS_16 = [
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
]

# Supported output CRS (label -> EPSG)
# -----------------------------------------------------------------------
CRS_PRESETS = {

    # WGS84 (기본 GPS 좌표)
    "wgs84":           "epsg:4326",   # WGS84 지리좌표 (위경도 DD)

    # 한국 현행 좌표계 (GRS80 타원체) - 2010년 이후 국가 표준
    "tm":              "epsg:5186",   # TM중부원점 GRS80  [기본값, 가장 많이 쓰임]
    "tm_west":         "epsg:5185",   # TM서부원점 GRS80  [서해안, 서부]
    "tm_east":         "epsg:5187",   # TM동부원점 GRS80  [동해안, 영남동부]
    "tm_eastsea":      "epsg:5188",   # TM동해원점 GRS80  [울릉도, 독도]
    "utmk":            "epsg:5179",   # UTM-K GRS80       [국토지리정보원 통합]
    "grs80_geo":       "epsg:4737",   # GRS80 지리좌표

    # 한국 구좌표계 (Bessel 1841) - 2010년 이전 지형도/문화재 도면
    "tm_old":          "epsg:5174",   # 구TM중부원점 Bessel  [구 내륙 표준]
    "tm_old_west":     "epsg:5173",   # 구TM서부원점 Bessel
    "tm_old_east":     "epsg:5176",   # 구TM동부원점 Bessel
    "tm_old_eastsea":  "epsg:5177",   # 구TM동해원점 Bessel  [구 울릉]
    "tm_mod":          "epsg:5181",   # 수정중부원점 Bessel  [2010년 경과조치]
    "tm_mod_west":     "epsg:5182",   # 수정서부원점 Bessel
    "tm_mod_east":     "epsg:5183",   # 수정동부원점 Bessel
    "bessel_geo":      "epsg:4162",   # Bessel 지리좌표

    # 국제 / 글로벌
    "utm52n":          "epsg:32652",  # UTM Zone 52N  [한반도 전역]
    "utm51n":          "epsg:32651",  # UTM Zone 51N  [서해 일부]
    "web_mercator":    "epsg:3857",   # Web Mercator  [Google/Kakao 지도]
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def dms_to_dd(dms_str: str) -> float | None:
    """
    Convert ExifTool DMS string to Decimal Degrees.
    Handles both:
      '36 deg 22' 14.45" N'
      '36.371791666'   (already decimal)
    Returns None on failure.
    """
    if dms_str is None:
        return None
    s = str(dms_str).strip()

    # Already decimal?
    try:
        val = float(s.replace("°", "").strip())
        # Sanity check: plain numbers from exiftool -n are decimal
        return val
    except ValueError:
        pass

    # DMS pattern: 36 deg 22' 14.45" N
    pattern = r"""
        (\d+)\s*deg\s+   # degrees
        (\d+)['′]\s*     # minutes
        ([\d.]+)[\"″]\s* # seconds
        ([NSEW])?        # optional hemisphere
    """
    m = re.search(pattern, s, re.VERBOSE | re.IGNORECASE)
    if not m:
        return None

    deg, mn, sec, hemi = m.groups()
    dd = float(deg) + float(mn) / 60.0 + float(sec) / 3600.0

    if hemi and hemi.upper() in ("S", "W"):
        dd = -dd
    return dd


def altitude_to_float(alt_str: str) -> float | None:
    """Extract numeric altitude from ExifTool string like '525.2 m Above Sea Level'."""
    if alt_str is None:
        return None
    m = re.search(r"([-\d.]+)", str(alt_str))
    return float(m.group(1)) if m else None


def deg_to_cardinal(deg: float | None, points: int = 16) -> str:
    """Convert bearing angle (0–360) to compass cardinal label."""
    if deg is None:
        return ""
    deg = deg % 360
    labels = CARDINALS_16 if points == 16 else ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(deg / (360 / len(labels))) % len(labels)
    return labels[idx]


def normalize_yaw(yaw: float | None) -> float | None:
    """Normalize yaw to 0–360 (DJI uses -180 to +180)."""
    if yaw is None:
        return None
    return yaw % 360


def find_exiftool(hint: str | None) -> str:
    """Locate exiftool executable.
    Search order:
      1. hint (--exiftool argument)
      2. Same folder as the running exe (PyInstaller frozen build)
      3. Same folder as this script
      4. System PATH
    """
    candidates = []
    if hint:
        candidates.append(hint)

    # PyInstaller frozen exe: look next to the .exe first
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates += [
            str(exe_dir / "exiftool.exe"),
            str(exe_dir / "exiftool"),
        ]

    # Script directory
    script_dir = Path(__file__).parent
    candidates += [
        str(script_dir / "exiftool.exe"),
        str(script_dir / "exiftool"),
        "exiftool",
        "exiftool.exe",
    ]
    for c in candidates:
        try:
            result = subprocess.run(
                [c, "-ver"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return c
        except (FileNotFoundError, OSError):
            continue
    raise FileNotFoundError(
        "exiftool을 찾을 수 없습니다. --exiftool 옵션으로 경로를 지정하거나 PATH에 추가하세요.\n"
        "  다운로드: https://exiftool.org"
    )


def run_exiftool(exiftool_path: str, image_paths: list[str]) -> list[dict]:
    """Run exiftool and return parsed JSON list."""
    tag_args = [f"-{t}" for t in EXIF_TAGS]
    cmd = [exiftool_path, "-json", "-charset", "filename=UTF8"] + tag_args + image_paths

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("⚠️  ExifTool 실행 시간 초과", file=sys.stderr)
        return []

    if result.returncode not in (0, 1):
        print(f"⚠️  ExifTool 오류: {result.stderr[:300]}", file=sys.stderr)
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"⚠️  JSON 파싱 오류: {e}", file=sys.stderr)
        return []


def collect_images(input_path: str) -> list[str]:
    """Collect all supported image paths from file or directory."""
    p = Path(input_path)
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        files = []
        for ext in SUPPORTED_EXT:
            files.extend(p.glob(f"*{ext}"))
            files.extend(p.glob(f"*{ext.upper()}"))
        return sorted(str(f) for f in set(files))
    raise FileNotFoundError(f"입력 경로를 찾을 수 없습니다: {input_path}")


def build_transformers(target_crs: str) -> tuple | None:
    """Build WGS84 → target CRS transformer pair."""
    if not HAS_PYPROJ:
        return None
    try:
        fwd = Transformer.from_crs("epsg:4326", target_crs, always_xy=True)
        return fwd
    except Exception as e:
        print(f"⚠️  좌표 변환기 초기화 실패 ({target_crs}): {e}", file=sys.stderr)
        return None


def transform_coords(transformer, lat: float, lon: float) -> tuple[float | None, float | None]:
    """Transform WGS84 lat/lon to target CRS. Returns (x, y)."""
    if transformer is None or lat is None or lon is None:
        return None, None
    try:
        x, y = transformer.transform(lon, lat)
        return x, y
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Record processing
# ---------------------------------------------------------------------------

def parse_record(raw: dict) -> dict:
    """Parse one ExifTool JSON record into a clean dict."""
    r = {}

    # File info
    r["FileName"] = raw.get("FileName", "")
    r["SourceFile"] = raw.get("SourceFile", "")
    r["DateTime"] = raw.get("DateTimeOriginal") or raw.get("CreateDate", "")
    r["Make"] = raw.get("Make", "")
    r["Model"] = raw.get("Model", "")

    # GPS coordinates
    lat_raw = raw.get("GPSLatitude")
    lon_raw = raw.get("GPSLongitude")

    lat = dms_to_dd(lat_raw)
    lon = dms_to_dd(lon_raw)

    # Apply hemisphere refs if not embedded in string
    if lat is not None and lat > 0:
        lat_ref = str(raw.get("GPSLatitudeRef", "N")).strip().upper()
        if lat_ref == "S":
            lat = -lat

    if lon is not None and lon > 0:
        lon_ref = str(raw.get("GPSLongitudeRef", "E")).strip().upper()
        if lon_ref == "W":
            lon = -lon

    r["Lat_DD"] = round(lat, 8) if lat is not None else None
    r["Lon_DD"] = round(lon, 8) if lon is not None else None
    r["Alt_m"] = altitude_to_float(raw.get("GPSAltitude"))

    # ----------------------------------------------------------------
    # Direction / orientation
    # Priority: DJI Gimbal > DJI Flight > EXIF GPSImgDirection
    # ----------------------------------------------------------------
    gimbal_yaw   = _to_float(raw.get("GimbalYawDegree"))
    gimbal_pitch = _to_float(raw.get("GimbalPitchDegree"))
    gimbal_roll  = _to_float(raw.get("GimbalRollDegree"))
    flight_yaw   = _to_float(raw.get("FlightYawDegree"))
    flight_pitch = _to_float(raw.get("FlightPitchDegree"))
    flight_roll  = _to_float(raw.get("FlightRollDegree"))
    gps_dir      = _to_float(raw.get("GPSImgDirection"))
    gps_dir_ref  = str(raw.get("GPSImgDirectionRef", "")).strip()

    r["GimbalYaw"]   = round(gimbal_yaw, 2)   if gimbal_yaw   is not None else None
    r["GimbalPitch"] = round(gimbal_pitch, 2) if gimbal_pitch is not None else None
    r["GimbalRoll"]  = round(gimbal_roll, 2)  if gimbal_roll  is not None else None
    r["FlightYaw"]   = round(flight_yaw, 2)   if flight_yaw   is not None else None
    r["FlightPitch"] = round(flight_pitch, 2) if flight_pitch is not None else None
    r["FlightRoll"]  = round(flight_roll, 2)  if flight_roll  is not None else None
    r["GPSImgDirection"]    = round(gps_dir, 2) if gps_dir is not None else None
    r["GPSImgDirectionRef"] = gps_dir_ref

    # Camera pointing direction (best available)
    cam_dir = gimbal_yaw if gimbal_yaw is not None else (
              flight_yaw if flight_yaw is not None else gps_dir)
    cam_dir_norm = normalize_yaw(cam_dir)
    r["CamDirection_deg"]      = round(cam_dir_norm, 2) if cam_dir_norm is not None else None
    r["CamDirection_cardinal"] = deg_to_cardinal(cam_dir_norm)

    return r


def _to_float(val) -> float | None:
    """Safe float conversion."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Thumbnail extraction (Pillow)
# ---------------------------------------------------------------------------

try:
    from PIL import Image as _PILImage
    import io as _io
    import base64 as _base64
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def extract_thumbnail(source_file: str, max_size: int = 320) -> str | None:
    """
    Open the original photo, resize to thumbnail, return base64 JPEG string.
    Returns None if Pillow is unavailable or file cannot be opened.
    """
    if not HAS_PILLOW:
        return None
    try:
        with _PILImage.open(source_file) as img:
            # Preserve EXIF orientation
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img.thumbnail((max_size, max_size), _PILImage.LANCZOS)
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=72)
            return _base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "FileName", "DateTime", "Make", "Model",
    "Lat_DD", "Lon_DD", "Alt_m",
    "CamDirection_deg", "CamDirection_cardinal",
    "GimbalYaw", "GimbalPitch", "GimbalRoll",
    "FlightYaw", "FlightPitch", "FlightRoll",
    "GPSImgDirection", "GPSImgDirectionRef",
    "Proj_X", "Proj_Y", "Proj_CRS",
    "SourceFile",
]


def write_csv(records: list[dict], output_path: str, transformer=None, target_crs: str = ""):
    """Write records to CSV."""
    rows = []
    for rec in records:
        row = {col: rec.get(col, "") for col in CSV_COLUMNS}

        # Add projected coordinates
        x, y = transform_coords(transformer, rec.get("Lat_DD"), rec.get("Lon_DD"))
        row["Proj_X"] = round(x, 3) if x is not None else ""
        row["Proj_Y"] = round(y, 3) if y is not None else ""
        row["Proj_CRS"] = target_crs if (x is not None) else ""
        rows.append(row)

    if HAS_PANDAS:
        import pandas as pd
        df = pd.DataFrame(rows, columns=CSV_COLUMNS)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
    else:
        import csv
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    print(f"✅ CSV 저장: {output_path}  ({len(rows)}개 레코드)")








# ---------------------------------------------------------------------------
# Leaflet HTML map generation
# ---------------------------------------------------------------------------

def write_map(records: list[dict], output_path: str):
    """
    Generate a Leaflet.js HTML map with:
      - Side panel layout
      - Zoomable/Pannable photo Lightbox using mouse scroll wheel and drag
      - Tile layers with custom CSS opacity and blend-mode (multiply) mix
      - Nadir target icons for vertical shots (Pitch <= -85)
    """

    features = []
    for rec in records:
        lat = rec.get("Lat_DD")
        lon = rec.get("Lon_DD")
        if lat is None or lon is None:
            continue

        yaw          = rec.get("CamDirection_deg")
        card         = rec.get("CamDirection_cardinal", "")
        fname        = rec.get("FileName", "")
        source_file  = rec.get("SourceFile", "")
        dt           = rec.get("DateTime", "")
        alt          = rec.get("Alt_m", "")
        make         = rec.get("Make", "")
        model        = rec.get("Model", "")
        gimbal_pitch = rec.get("GimbalPitch")
        gimbal_roll  = rec.get("GimbalRoll")
        flight_yaw   = rec.get("FlightYaw")
        gimbal_yaw   = rec.get("GimbalYaw")

        yaw_display = f"{yaw}° ({card})" if yaw is not None else "N/A"

        # Nadir check (straight down)
        is_nadir = False
        if gimbal_pitch is not None:
            try:
                if float(gimbal_pitch) <= -85.0:
                    is_nadir = True
            except (ValueError, TypeError):
                pass

        # High resolution base64 thumbnail (640px)
        thumb_b64 = extract_thumbnail(source_file, max_size=640) if source_file else None

        alt_val = f"{alt} m" if alt is not None else "N/A"
        gp_val = f"{gimbal_pitch}°" if gimbal_pitch is not None else "N/A"
        gr_val = f"{gimbal_roll}°" if gimbal_roll is not None else "N/A"
        gy_val = f"{gimbal_yaw}°" if gimbal_yaw is not None else "N/A"
        fy_val = f"{flight_yaw}°" if flight_yaw is not None else "N/A"

        popup_html = f"""
<div style="font-family:'Segoe UI',sans-serif;font-size:0.8rem;">
  <b>{fname}</b>
</div>"""

        features.append({
            "lat": lat,
            "lon": lon,
            "yaw": yaw if yaw is not None else 0,
            "has_dir": yaw is not None,
            "is_nadir": is_nadir,
            "popup": popup_html,
            "fname": fname,
            "dt": dt or "N/A",
            "alt": alt_val,
            "make": make or "N/A",
            "model": model or "N/A",
            "gimbal_yaw": gy_val,
            "gimbal_pitch": gp_val,
            "gimbal_roll": gr_val,
            "flight_yaw": fy_val,
            "yaw_display": yaw_display,
            "thumb_b64": thumb_b64 if thumb_b64 else "",
        })

    if not features:
        print("⚠️  지도에 표시할 GPS 데이터가 없습니다.", file=sys.stderr)
        return

    center_lat = sum(f["lat"] for f in features) / len(features)
    center_lon = sum(f["lon"] for f in features) / len(features)
    features_json = json.dumps(features, ensure_ascii=False, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ExifTool Archaeo — 고고학 현장 GPS 분석 맵</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Noto Sans KR', 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}
    
    #header {{
      padding: 10px 20px;
      background: linear-gradient(135deg, #16213e, #0f3460);
      border-bottom: 2px solid #e94560;
      display: flex; align-items: center; gap: 12px;
      height: 48px;
      flex-shrink: 0;
      z-index: 1000;
    }}
    #header h1 {{ font-size: 1.05rem; font-weight: 700; letter-spacing: 0.3px; color: #fff; }}
    #header .sub {{ font-size: 0.8rem; color: #a0aec0; }}
    
    #app-container {{
      display: flex;
      flex: 1;
      width: 100%;
      height: calc(100vh - 48px);
      position: relative;
    }}
    
    #map {{
      flex: 1;
      height: 100%;
      z-index: 1;
    }}
    
    #sidebar {{
      width: 380px;
      background: #16213e;
      border-left: 2px solid #e94560;
      height: 100%;
      overflow-y: auto;
      padding: 20px;
      color: #eee;
      z-index: 5;
      flex-shrink: 0;
      box-shadow: -4px 0 15px rgba(0,0,0,0.5);
    }}
    
    .sidebar-table {{
      width: 100%;
      font-size: 0.85rem;
      border-collapse: collapse;
      margin-top: 15px;
    }}
    .sidebar-table tr {{
      border-bottom: 1px solid #2d3748;
    }}
    .sidebar-table td {{
      padding: 8px 6px;
      vertical-align: middle;
    }}
    .sidebar-table td.label-col {{
      color: #94a3b8;
      width: 110px;
    }}
    
    .sidebar-thumb {{
      width: 100%;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      margin-bottom: 15px;
      max-height: 220px;
      object-fit: contain;
      background: #090e1a;
      cursor: zoom-in;
      transition: transform 0.2s ease;
      display: block;
    }}
    .sidebar-thumb:hover {{
      transform: scale(1.02);
    }}
    
    .legend {{
      background: rgba(22, 33, 62, 0.93);
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid #e94560;
      font-size: 0.76rem;
      color: #eee;
      line-height: 1.9;
    }}
    .legend-dot {{ display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
    
    /* ── 라이트박스(마우스 휠 줌 및 드래그 판 지원) ── */
    #lightbox {{
      display: none;
      position: fixed;
      z-index: 9999;
      top: 0; left: 0;
      width: 100vw; height: 100vh;
      background: rgba(10, 10, 25, 0.94);
      align-items: center;
      justify-content: center;
      overflow: hidden;
      user-select: none;
    }}
    #lightbox-img {{
      max-width: 90%;
      max-height: 90%;
      border-radius: 6px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.8);
      border: 2px solid #e94560;
      cursor: grab;
      transform-origin: center center;
      transition: transform 0.05s ease-out;
    }}
    
    .lightbox-close {{
      position: absolute;
      top: 20px; right: 20px;
      font-size: 2rem; color: #fff;
      cursor: pointer; z-index: 10000;
      background: rgba(233, 69, 96, 0.8);
      width: 45px; height: 45px;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 10px rgba(0,0,0,0.3);
      transition: background 0.2s;
    }}
    .lightbox-close:hover {{
      background: rgb(233, 69, 96);
    }}
    .lightbox-guide {{
      position: absolute;
      bottom: 20px; left: 50%;
      transform: translateX(-50%);
      background: rgba(22, 33, 62, 0.85);
      border: 1px solid #e94560;
      padding: 8px 16px;
      border-radius: 20px;
      font-size: 0.8rem; color: #eee;
      z-index: 10000;
      pointer-events: none;
    }}

    /* ── Leaflet CSS Multiply (곱하기) 융합 레이어 스타일 ── */
    .blend-multiply {{
      mix-blend-mode: multiply;
      filter: contrast(1.1) brightness(0.95);
    }}
  </style>
</head>
<body>
  <div id="header">
    <span style="font-size:1.3rem;">🏛️</span>
    <h1>ExifTool Archaeo</h1>
    <span class="sub">고고학 현장 사진 위치·방향 시각화</span>
    <span style="margin-left:auto;color:#e94560;font-weight:700;">{len(features)}장</span>
  </div>
  
  <div id="app-container">
    <div id="map"></div>
    <div id="sidebar">
      <div id="sidebar-placeholder" style="text-align: center; margin-top: 60px; color: #a0aec0; line-height: 1.8;">
        <span style="font-size: 3.5rem; display: block; margin-bottom: 15px;">📸</span>
        <b style="color: #fff; font-size: 0.95rem;">사진 상세 정보 패널</b><br>
        지도 위 화살표 마커를 클릭하시면<br>촬영 방향 정보와 사진 썸네일이<br>이곳에 상세히 연동되어 표시됩니다.
      </div>
      <div id="sidebar-content" style="display: none;">
        <!-- JS 동적 렌더링 -->
      </div>
    </div>
  </div>

  <!-- 라이트박스 구조 -->
  <div id="lightbox" onclick="closeLightbox()">
    <div class="lightbox-close" onclick="closeLightbox()">&times;</div>
    <div class="lightbox-guide">🖱️ 휠을 굴려 확대/축소, 마우스로 드래그하여 이동할 수 있습니다.</div>
    <img id="lightbox-img" src="" onclick="event.stopPropagation()">
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    // 맵 선언 (무제한 줌인 세팅)
    const map = L.map('map', {{
      maxZoom: 22,
      zoomControl: true
    }}).setView([{center_lat}, {center_lon}], 16);

    // ── 베이스 레이어 및 오버레이 (곱하기 융합 및 투명도 조절 지원) ──
    const satellite = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
      {{ 
        attribution: 'Tiles &copy; Esri', 
        maxNativeZoom: 18, 
        maxZoom: 22 
      }}
    );

    const labels = L.tileLayer(
      'https://{{s}}.basemaps.cartocdn.com/light_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png',
      {{ 
        attribution: '&copy; CartoDB', 
        maxNativeZoom: 18, 
        maxZoom: 22 
      }}
    );

    const osm = L.tileLayer(
      'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
      {{ 
        attribution: '&copy; OSM', 
        maxNativeZoom: 19, 
        maxZoom: 22 
      }}
    );

    // 1. 위성 하이브리드 조합 레이어 (Esri 위성 + CartoDB 라벨)
    const hybrid = L.layerGroup([satellite, labels]);

    // 2. OSM 곱하기 융합 레이어 (위성 위에 얹을 수도 있고 단독 사용도 가능하도록)
    // CSS mix-blend-mode: multiply와 opacity: 0.65를 조합하여 위성사진과 완벽 융합
    const osmMultiply = L.tileLayer(
      'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
      {{ 
        attribution: '&copy; OSM', 
        maxNativeZoom: 19, 
        maxZoom: 22,
        className: 'blend-multiply',
        opacity: 0.65
      }}
    );

    // 곱하기 하이브리드 조합 레이어 (Esri 위성 + OSM 곱하기 오버레이)
    const multiplyCombo = L.layerGroup([satellite, osmMultiply]);

    // 기본 레이어로 일반 위성 하이브리드 적용
    hybrid.addTo(map);

    L.control.layers(
      {{ 
        "🛰 위성 하이브리드 (위성+라벨)": hybrid,
        "✖ 위성+OSM 곱하기 융합 (Multiply)": multiplyCombo,
        "🛰 위성 단독": satellite,
        "🗺 일반 지도 (OSM)": osm 
      }},
      {{ 
        "🏷 지명/도로명 라벨 (단독 토글)": labels,
        "✖ OSM 곱하기 레이어 (개별 오버레이)": osmMultiply 
      }},
      {{ position: 'topright', collapsed: false }}
    ).addTo(map);

    // ── 라이트박스 휠 줌 및 드래그 판 구현 ──────────────────────────────────
    let scale = 1;
    let translateX = 0;
    let translateY = 0;
    let isDragging = false;
    let startX = 0;
    let startY = 0;

    const lb = document.getElementById('lightbox');
    const lbImg = document.getElementById('lightbox-img');

    function openLightbox(src) {{
      lbImg.src = src;
      scale = 1;
      translateX = 0;
      translateY = 0;
      updateTransform();
      
      lb.style.display = 'flex';
      lb.offsetHeight;
      lb.classList.add('active');
    }}

    function closeLightbox() {{
      lb.classList.remove('active');
      setTimeout(() => {{
        lb.style.display = 'none';
        lbImg.src = '';
      }}, 200);
    }}

    function updateTransform() {{
      lbImg.style.transform = `translate(${{translateX}}px, ${{translateY}}px) scale(${{scale}})`;
    }}

    // 마우스 휠 이벤트 (Zoom)
    lbImg.addEventListener('wheel', function(e) {{
      e.preventDefault();
      const zoomFactor = 0.12;
      if (e.deltaY < 0) {{
        scale += zoomFactor;
      }} else {{
        scale -= zoomFactor;
      }}
      scale = Math.min(Math.max(0.4, scale), 6.0);
      updateTransform();
    }});

    // 마우스 드래그 이벤트 (Pan)
    lbImg.addEventListener('mousedown', function(e) {{
      e.preventDefault();
      isDragging = true;
      startX = e.clientX - translateX;
      startY = e.clientY - translateY;
      lbImg.style.cursor = 'grabbing';
    }});

    window.addEventListener('mousemove', function(e) {{
      if (!isDragging) return;
      translateX = e.clientX - startX;
      translateY = e.clientY - startY;
      updateTransform();
    }});

    window.addEventListener('mouseup', function() {{
      isDragging = false;
      lbImg.style.cursor = 'grab';
    }});

    // ── 아이콘 팩토리 (수직촬영 Nadir vs 경사촬영 Yaw 화살표) ───────────────────
    function makeCustomIcon(f) {{
      const color = f.has_dir ? '#e94560' : '#718096';
      let svgHtml = '';

      if (f.is_nadir) {{
        svgHtml = `
          <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
            <circle cx="18" cy="18" r="12" fill="none" stroke="${{color}}" stroke-width="2.5" stroke-dasharray="2 2"/>
            <circle cx="18" cy="18" r="7" fill="none" stroke="${{color}}" stroke-width="1.8"/>
            <circle cx="18" cy="18" r="3" fill="${{color}}"/>
            <line x1="18" y1="2" x2="18" y2="34" stroke="${{color}}" stroke-width="1" stroke-opacity="0.6"/>
            <line x1="2" y1="18" x2="34" y2="18" stroke="${{color}}" stroke-width="1" stroke-opacity="0.6"/>
          </svg>`;
      }} else {{
        const arrowSvg = f.has_dir ? `
          <line x1="18" y1="18" x2="18" y2="4"
            stroke="${{color}}" stroke-width="2.5" stroke-linecap="round"
            transform="rotate(${{f.yaw}},18,18)"/>
          <polygon points="18,1 14.5,9 21.5,9"
            fill="${{color}}" transform="rotate(${{f.yaw}},18,18)"/>` : '';
        svgHtml = `
          <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
            <circle cx="18" cy="18" r="11" fill="${{color}}" fill-opacity="0.18" stroke="${{color}}" stroke-width="2"/>
            <circle cx="18" cy="18" r="3.5" fill="${{color}}"/>
            ${{arrowSvg}}
          </svg>`;
      }}

      return L.divIcon({{
        html: svgHtml, 
        className: '',
        iconSize: [36,36], 
        iconAnchor: [18,18], 
        popupAnchor: [0,-22],
      }});
    }}

    // ── 데이터 연동 ────────────────────────────────────────────────────────
    const features = {features_json};
    const markerGroup = L.featureGroup();

    features.forEach(f => {{
      const marker = L.marker([f.lat, f.lon], {{ icon: makeCustomIcon(f) }})
        .bindPopup(f.popup, {{ maxWidth: 200 }});
      
      // 마커 클릭 시 우측 사이드바 로드
      marker.on('click', function() {{
        document.getElementById('sidebar-placeholder').style.display = 'none';
        const contentDiv = document.getElementById('sidebar-content');
        contentDiv.style.display = 'block';
        
        let imgHtml = '';
        if (f.thumb_b64) {{
          imgHtml = `<img class="sidebar-thumb" src="data:image/jpeg;base64,${{f.thumb_b64}}" onclick="openLightbox(this.src)" title="클릭하면 마우스 휠로 확대/축소 가능">`;
        }} else {{
          imgHtml = `<div style="width:100%; height:160px; background:#0f2044; border-radius:8px; display:flex; align-items:center; justify-content:center; color:#718096; margin-bottom:15px; border: 1px dashed #2d3748; font-size:0.8rem;">썸네일 이미지 없음</div>`;
        }}

        const angleLabel = f.is_nadir ? '<span style="background:#e94560; color:#fff; padding:2px 6px; border-radius:4px; font-size:0.7rem; margin-left:6px; vertical-align:middle;">수직 촬영</span>' : '';

        contentDiv.innerHTML = `
          ${{imgHtml}}
          <h2 style="font-size:1.15rem; font-weight:700; margin-bottom:4px; color:#e94560; word-break:break-all; line-height:1.35;">${{f.fname}}${{angleLabel}}</h2>
          <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:15px;">🕐 촬영시각: ${{f.dt}}</div>
          <hr style="border:none; border-top:1px solid #2d3748; margin-bottom:15px;">
          
          <table class="sidebar-table">
            <tr><td class="label-col">📍 위도/경도</td><td style="font-family:monospace; font-weight:bold; color:#fff;">${{f.lat.toFixed(6)}}, ${{f.lon.toFixed(6)}}</td></tr>
            <tr><td class="label-col">⬆ GPS 고도</td><td style="color:#fff;">${{f.alt}}</td></tr>
            <tr><td class="label-col">🧭 카메라 방향</td><td style="font-weight:bold; color:#e94560; font-size:0.9rem;">${{f.yaw_display}}</td></tr>
            <tr><td class="label-col" style="padding-left:15px;">짐벌 Yaw</td><td style="font-family:monospace; color:#e2e8f0;">${{f.gimbal_yaw}}</td></tr>
            <tr><td class="label-col" style="padding-left:15px;">짐벌 Pitch</td><td style="font-family:monospace; color:#e2e8f0;">${{f.gimbal_pitch}}</td></tr>
            <tr><td class="label-col" style="padding-left:15px;">짐벌 Roll</td><td style="font-family:monospace; color:#e2e8f0;">${{f.gimbal_roll}}</td></tr>
            <tr><td class="label-col" style="padding-left:15px;">기체 Yaw</td><td style="font-family:monospace; color:#e2e8f0;">${{f.flight_yaw}}</td></tr>
            <tr><td class="label-col">📷 카메라 장비</td><td style="color:#fff;">${{f.make}} ${{f.model}}</td></tr>
          </table>
        `;
      }});

      markerGroup.addLayer(marker);
    }});

    markerGroup.addTo(map);
    map.fitBounds(markerGroup.getBounds().pad(0.18));

    // ── 범례 ─────────────────────────────────────────────────────────────
    const legend = L.control({{position:'bottomleft'}});
    legend.onAdd = () => {{
      const d = L.DomUtil.create('div','legend');
      d.innerHTML = `
        <b>범례</b><br>
        <span class="legend-dot" style="background:#e94560"></span> 경사 촬영 (화살표=바라보는 방향)<br>
        <span class="legend-dot" style="background:#718096"></span> 방향 정보 없음<br>
        <span style="display:inline-block; width:12px; height:12px; margin-right:6px; border:2px dashed #e94560; border-radius:50%; vertical-align:middle;"></span> 수직 촬영 (Pitch 90° 부근 과녁 마커)<br>
        <span style="font-size:0.7rem;color:#a0aec0;">* 사이드바 사진 클릭 후 마우스 휠을 굴려 확대/축소 및 드래그 이동이 가능합니다.</span>`;
      return d;
    }};
    legend.addTo(map);
  </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🗺️  지도 저장: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archaeo_gps",
        description=(
            "고고학 현장 사진 GPS·방향 추출 도구\n"
            "드론(DJI) 및 DSLR 사진에서 좌표·방위각을 추출하고 CSV/HTML 지도로 출력합니다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python archaeo_gps.py -i ./photos -o output.csv
  python archaeo_gps.py -i ./photos -o output.csv --crs tm --map map.html
  python archaeo_gps.py -i . -o out.csv --crs tm_old --exiftool ./exiftool.exe

지원 좌표계 (--crs):
  [한국 현행 GRS80]
  tm           EPSG:5186  TM중부원점  (기본값, 한국 국가 표준)
  tm_west      EPSG:5185  TM서부원점  (서해안, 서부)
  tm_east      EPSG:5187  TM동부원점  (동해안, 영남동부)
  tm_eastsea   EPSG:5188  TM동해원점  (울릉도, 독도)
  utmk         EPSG:5179  UTM-K       (국토지리정보원 통합)
  grs80_geo    EPSG:4737  GRS80 지리좌표

  [한국 구좌표 Bessel 1841 - 2010년 이전 지형도/문화재 도면]
  tm_old       EPSG:5174  구TM중부원점
  tm_old_west  EPSG:5173  구TM서부원점
  tm_old_east  EPSG:5176  구TM동부원점
  tm_mod       EPSG:5181  수정중부원점  (2010년 경과조치)
  tm_mod_west  EPSG:5182  수정서부원점
  tm_mod_east  EPSG:5183  수정동부원점
  bessel_geo   EPSG:4162  Bessel 지리좌표

  [국제 / 글로벌]
  utm52n       EPSG:32652 UTM Zone 52N  (한반도 전역)
  utm51n       EPSG:32651 UTM Zone 51N  (서해 일부)
  web_mercator EPSG:3857  Web Mercator  (Google/Kakao지도)
  wgs84        EPSG:4326  WGS84 지리좌표  (변환 없음)

  또는 EPSG 코드 직접 입력: epsg:5186, epsg:32652, …
        """,
    )
    parser.add_argument("-i", "--input", required=True,
                        help="입력 폴더 또는 이미지 파일 경로")
    parser.add_argument("-o", "--output", required=True,
                        help="출력 CSV 파일 경로 (예: output.csv)")
    parser.add_argument("--crs", default="tm",
                        help="출력 좌표계 (기본: tm = EPSG:5186 TM중부원점)")
    parser.add_argument("--map", default=None,
                        help="Leaflet HTML 지도 출력 파일 경로 (선택)")
    parser.add_argument("--exiftool", default=None,
                        help="exiftool 실행 파일 경로 (미지정 시 자동 탐색)")
    parser.add_argument("--no-transform", action="store_true",
                        help="좌표 변환 없이 WGS84만 출력")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # ── 의존성 경고 ──────────────────────────────────────────────────────────
    if not HAS_PANDAS:
        print("ℹ️  pandas 미설치 → csv 모듈로 대체 출력합니다. (pip install pandas)")
    if not HAS_PYPROJ and not args.no_transform:
        print("⚠️  pyproj 미설치 → 좌표 변환이 비활성화됩니다. (pip install pyproj)")

    # ── exiftool 탐색 ────────────────────────────────────────────────────────
    try:
        exiftool_path = find_exiftool(args.exiftool)
        print(f"🔧 ExifTool: {exiftool_path}")
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # ── 이미지 목록 ──────────────────────────────────────────────────────────
    try:
        images = collect_images(args.input)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if not images:
        print(f"⚠️  지원 이미지 파일이 없습니다: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 이미지 {len(images)}장 발견")

    # ── EXIF 추출 ────────────────────────────────────────────────────────────
    print("📡 EXIF 데이터 추출 중…")
    raw_records = run_exiftool(exiftool_path, images)
    if not raw_records:
        print("❌ EXIF 데이터를 추출하지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    records = [parse_record(r) for r in raw_records]
    gps_ok = sum(1 for r in records if r["Lat_DD"] is not None)
    dir_ok = sum(1 for r in records if r["CamDirection_deg"] is not None)
    print(f"   GPS 있음: {gps_ok}/{len(records)}장  |  방향 있음: {dir_ok}/{len(records)}장")

    # ── 좌표 변환 ────────────────────────────────────────────────────────────
    target_crs = ""
    transformer = None
    if not args.no_transform and HAS_PYPROJ:
        crs_input = args.crs.lower().strip()
        target_crs = CRS_PRESETS.get(crs_input, crs_input)
        transformer = build_transformers(target_crs)
        if transformer:
            print(f"🌐 좌표 변환: WGS84 → {target_crs}")

    # ── CSV 출력 ─────────────────────────────────────────────────────────────
    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(records, args.output, transformer=transformer, target_crs=target_crs)

    # ── HTML 지도 ────────────────────────────────────────────────────────────
    if args.map:
        write_map(records, args.map)

    # ── 요약 출력 ────────────────────────────────────────────────────────────
    print()
    print("─" * 50)
    print(f"{'파일명':<20} {'방향(°)':<10} {'방향':<6} {'위도':>12} {'경도':>13}")
    print("─" * 50)
    for r in records:
        lat_s = f"{r['Lat_DD']:.6f}" if r["Lat_DD"] is not None else "N/A"
        lon_s = f"{r['Lon_DD']:.6f}" if r["Lon_DD"] is not None else "N/A"
        dir_s = f"{r['CamDirection_deg']:.1f}" if r["CamDirection_deg"] is not None else "N/A"
        card  = r.get("CamDirection_cardinal", "")
        print(f"{r['FileName']:<20} {dir_s:<10} {card:<6} {lat_s:>12} {lon_s:>13}")
    print("─" * 50)


if __name__ == "__main__":
    main()
