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
    import io
    if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr is not None and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
    """Locate exiftool executable."""
    candidates = []
    if hint:
        candidates.append(hint)
    # Common locations
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
    """Generate a Leaflet.js HTML map with direction arrows."""

    features = []
    for rec in records:
        lat = rec.get("Lat_DD")
        lon = rec.get("Lon_DD")
        if lat is None or lon is None:
            continue

        yaw = rec.get("CamDirection_deg")
        card = rec.get("CamDirection_cardinal", "")
        fname = rec.get("FileName", "")
        dt = rec.get("DateTime", "")
        alt = rec.get("Alt_m", "")
        make = rec.get("Make", "")
        model = rec.get("Model", "")
        gimbal_pitch = rec.get("GimbalPitch", "")
        flight_yaw = rec.get("FlightYaw", "")
        gimbal_yaw = rec.get("GimbalYaw", "")

        yaw_display = f"{yaw}° ({card})" if yaw is not None else "N/A"

        popup = (
            f"<b>{fname}</b><br>"
            f"📍 {lat:.6f}, {lon:.6f}<br>"
            f"⬆ 고도: {alt} m<br>"
            f"🧭 촬영방향: {yaw_display}<br>"
            f"&nbsp;&nbsp;&nbsp;│ 짐벌 Yaw: {gimbal_yaw}°<br>"
            f"&nbsp;&nbsp;&nbsp;│ 짐벌 Pitch: {gimbal_pitch}°<br>"
            f"&nbsp;&nbsp;&nbsp;└ 기체 Yaw: {flight_yaw}°<br>"
            f"📷 {make} {model}<br>"
            f"🕐 {dt}"
        )

        features.append({
            "lat": lat,
            "lon": lon,
            "yaw": yaw if yaw is not None else 0,
            "has_dir": yaw is not None,
            "popup": popup,
            "fname": fname,
        })

    if not features:
        print("⚠️  지도에 표시할 GPS 데이터가 없습니다.", file=sys.stderr)
        return

    # Center map on mean position
    center_lat = sum(f["lat"] for f in features) / len(features)
    center_lon = sum(f["lon"] for f in features) / len(features)

    features_json = json.dumps(features, ensure_ascii=False, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ExifTool Archaeo — 사진 위치·방향 지도</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Noto Sans KR', 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; }}
    #header {{
      padding: 12px 20px;
      background: linear-gradient(135deg, #16213e, #0f3460);
      border-bottom: 2px solid #e94560;
      display: flex; align-items: center; gap: 12px;
    }}
    #header h1 {{ font-size: 1.1rem; font-weight: 600; letter-spacing: 0.5px; }}
    #header span {{ font-size: 0.85rem; color: #a0aec0; }}
    #map {{ width: 100%; height: calc(100vh - 52px); }}
    .leaflet-popup-content {{ font-size: 0.82rem; line-height: 1.7; min-width: 200px; }}
    .legend {{
      background: rgba(22,33,62,0.92);
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid #e94560;
      font-size: 0.78rem;
      color: #eee;
      line-height: 1.8;
    }}
    .legend-dot {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
  </style>
</head>
<body>
  <div id="header">
    <span style="font-size:1.4rem;">🏛️</span>
    <h1>ExifTool Archaeo</h1>
    <span>고고학 현장 사진 위치·방향 시각화</span>
    <span style="margin-left:auto; color:#e94560; font-weight:600;">{len(features)}장</span>
  </div>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map').setView([{center_lat}, {center_lon}], 15);

    // Base layers
    const osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© OpenStreetMap contributors', maxZoom: 20
    }});
    const satellite = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
      {{ attribution: 'Tiles © Esri', maxZoom: 20 }}
    );
    satellite.addTo(map);
    L.control.layers({{"위성 (Esri)": satellite, "지도 (OSM)": osm}}, {{}}, {{position:'topright'}}).addTo(map);

    // Arrow SVG icon factory
    function makeArrowIcon(yawDeg, hasDir) {{
      const color = hasDir ? '#e94560' : '#718096';
      const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
          <circle cx="18" cy="18" r="10" fill="${{color}}" fill-opacity="0.25" stroke="${{color}}" stroke-width="2"/>
          <circle cx="18" cy="18" r="3" fill="${{color}}"/>
          ${{hasDir ? `<line x1="18" y1="18" x2="18" y2="5"
            stroke="${{color}}" stroke-width="2.5" stroke-linecap="round"
            transform="rotate(${{yawDeg}}, 18, 18)"/>
          <polygon points="18,2 15,9 21,9"
            fill="${{color}}"
            transform="rotate(${{yawDeg}}, 18, 18)"/>` : ''}}
        </svg>`;
      return L.divIcon({{
        html: svg,
        className: '',
        iconSize: [36, 36],
        iconAnchor: [18, 18],
        popupAnchor: [0, -20],
      }});
    }}

    const features = {features_json};
    const markerGroup = L.featureGroup();

    features.forEach(f => {{
      const icon = makeArrowIcon(f.yaw, f.has_dir);
      const marker = L.marker([f.lat, f.lon], {{icon}})
        .bindPopup(f.popup, {{maxWidth: 280}});
      markerGroup.addLayer(marker);
    }});

    markerGroup.addTo(map);
    map.fitBounds(markerGroup.getBounds().pad(0.15));

    // Legend
    const legend = L.control({{position: 'bottomleft'}});
    legend.onAdd = () => {{
      const d = L.DomUtil.create('div', 'legend');
      d.innerHTML = `
        <b>범례</b><br>
        <span class="legend-dot" style="background:#e94560;"></span> 방향 정보 있음<br>
        <span class="legend-dot" style="background:#718096;"></span> 방향 정보 없음<br>
        <span style="font-size:0.72rem; color:#a0aec0;">화살표 = 카메라(짐벌) 방향</span>
      `;
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
