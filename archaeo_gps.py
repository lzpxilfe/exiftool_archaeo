#!/usr/bin/env python3
"""
archaeo_gps.py — 고고학 현장 사진 GPS·방향 추출 도구
Archaeological field photo GPS & orientation extractor

Usage:
    python archaeo_gps.py -i ./photos -o output.csv
    python archaeo_gps.py -i ./photos -o output.csv --crs epsg:5186 --map map.html
    python archaeo_gps.py -i ./photos -o output.csv --exiftool ./exiftool.exe
    python archaeo_gps.py -i ./photos -o output.csv --recursive --geojson out.geojson

환경 변수:
    EXIFTOOL_PATH  exiftool 실행 파일(또는 그 폴더)의 경로를 직접 지정
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

__version__ = "1.2.0"

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
SUPPORTED_EXT = {
    # 일반 이미지
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".tif", ".tiff",
    # RAW (제조사별)
    ".dng",           # Adobe / DJI
    ".arw", ".sr2",   # Sony
    ".cr2", ".cr3",   # Canon
    ".nef", ".nrw",   # Nikon
    ".orf",           # Olympus / OM System
    ".rw2",           # Panasonic
    ".raf",           # Fujifilm
    ".pef",           # Pentax
    ".3fr",           # Hasselblad
}

# ── 동작 파라미터 (하드코딩 대신 한 곳에서 관리) ────────────────────────────
EXIFTOOL_TIMEOUT_BASE = 30      # ExifTool 배치 실행 기본 타임아웃(초)
EXIFTOOL_TIMEOUT_PER_IMAGE = 0.15  # 이미지 1장당 추가 허용 시간(초)
EXIFTOOL_TIMEOUT_MAX = 900      # 타임아웃 상한(초)
EXIFTOOL_PROBE_TIMEOUT = 5      # exiftool 자가 검증 실행 타임아웃(초)

THUMB_SIZE_DEFAULT = 640        # 지도 팝업 썸네일 최대 변 길이(px)
THUMB_QUALITY_DEFAULT = 72      # 썸네일 JPEG 품질(1-95)
THUMB_WORKERS_MAX = 8           # 썸네일 병렬 추출 최대 스레드 수

NADIR_PITCH_THRESHOLD = -85.0   # 이 값 이하의 짐벌 Pitch는 수직(Nadir) 촬영으로 간주

ENV_EXIFTOOL_PATH = "EXIFTOOL_PATH"  # exiftool 경로 지정용 환경 변수명

CAMERA_TYPE_DRONE = "drone"
CAMERA_TYPE_DSLR = "dslr"

DRONE_MAKES = ("dji", "autel", "parrot", "skydio", "yuneec")

# 입력 폴더별로 점 색상을 구분하기 위한 고대비 팔레트 (위성 지도 위에서 잘 보이는 색상)
FOLDER_PALETTE_DRONE = [
    "#e94560",  # 코랄 레드 (드론 기본)
    "#ff9f1c",  # 앰버 오렌지
    "#f72585",  # 마젠타 핑크
    "#ffbe0b",  # 골드 옐로우
    "#fb5607",  # 블레이즈 오렌지
]

FOLDER_PALETTE_DSLR = [
    "#38bdf8",  # 스카이 블루 (DSLR 기본)
    "#34d399",  # 에메랄드 그린
    "#a78bfa",  # 바이올렛 퍼플
    "#2dd4bf",  # 틸 민트
    "#f472b6",  # 로즈 핑크
]

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
    Convert ExifTool DMS or decimal string to Decimal Degrees.
    Handles:
      '36 deg 22' 14.45" N'
      '36.371791666'          (already decimal)
      '36.371791666 N'        (decimal with hemisphere)
      '-33 deg 52' 07.68"'    (signed DMS)
    Returns None on failure.
    """
    if dms_str is None:
        return None
    s = str(dms_str).strip()
    if not s:
        return None

    # 1) Plain decimal or decimal with optional ° and trailing hemisphere (e.g. "36.3717° N")
    dec_m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?", s, re.IGNORECASE)
    if dec_m:
        val = float(dec_m.group(1))
        hemi = dec_m.group(2)
        if hemi and hemi.upper() in ("S", "W"):
            val = -abs(val)
        return val

    # 2) DMS pattern: 36 deg 22' 14.45" N
    pattern = r"""
        ([+-]?\d+)\s*deg\s+ # degrees (optional sign)
        (\d+)['′]\s*        # minutes
        ([\d.]+)[\"″]\s*    # seconds
        ([NSEW])?           # optional hemisphere
    """
    m = re.search(pattern, s, re.VERBOSE | re.IGNORECASE)
    if not m:
        return None

    deg_s, mn, sec, hemi = m.groups()
    sign = -1.0 if deg_s.startswith("-") else 1.0
    dd = abs(float(deg_s)) + float(mn) / 60.0 + float(sec) / 3600.0
    dd *= sign

    if hemi and hemi.upper() in ("S", "W"):
        dd = -abs(dd)
    return dd


def altitude_to_float(alt_str, alt_ref=None) -> float | None:
    """ExifTool 고도 문자열에서 숫자를 추출한다.

    '525.2 m Above Sea Level' -> 525.2
    '12.5 m Below Sea Level'  -> -12.5   (해수면 아래는 음수로 변환)

    alt_ref 는 GPSAltitudeRef (0=Above, 1=Below) 값으로, 문자열에 방향
    표기가 없는 경우(-n 옵션 등)의 보조 판단에 쓰인다.
    """
    if alt_str is None:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)", str(alt_str))
    if not m:
        return None
    val = float(m.group(1))

    below = "below sea level" in str(alt_str).lower()
    if not below and alt_ref is not None:
        ref_s = str(alt_ref).strip().lower()
        below = ref_s.startswith("1") or "below" in ref_s

    if below:
        val = -abs(val)
    return val


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


EXIFTOOL_NAMES = ("exiftool.exe", "exiftool")


def _is_valid_exiftool(path_str: str) -> bool:
    """단독형/분리형(exiftool_files) exiftool 실행 파일인지 검증한다.

    분리형 배포판은 exiftool.exe 옆에 perl5xx.dll 이 든 exiftool_files 폴더가
    있어야 동작한다. 폴더가 없거나 사용자 지정 파일명인 경우 실제로 -ver 를
    실행해 유효한 ExifTool 바이너리인지 확인한다.
    """
    p = Path(path_str)
    if not p.is_file():
        return False

    has_files_dir = (p.parent / "exiftool_files").is_dir()

    # 표준 이름(exiftool/exiftool.exe)이면서 옆에 exiftool_files 폴더가 있는 경우에만
    # 빠른 통과를 허용하고, 그 외(단독형 바이너리 또는 임의 파일명)는 모두 -ver 실행으로 검증한다.
    if not (p.name.lower() in EXIFTOOL_NAMES and has_files_dir):
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            res = subprocess.run(
                [str(p.resolve()), "-ver"],
                capture_output=True, text=True,
                timeout=EXIFTOOL_PROBE_TIMEOUT,
                startupinfo=startupinfo,
            )
        except Exception:
            return False
        # -ver 은 "13.10" 같은 버전 문자열만 출력한다. 실패 시 유효하지 않은 실행 파일로 간주.
        ver_out = res.stdout.strip()
        if res.returncode != 0 or not re.match(r"^\d+\.\d+", ver_out):
            return False
    return True


def _exiftool_candidate_dirs() -> list[Path]:
    """exiftool 을 탐색할 안전한 로컬 디렉토리 후보를 우선순위 순으로 생성한다.

    보안을 위해 Downloads / Desktop 등 신뢰할 수 없는 다운로드 폴더는 자동 탐색에서 제외한다.
    """
    dirs: list[Path] = []

    def add(base: Path | None) -> None:
        """base 및 그 하위의 관례적인 배포 폴더들을 후보에 추가."""
        if base is None:
            return
        dirs.append(base)
        for sub in ("dist", "exiftool", "ExifTool"):
            dirs.append(base / sub)

    # 1. 실행 파일(exe)과 같은 디렉토리 및 그 상위 (PyInstaller frozen)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        add(exe_dir)
        add(exe_dir.parent)

    # 2. 스크립트 디렉토리 및 그 상위
    script_dir = Path(__file__).resolve().parent
    add(script_dir)
    add(script_dir.parent)

    # 3. 현재 작업 디렉토리
    try:
        add(Path.cwd())
    except Exception:
        pass

    return dirs


def find_exiftool(hint: str | None = None) -> str:
    """exiftool 실행 파일 경로를 찾는다.

    탐색 우선순위:
      1) hint 인자 (파일 경로 또는 폴더 경로)
      2) 환경 변수 EXIFTOOL_PATH (파일 경로 또는 폴더 경로)
      3) 실행 파일/스크립트/작업 디렉토리 및 그 관례적 하위 폴더
      4) 시스템 PATH
    """
    candidate_dirs: list[Path] = []

    # hint 와 환경 변수를 최우선으로 처리 (파일이면 즉시 반환, 폴더면 후보 맨 앞에)
    for raw in (hint, os.environ.get(ENV_EXIFTOOL_PATH)):
        if not raw:
            continue
        raw = str(raw).strip().strip('"')
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if _is_valid_exiftool(str(candidate)):
            return str(candidate.resolve())
        if candidate.is_dir():
            candidate_dirs.append(candidate)

    candidate_dirs.extend(_exiftool_candidate_dirs())

    # 중복 제거 및 존재하는 디렉토리만 남기기 (순서 유지)
    unique_dirs: list[Path] = []
    seen: set[Path] = set()
    for d in candidate_dirs:
        try:
            resolved = d.resolve()
        except Exception:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            unique_dirs.append(resolved)

    for d in unique_dirs:
        for name in EXIFTOOL_NAMES:
            target = d / name
            if _is_valid_exiftool(str(target)):
                return str(target.resolve())

    # 마지막 보루: 시스템 PATH에서 탐색
    for name in EXIFTOOL_NAMES:
        w = shutil.which(name)
        if w and _is_valid_exiftool(w):
            return str(Path(w).resolve())

    raise FileNotFoundError(
        "exiftool을 찾을 수 없습니다.\n"
        "다음 중 한 가지 방법으로 지정해 주십시오:\n"
        "  1) 실행 파일(ExifToolArchaeo.exe)과 같은 폴더에 exiftool.exe 를 복사\n"
        f"  2) 환경 변수 {ENV_EXIFTOOL_PATH} 에 exiftool 경로를 설정\n"
        "  3) --exiftool 옵션으로 경로를 직접 전달"
    )


# Windows 8.3 단축 경로 변환용 ctypes 시그니처 (모듈 로드 시 1회만 초기화하여 멀티스레드 안전성 확보)
_GetShortPathNameW = None
if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes
        _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        _GetShortPathNameW.restype = wintypes.DWORD
    except Exception:
        _GetShortPathNameW = None


def get_short_path_name(long_name: str) -> str:
    """Convert long Windows paths with spaces/CJK chars to 8.3 short paths."""
    if os.name != "nt" or _GetShortPathNameW is None:
        return long_name
    import ctypes
    try:
        buf_size = 512
        buffer = ctypes.create_unicode_buffer(buf_size)
        result = _GetShortPathNameW(long_name, buffer, buf_size)
        if result == 0:
            return long_name
        if result > buf_size:
            buffer = ctypes.create_unicode_buffer(result)
            _GetShortPathNameW(long_name, buffer, result)
        return buffer.value
    except Exception:
        return long_name


def run_exiftool(exiftool_path: str, image_paths: list[str]) -> tuple[list[dict], list[str]]:
    """Run exiftool in batch mode using an argument file (-@) with UTF-8 encoding.
    This is the official ExifTool recommendation to bypass Windows Unicode / CJK filename issues.
    Returns (results, errors).
    """
    import tempfile
    
    tag_args = [f"-{t}" for t in EXIF_TAGS]
    exiftool_path = str(Path(exiftool_path).resolve().absolute())
    
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    results = []
    errors = []
    temp_file_path = None

    # 1. 임시 파일 작성 (UTF-8 인코딩)
    try:
        # 윈도우 락 우회를 위해 delete=False 적용 후 블록 내에서 수동 기록
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.args', delete=False) as f:
            # tag 인자들 먼저 기재
            for tag in tag_args:
                f.write(tag + "\n")
            # 분석할 이미지들의 절대 경로 기재
            for path in image_paths:
                abs_path = str(Path(path).resolve().absolute())
                f.write(abs_path + "\n")
            temp_file_path = f.name
            
        # block을 나왔으므로 f.close() 되어 윈도우 파일 잠금 해제됨!
        
        # 2. ExifTool 일괄 실행 (Batch mode)
        # -c "%d deg %d' %.6f\"" 로 초 소수점 6자리(~0.03mm) 정밀도를 유지한다.
        cmd = [
            exiftool_path,
            "-json",
            "-c", "%d deg %d' %.6f\"",
            "-charset", "filename=UTF8",
            "-@", temp_file_path,
        ]

        # 사진 수에 비례해 타임아웃을 늘린다. 수백~수천 장 배치에서 고정 30초로는
        # 정상 동작 중에도 중간에 끊기기 때문이다.
        timeout_s = min(
            EXIFTOOL_TIMEOUT_MAX,
            EXIFTOOL_TIMEOUT_BASE + EXIFTOOL_TIMEOUT_PER_IMAGE * len(image_paths),
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            startupinfo=startupinfo,
        )
        
        # 3. 결과 분석
        if result.returncode in (0, 1) and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                if isinstance(data, list):
                    # 반환된 리스트의 SourceFile 경로들을 시스템 절대경로와 일치시킴
                    for rec in data:
                        if "SourceFile" in rec:
                            sf_normalized = str(Path(rec["SourceFile"]).resolve().absolute())
                            rec["SourceFile"] = sf_normalized
                    results = data
                else:
                    errors.append("ExifTool 반환 데이터 형식 오류 (JSON 리스트 아님)")
            except json.JSONDecodeError as je:
                errors.append(f"JSON 파싱 실패: {je}\n출력: {result.stdout[:300]}")
            # 일부 파일 읽기 실패(returncode == 1) 시 stderr 경고를 누락하지 않고 전달
            if result.returncode == 1 and result.stderr and result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    if line.strip():
                        errors.append(f"ExifTool 부분 경고: {line.strip()}")
        else:
            err_msg = result.stderr.strip() if result.stderr else f"ExitCode={result.returncode}"
            errors.append(f"ExifTool 실행 실패: {err_msg}")
            
    except subprocess.TimeoutExpired:
        errors.append(
            f"ExifTool 실행 시간이 초과되었습니다 (사진 {len(image_paths)}장). "
            "사진 수를 줄이거나 폴더를 나누어 다시 시도해 주십시오."
        )
    except Exception as e:
        errors.append(f"배치 실행 중 오류 발생: {e}")
    finally:
        # 예외 발생 여부와 무관하게 임시 args 파일을 반드시 정리한다.
        if temp_file_path:
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass

    return results, errors


def collect_images(input_path: str, recursive: bool = False) -> list[str]:
    """파일 또는 폴더에서 지원 이미지의 절대 경로 목록을 수집한다.

    확장자 비교는 대소문자를 구분하지 않으므로 .JPG/.Jpg/.jpg 를 모두 인식하며,
    윈도우처럼 대소문자를 구분하지 않는 파일 시스템에서도 중복되지 않는다.
    recursive=True 이면 하위 폴더까지 모두 훑는다.
    """
    p = Path(input_path).expanduser().resolve()
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        it = p.rglob("*") if recursive else p.glob("*")
        files = {
            f.resolve()
            for f in it
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
        }
        return sorted(str(f) for f in files)
    raise FileNotFoundError(f"입력 경로를 찾을 수 없습니다: {input_path}")


def _folder_label_for(img_path: Path, root_path: Path) -> str:
    """입력 루트 경로 대비 사진이 속한 폴더 라벨을 생성한다.

    하위 폴더 재귀 탐색 시 '상위폴더/하위폴더' 형태로 구분해 주므로,
    폴더별로 점 색상과 그룹을 다르게 표현할 수 있다.
    """
    if root_path.is_file():
        return root_path.parent.name or "기본"
    try:
        rel_parent = img_path.parent.relative_to(root_path)
        if str(rel_parent) in ("", "."):
            return root_path.name or str(root_path)
        return f"{root_path.name}/{rel_parent.as_posix()}"
    except ValueError:
        return img_path.parent.name or "기본"


def collect_grouped_images(
    drone_inputs: list[str] | None = None,
    dslr_inputs: list[str] | None = None,
    generic_inputs: list[str] | None = None,
    recursive: bool = False,
) -> tuple[list[str], dict[str, dict[str, str | None]]]:
    """드론/DSLR/일반 입력 경로들에서 이미지를 수집하고 출처 메타정보를 매핑한다.

    Returns:
        (image_paths, meta_by_path)
        meta_by_path[abs_path] = {"camera_type": "drone"|"dslr"|None, "folder": 폴더명}
    """
    ordered_paths: list[str] = []
    meta_by_path: dict[str, dict[str, str | None]] = {}

    groups: list[tuple[str | None, list[str]]] = [
        (CAMERA_TYPE_DRONE, [p for p in (drone_inputs or []) if p and str(p).strip()]),
        (CAMERA_TYPE_DSLR,  [p for p in (dslr_inputs or []) if p and str(p).strip()]),
        (None,              [p for p in (generic_inputs or []) if p and str(p).strip()]),
    ]

    for cam_type, path_list in groups:
        for raw_in in path_list:
            root_p = Path(raw_in).expanduser().resolve()
            found = collect_images(str(root_p), recursive=recursive)
            for f_str in found:
                f_resolved = str(Path(f_str).resolve().absolute())
                folder_lbl = _folder_label_for(Path(f_resolved), root_p)
                if f_resolved not in meta_by_path:
                    ordered_paths.append(f_resolved)
                meta_by_path[f_resolved] = {
                    "camera_type": cam_type,
                    "folder": folder_lbl,
                }

    return ordered_paths, meta_by_path


def infer_camera_type(raw: dict) -> str:
    """EXIF 태그로부터 드론(drone)인지 일반 카메라(dslr)인지 판별한다."""
    for dji_tag in ("GimbalYawDegree", "GimbalPitchDegree", "FlightYawDegree", "FlightPitchDegree"):
        if raw.get(dji_tag) is not None:
            return CAMERA_TYPE_DRONE
    make = str(raw.get("Make", "")).strip().lower()
    if any(k in make for k in DRONE_MAKES):
        return CAMERA_TYPE_DRONE
    return CAMERA_TYPE_DSLR


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

def parse_record(
    raw: dict,
    camera_type: str | None = None,
    folder_label: str | None = None,
) -> dict:
    """Parse one ExifTool JSON record into a clean dict."""
    r = {}

    # File info
    r["FileName"] = raw.get("FileName", "")
    r["SourceFile"] = raw.get("SourceFile", "")
    r["DateTime"] = raw.get("DateTimeOriginal") or raw.get("CreateDate", "")
    r["Make"] = raw.get("Make", "")
    r["Model"] = raw.get("Model", "")

    resolved_type = (
        camera_type
        or raw.get("CameraType")
        or raw.get("_CameraType")
        or infer_camera_type(raw)
    )
    resolved_type = str(resolved_type).strip().lower()
    if resolved_type not in (CAMERA_TYPE_DRONE, CAMERA_TYPE_DSLR):
        resolved_type = infer_camera_type(raw)
    r["CameraType"] = resolved_type

    resolved_folder = (
        folder_label
        if folder_label is not None
        else (raw.get("Folder") or raw.get("_Folder") or "")
    )
    if not resolved_folder and r["SourceFile"]:
        try:
            resolved_folder = Path(r["SourceFile"]).parent.name
        except Exception:
            resolved_folder = ""
    r["Folder"] = str(resolved_folder) if resolved_folder else "기본"

    # GPS coordinates
    lat_raw = raw.get("GPSLatitude")
    lon_raw = raw.get("GPSLongitude")

    lat = dms_to_dd(lat_raw)
    lon = dms_to_dd(lon_raw)

    # Apply hemisphere refs if not embedded in string ("S"/"South", "W"/"West" 모두 처리)
    if lat is not None and lat > 0:
        lat_ref = str(raw.get("GPSLatitudeRef", "N")).strip().upper()
        if lat_ref.startswith("S"):
            lat = -lat

    if lon is not None and lon > 0:
        lon_ref = str(raw.get("GPSLongitudeRef", "E")).strip().upper()
        if lon_ref.startswith("W"):
            lon = -lon

    r["Lat_DD"] = round(lat, 8) if lat is not None else None
    r["Lon_DD"] = round(lon, 8) if lon is not None else None
    r["Alt_m"] = altitude_to_float(raw.get("GPSAltitude"), raw.get("GPSAltitudeRef"))

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


def _extract_raw_preview_bytes(source_file: str, exiftool_path: str | None = None) -> bytes | None:
    """Pillow 가 직접 열지 못하는 RAW/HEIC 파일에서 ExifTool 로 내장 프리뷰 이미지를 추출한다."""
    et = exiftool_path
    if not et:
        try:
            et = find_exiftool(None)
        except FileNotFoundError:
            return None

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        try:
            res = subprocess.run(
                [et, "-b", tag, str(Path(source_file).resolve())],
                capture_output=True,
                timeout=10,
                startupinfo=startupinfo,
            )
            if res.returncode == 0 and res.stdout and len(res.stdout) > 128:
                return res.stdout
        except Exception:
            continue
    return None


def _encode_pil_to_b64(img, max_size: int, quality: int) -> str:
    """PIL Image 객체를 방향 보정·리사이즈 후 Base64 JPEG 문자열로 인코딩한다."""
    try:
        img.draft("RGB", (max_size * 2, max_size * 2))
    except Exception:
        pass
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    resample = getattr(_PILImage, "Resampling", _PILImage).LANCZOS
    img.thumbnail((max_size, max_size), resample)
    buf = _io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return _base64.b64encode(buf.getvalue()).decode()


def extract_thumbnail(source_file: str,
                      max_size: int = THUMB_SIZE_DEFAULT,
                      quality: int = THUMB_QUALITY_DEFAULT,
                      exiftool_path: str | None = None) -> str | None:
    """
    Open the original photo using Windows short path name to prevent encoding issues,
    resize to thumbnail, and return base64 JPEG string.
    Falls back to ExifTool embedded preview extraction for RAW/HEIC formats.
    """
    if not HAS_PILLOW:
        return None
    short_file_path = get_short_path_name(source_file)
    try:
        with _PILImage.open(short_file_path) as img:
            return _encode_pil_to_b64(img, max_size, quality)
    except Exception as pil_err:
        # RAW / HEIC 등 Pillow 가 직접 열지 못하는 포맷은 ExifTool 내장 프리뷰로 폴백
        raw_bytes = _extract_raw_preview_bytes(source_file, exiftool_path=exiftool_path)
        if raw_bytes:
            try:
                with _PILImage.open(_io.BytesIO(raw_bytes)) as img:
                    return _encode_pil_to_b64(img, max_size, quality)
            except Exception:
                pass
        print(f"⚠️  썸네일 추출 에러 ({source_file}): {pil_err}", file=sys.stderr)
        return None


def extract_thumbnails(source_files: list[str],
                       max_size: int = THUMB_SIZE_DEFAULT,
                       quality: int = THUMB_QUALITY_DEFAULT,
                       progress=None,
                       exiftool_path: str | None = None) -> dict[str, str]:
    """여러 사진의 썸네일을 병렬로 추출한다.

    수백 장 규모에서 순차 처리는 지도 생성의 가장 큰 병목이므로 스레드 풀을
    사용한다 (Pillow 의 디코딩/리사이즈는 GIL 을 놓기 때문에 실제로 빨라진다).

    progress: progress(done, total) 형태의 콜백 (선택). GUI 진행률 표시용.
    Returns: {source_file: base64_jpeg} — 실패한 파일은 결과에서 빠진다.
    """
    thumbs: dict[str, str] = {}
    targets = [f for f in source_files if f]
    total = len(targets)
    if total == 0:
        return thumbs

    if not HAS_PILLOW:
        print("⚠️  Pillow 라이브러리가 없어 썸네일을 생성하지 않습니다. (pip install Pillow)",
              file=sys.stderr)
        if progress:
            progress(total, total)
        return thumbs

    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max(1, min(THUMB_WORKERS_MAX, (os.cpu_count() or 2), total))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(extract_thumbnail, f, max_size, quality, exiftool_path): f
            for f in targets
        }
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                b64 = fut.result()
            except Exception:
                b64 = None
            if b64:
                thumbs[src] = b64
            done += 1
            if progress:
                progress(done, total)
    return thumbs


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "FileName", "CameraType", "Folder", "DateTime", "Make", "Model",
    "Lat_DD", "Lon_DD", "Alt_m",
    "CamDirection_deg", "CamDirection_cardinal",
    "GimbalYaw", "GimbalPitch", "GimbalRoll",
    "FlightYaw", "FlightPitch", "FlightRoll",
    "GPSImgDirection", "GPSImgDirectionRef",
    "Proj_X", "Proj_Y", "Proj_CRS",
    "SourceFile",
]


def write_csv(records: list[dict], output_path: str, transformer=None, target_crs: str = ""):
    """Write records to UTF-8 BOM CSV using standard library csv module.

    Note on projected coordinates (GIS vs Korean Surveying convention):
      - Proj_X: Easting (동향 좌표, m)
      - Proj_Y: Northing (북향 좌표, m)
    """
    import csv

    rows = []
    for rec in records:
        row = {col: rec.get(col, "") for col in CSV_COLUMNS}

        # Add projected coordinates (always_xy=True -> x=Easting, y=Northing)
        x, y = transform_coords(transformer, rec.get("Lat_DD"), rec.get("Lon_DD"))
        row["Proj_X"] = round(x, 3) if x is not None else ""
        row["Proj_Y"] = round(y, 3) if y is not None else ""
        row["Proj_CRS"] = target_crs if (x is not None) else ""
        rows.append(row)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"✅ CSV 저장: {output_path}  ({len(rows)}개 레코드)")
    if target_crs:
        print("   ℹ️  투영 좌표 축 안내: Proj_X = 동향(Easting, E), Proj_Y = 북향(Northing, N) [GIS 표준]")


def write_geojson(records: list[dict], output_path: str) -> bool:
    """GPS 가 있는 레코드를 GeoJSON(WGS84) 으로 저장한다.

    QGIS·ArcGIS 에서 CSV 의 위경도 열을 수동 지정할 필요 없이 바로 열 수 있고,
    촬영 방위각이 속성으로 들어가므로 회전 심볼(화살표) 표현에 그대로 쓸 수 있다.
    """
    feats = []
    for rec in records:
        lat, lon = rec.get("Lat_DD"), rec.get("Lon_DD")
        if lat is None or lon is None:
            continue
        props = {k: v for k, v in rec.items() if k not in ("Lat_DD", "Lon_DD")}
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    if not feats:
        print("⚠️  GeoJSON 으로 내보낼 GPS 데이터가 없습니다.", file=sys.stderr)
        return False

    out = Path(output_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    # GeoJSON(RFC 7946) 은 항상 WGS84 경위도이므로 crs 멤버를 붙이지 않는다.
    payload = {"type": "FeatureCollection", "features": feats}
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"🌍 GeoJSON 저장: {output_path}  ({len(feats)}개 지점)")
    return True


# ---------------------------------------------------------------------------
# Leaflet HTML map generation
# ---------------------------------------------------------------------------

def write_map(records: list[dict], output_path: str,
              thumb_size: int = THUMB_SIZE_DEFAULT,
              thumb_quality: int = THUMB_QUALITY_DEFAULT,
              progress=None,
              exiftool_path: str | None = None) -> bool:
    """
    Generate a Leaflet.js HTML map with:
      - Per-folder color & symbol differentiation
      - Drone vs DSLR marker differentiation (only Drone markers show directional arrows;
        DSLR markers render as compact dots so dense excavation shots don't clutter)
      - Compact, non-occluding marker design + real-time size/opacity sliders & 'H' hide key
      - Side panel layout & Zoomable/Pannable photo Lightbox
      - Independent tile layer instances (fixing blank map on base layer switch)

    progress: progress(done, total) 콜백 (썸네일 추출 진행률, 선택)
    Returns: 지도를 실제로 저장했으면 True, 표시할 GPS 데이터가 없으면 False
    """
    plotted = [r for r in records
               if r.get("Lat_DD") is not None and r.get("Lon_DD") is not None]
    if not plotted:
        print("⚠️  지도에 표시할 GPS 데이터가 없습니다.", file=sys.stderr)
        return False

    # 썸네일은 지도 생성의 가장 큰 병목이므로 한 번에 병렬로 추출한다.
    # thumb_size 가 0 이하이면 썸네일을 아예 생성하지 않는다 (용량·속도 우선).
    thumbs: dict[str, str] = {}
    if thumb_size > 0:
        thumbs = extract_thumbnails(
            [r.get("SourceFile", "") for r in plotted],
            max_size=thumb_size, quality=thumb_quality,
            progress=progress, exiftool_path=exiftool_path,
        )

    # 폴더별 그룹 메타데이터 (색상·모양 배정)
    # 드론 폴더와 DSLR 폴더에 서로 다른 색상 팔레트와 도형을 배정한다.
    folder_groups: dict[str, dict] = {}
    drone_folder_idx = 0
    dslr_folder_idx = 0
    dslr_shapes = ["circle", "diamond", "square", "triangle"]

    features = []
    for rec in plotted:
        lat = rec.get("Lat_DD")
        lon = rec.get("Lon_DD")

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

        cam_type = str(rec.get("CameraType") or infer_camera_type(rec)).strip().lower()
        if cam_type not in (CAMERA_TYPE_DRONE, CAMERA_TYPE_DSLR):
            cam_type = infer_camera_type(rec)
        is_drone = (cam_type == CAMERA_TYPE_DRONE)

        raw_folder = str(rec.get("Folder") or "").strip()
        if not raw_folder and source_file:
            try:
                raw_folder = Path(source_file).parent.name
            except Exception:
                raw_folder = ""
        if not raw_folder:
            raw_folder = "드론" if is_drone else "DSLR"

        group_key = f"{cam_type}:{raw_folder}"
        if group_key not in folder_groups:
            if is_drone:
                color = FOLDER_PALETTE_DRONE[drone_folder_idx % len(FOLDER_PALETTE_DRONE)]
                shape = "arrow"
                drone_folder_idx += 1
            else:
                color = FOLDER_PALETTE_DSLR[dslr_folder_idx % len(FOLDER_PALETTE_DSLR)]
                shape = dslr_shapes[dslr_folder_idx % len(dslr_shapes)]
                dslr_folder_idx += 1
            folder_groups[group_key] = {
                "key": group_key,
                "cam_type": cam_type,
                "folder": html.escape(raw_folder),
                "color": color,
                "shape": shape,
                "count": 0,
            }
        folder_groups[group_key]["count"] += 1
        grp = folder_groups[group_key]

        yaw_display = f"{yaw}° ({card})" if yaw is not None else "N/A"

        # Nadir check (straight down) — 드론 촬영에만 적용
        is_nadir = False
        if is_drone and gimbal_pitch is not None:
            try:
                if float(gimbal_pitch) <= NADIR_PITCH_THRESHOLD:
                    is_nadir = True
            except (ValueError, TypeError):
                pass

        # 드론만 점에 화살표로 방향을 표시하고, DSLR은 점으로만 표시한다.
        has_dir = bool(is_drone and (yaw is not None))

        thumb_b64 = thumbs.get(source_file)

        alt_val = f"{alt} m" if alt not in (None, "") else "N/A"
        gp_val = f"{gimbal_pitch}°" if gimbal_pitch is not None else "N/A"
        gr_val = f"{gimbal_roll}°" if gimbal_roll is not None else "N/A"
        gy_val = f"{gimbal_yaw}°" if gimbal_yaw is not None else "N/A"
        fy_val = f"{flight_yaw}°" if flight_yaw is not None else "N/A"

        # 파일명·폴더명은 innerHTML 에 들어가므로 반드시 이스케이프한다.
        fname_safe = html.escape(str(fname))
        folder_safe = grp["folder"]
        type_badge = "드론" if is_drone else "DSLR"
        popup_html = (
            "<div style=\"font-family:'Segoe UI',sans-serif;font-size:0.78rem;line-height:1.4;\">"
            f"<span style=\"color:{grp['color']};font-weight:700;\">[{type_badge} · {folder_safe}]</span><br>"
            f"<b>{fname_safe}</b></div>"
        )

        features.append({
            "lat": lat,
            "lon": lon,
            "yaw": yaw if yaw is not None else 0,
            "has_dir": has_dir,
            "is_nadir": is_nadir,
            "cam_type": cam_type,
            "group_key": group_key,
            "folder": folder_safe,
            "color": grp["color"],
            "shape": grp["shape"],
            "popup": popup_html,
            "fname": fname_safe,
            "dt": html.escape(str(dt)) if dt else "N/A",
            "alt": alt_val,
            "make": html.escape(str(make)) if make else "N/A",
            "model": html.escape(str(model)) if model else "N/A",
            "gimbal_yaw": gy_val,
            "gimbal_pitch": gp_val,
            "gimbal_roll": gr_val,
            "flight_yaw": fy_val,
            "yaw_display": yaw_display,
            "thumb_b64": thumb_b64 if thumb_b64 else "",
        })

    center_lat = sum(f["lat"] for f in features) / len(features)
    center_lon = sum(f["lon"] for f in features) / len(features)
    # base64 썸네일이 포함되므로 indent 없이 최소 크기로 직렬화한다
    features_json = json.dumps(features, ensure_ascii=False, separators=(",", ":"))
    groups_json = json.dumps(list(folder_groups.values()), ensure_ascii=False, separators=(",", ":"))

    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ExifTool Archaeo — 고고학 현장 GPS 분석 맵</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    :root {{
      --marker-scale: 1;
      --marker-opacity: 0.88;
    }}
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
    
    /* ── 밀집 지역 가림 방지를 위한 컴팩트 마커 스타일 ── */
    .archaeo-marker-wrap {{
      width: 28px;
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: var(--marker-opacity);
      transform: scale(var(--marker-scale));
      transform-origin: center center;
      transition: transform 0.12s ease, opacity 0.12s ease;
    }}
    .archaeo-marker-wrap:hover {{
      opacity: 1 !important;
      transform: scale(calc(var(--marker-scale) * 1.3));
      z-index: 900 !important;
    }}
    .archaeo-marker-wrap.selected-marker {{
      opacity: 1 !important;
      transform: scale(calc(var(--marker-scale) * 1.35));
      filter: drop-shadow(0 0 5px rgba(255, 255, 255, 0.95));
    }}
    body.markers-hidden .archaeo-marker-wrap {{
      opacity: 0 !important;
      pointer-events: none !important;
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
    
    .nav-bar {{
      display: flex; align-items: center; justify-content: space-between;
      gap: 8px; margin-bottom: 12px;
    }}
    .nav-btn {{
      background: #0f3460; color: #e2e8f0; border: 1px solid #2d3748;
      border-radius: 6px; padding: 5px 10px; font-size: 0.78rem;
      cursor: pointer; transition: background 0.15s;
    }}
    .nav-btn:hover:not(:disabled) {{ background: #1a4a90; }}
    .nav-btn:disabled {{ opacity: 0.35; cursor: default; }}
    .nav-count {{ font-size: 0.78rem; color: #94a3b8; font-family: monospace; }}
    .copy-btn {{
      background: transparent; color: #94a3b8; border: 1px solid #2d3748;
      border-radius: 4px; padding: 1px 6px; font-size: 0.68rem;
      cursor: pointer; margin-left: 6px;
    }}
    .copy-btn:hover {{ color: #e94560; border-color: #e94560; }}

    /* ── 좌측 하단 컨트롤 & 폴더별 범례 패널 ── */
    .legend {{
      background: rgba(22, 33, 62, 0.94);
      padding: 11px 14px;
      border-radius: 8px;
      border: 1px solid #e94560;
      font-size: 0.75rem;
      color: #eee;
      line-height: 1.65;
      pointer-events: auto;
      max-width: 310px;
      box-shadow: 0 4px 14px rgba(0,0,0,0.45);
    }}
    .legend-title {{
      font-weight: 700;
      color: #fff;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .legend-group-list {{
      max-height: 140px;
      overflow-y: auto;
      margin-bottom: 8px;
      padding-right: 4px;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      padding: 2px 0;
      user-select: none;
    }}
    .legend-item input[type="checkbox"] {{
      accent-color: #e94560;
      cursor: pointer;
    }}
    .legend-swatch {{
      display: inline-flex;
      width: 14px;
      height: 14px;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}
    .ctrl-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 4px;
      font-size: 0.72rem;
      color: #cbd5e1;
    }}
    .ctrl-row input[type="range"] {{
      width: 115px;
      accent-color: #e94560;
      cursor: pointer;
    }}
    .hide-btn {{
      margin-top: 7px;
      width: 100%;
      background: #0f3460;
      color: #e2e8f0;
      border: 1px solid #2d3748;
      border-radius: 5px;
      padding: 4px 8px;
      font-size: 0.72rem;
      cursor: pointer;
      transition: background 0.15s;
    }}
    .hide-btn:hover {{ background: #1a4a90; }}
    .hide-btn.active {{ background: #e94560; color: #fff; border-color: #e94560; }}
    
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
  </style>
</head>
<body>
  <div id="header">
    <span style="font-size:1.3rem;">🏛️</span>
    <h1>ExifTool Archaeo</h1>
    <span class="sub">고고학 현장 사진 위치·방향 시각화 &nbsp;v{__version__}</span>
    <span id="visible-counter" style="margin-left:auto;color:#e94560;font-weight:700;">{len(features)}장</span>
  </div>
  
  <div id="app-container">
    <div id="map"></div>
    <div id="sidebar">
      <div id="sidebar-placeholder" style="text-align: center; margin-top: 60px; color: #a0aec0; line-height: 1.8;">
        <span style="font-size: 3.5rem; display: block; margin-bottom: 15px;">📸</span>
        <b style="color: #fff; font-size: 0.95rem;">사진 상세 정보 패널</b><br>
        지도 위 마커를 클릭하시면<br>촬영 정보와 사진 썸네일이<br>이곳에 상세히 연동되어 표시됩니다.
        <div style="margin-top:18px; font-size:0.75rem; color:#718096; line-height:1.7;">
          ⌨ <b style="color:#94a3b8;">단축키</b><br>
          ← / → : 이전·다음 사진<br>
          F : 전체 사진이 보이도록 맞춤<br>
          H : 마커 잠시 숨기기 / 표시 (유구 판독용)<br>
          Esc : 사진 확대창 닫기
        </div>
      </div>
      <div id="sidebar-content" style="display: none;">
        <!-- JS 동적 렌더링 -->
      </div>
    </div>
  </div>

  <!-- 라이트박스 구조 -->
  <div id="lightbox" onclick="closeLightbox()">
    <div class="lightbox-close" onclick="event.stopPropagation(); closeLightbox()">&times;</div>
    <div class="lightbox-guide">🖱️ 휠 = 확대/축소 · 드래그 = 이동 · 더블클릭 = 원래 크기 · Esc = 닫기</div>
    <img id="lightbox-img" alt="확대 사진" onclick="event.stopPropagation()">
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    // 맵 선언 (무제한 줌인 세팅)
    const map = L.map('map', {{
      maxZoom: 22,
      zoomControl: true
    }}).setView([{center_lat}, {center_lon}], 16);

    // ── 베이스 레이어 및 오버레이 (레이어 전환 시 백지 방지를 위해 인스턴스 분리) ──
    const vworldUrl = 'https://xdworld.vworld.kr/2d';
    const esriUrl = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}';
    const cartoLabelsUrl = 'https://{{s}}.basemaps.cartocdn.com/light_only_labels/{{z}}/{{x}}/{{y}}{{r}}.png';

    const vworldCombo = L.layerGroup([
      L.tileLayer(`${{vworldUrl}}/Satellite/service/{{z}}/{{x}}/{{y}}.png`, {{ attribution: 'Vworld', maxNativeZoom: 18, maxZoom: 22 }}),
      L.tileLayer(`${{vworldUrl}}/Hybrid/service/{{z}}/{{x}}/{{y}}.png`, {{ attribution: 'Vworld', maxNativeZoom: 18, maxZoom: 22 }})
    ]);
    const vworldSatellite = L.tileLayer(`${{vworldUrl}}/Satellite/service/{{z}}/{{x}}/{{y}}.png`, {{ attribution: 'Vworld', maxNativeZoom: 18, maxZoom: 22 }});
    const vworldBase = L.tileLayer(`${{vworldUrl}}/Base/service/{{z}}/{{x}}/{{y}}.png`, {{ attribution: 'Vworld', maxNativeZoom: 18, maxZoom: 22 }});

    const esriCombo = L.layerGroup([
      L.tileLayer(esriUrl, {{ attribution: 'Tiles &copy; Esri', maxNativeZoom: 18, maxZoom: 22 }}),
      L.tileLayer(cartoLabelsUrl, {{ attribution: '&copy; CartoDB', maxNativeZoom: 18, maxZoom: 22 }})
    ]);
    const esriSatellite = L.tileLayer(esriUrl, {{ attribution: 'Tiles &copy; Esri', maxNativeZoom: 18, maxZoom: 22 }});
    const osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ attribution: '&copy; OSM', maxNativeZoom: 19, maxZoom: 22 }});

    const vworldHybridOverlay = L.tileLayer(`${{vworldUrl}}/Hybrid/service/{{z}}/{{x}}/{{y}}.png`, {{ attribution: 'Vworld', maxNativeZoom: 18, maxZoom: 22 }});
    const cartoLabelsOverlay = L.tileLayer(cartoLabelsUrl, {{ attribution: '&copy; CartoDB', maxNativeZoom: 18, maxZoom: 22 }});

    // 기본 레이어로 브이월드 하이브리드 조합 맵 적용
    vworldCombo.addTo(map);

    L.control.layers(
      {{ 
        "🛰 브이월드 하이브리드 (위성+라벨)": vworldCombo,
        "🛰 브이월드 위성": vworldSatellite,
        "🗺 브이월드 일반지도": vworldBase,
        "🛰 Esri 위성 하이브리드": esriCombo,
        "🛰 Esri 위성 단독": esriSatellite,
        "🗺 일반 지도 (OSM)": osm 
      }},
      {{ 
        "🏷 브이월드 도로/지명 라벨 (추가 토글)": vworldHybridOverlay,
        "🏷 CartoDB 라벨 (추가 토글)": cartoLabelsOverlay 
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
    }}

    function closeLightbox() {{
      lb.style.display = 'none';
      lbImg.removeAttribute('src');
    }}

    function updateTransform() {{
      lbImg.style.transform = `translate(${{translateX}}px, ${{translateY}}px) scale(${{scale}})`;
    }}

    lbImg.addEventListener('wheel', function(e) {{
      e.preventDefault();
      const zoomFactor = 0.12;
      scale += (e.deltaY < 0) ? zoomFactor : -zoomFactor;
      scale = Math.min(Math.max(0.4, scale), 6.0);
      updateTransform();
    }});

    lbImg.addEventListener('dblclick', function(e) {{
      e.stopPropagation();
      scale = 1; translateX = 0; translateY = 0;
      updateTransform();
    }});

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

    // ── 마커 아이콘 팩토리 ───────────────────────────────────────────────────
    // 1. 밀집 구역에서 지도를 가리지 않도록 기존 22px 외곽 배경 원(r=11)을 제거.
    // 2. 드론(cam_type === 'drone')만 방향 화살표(또는 수직 촬영 과녁)를 표시.
    // 3. DSLR(cam_type === 'dslr')은 화살표 없이 폴더별 색상/도형 점으로만 깔끔하게 표시.
    function renderPointShape(shape, color) {{
      if (shape === 'diamond') {{
        return `<polygon points="14,9.5 18.5,14 14,18.5 9.5,14" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.3"/>`;
      }}
      if (shape === 'square') {{
        return `<rect x="10.2" y="10.2" width="7.6" height="7.6" rx="1.5" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.3"/>`;
      }}
      if (shape === 'triangle') {{
        return `<polygon points="14,9.2 18.6,17.5 9.4,17.5" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.3"/>`;
      }}
      return `<circle cx="14" cy="14" r="4.0" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.3"/>`;
    }}

    function makeCustomIcon(f, isSelected = false) {{
      const color = f.color || (f.cam_type === 'drone' ? '#e94560' : '#38bdf8');
      const selRing = isSelected
        ? `<circle cx="14" cy="14" r="9.5" fill="none" stroke="#ffffff" stroke-width="2.2"/>`
        : '';
      let svgBody = '';

      if (f.cam_type === 'drone') {{
        if (f.is_nadir) {{
          // 드론 수직(Nadir) 촬영: 지도를 가리지 않는 컴팩트 과녁
          svgBody = `
            ${{selRing}}
            <circle cx="14" cy="14" r="6.5" fill="none" stroke="${{color}}" stroke-width="1.8" stroke-dasharray="2 2"/>
            <line x1="14" y1="5" x2="14" y2="23" stroke="${{color}}" stroke-width="1" stroke-opacity="0.75"/>
            <line x1="5" y1="14" x2="23" y2="14" stroke="${{color}}" stroke-width="1" stroke-opacity="0.75"/>
            <circle cx="14" cy="14" r="2.6" fill="${{color}}" stroke="#0a0f1e" stroke-width="1"/>`;
        }} else if (f.has_dir) {{
          // 드론 경사 촬영: 중심점 + 바라보는 방향 화살표 (배경 큰 원 없음)
          svgBody = `
            ${{selRing}}
            <g transform="rotate(${{f.yaw}},14,14)">
              <line x1="14" y1="14" x2="14" y2="3.2" stroke="#0a0f1e" stroke-width="3.4" stroke-linecap="round"/>
              <line x1="14" y1="14" x2="14" y2="3.2" stroke="${{color}}" stroke-width="2.0" stroke-linecap="round"/>
              <polygon points="14,0.8 10.6,7.2 17.4,7.2" fill="${{color}}" stroke="#0a0f1e" stroke-width="0.9"/>
            </g>
            <circle cx="14" cy="14" r="3.4" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.2"/>`;
        }} else {{
          svgBody = `${{selRing}}<circle cx="14" cy="14" r="3.8" fill="${{color}}" stroke="#0a0f1e" stroke-width="1.3"/>`;
        }}
      }} else {{
        // DSLR / 일반 카메라: 화살표 없이 폴더별 깔끔한 점으로만 표현
        svgBody = `${{selRing}}${{renderPointShape(f.shape, color)}}`;
      }}

      const wrapClass = isSelected ? 'archaeo-marker-wrap selected-marker' : 'archaeo-marker-wrap';
      const svgHtml = `<div class="${{wrapClass}}"><svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">${{svgBody}}</svg></div>`;

      return L.divIcon({{
        html: svgHtml,
        className: '',
        iconSize: [28, 28],
        iconAnchor: [14, 14],
        popupAnchor: [0, -14],
      }});
    }}

    // ── 데이터 연동 및 폴더별 필터링 ─────────────────────────────────────────
    const features = {features_json};
    const folderGroups = {groups_json};
    const markerGroup = L.featureGroup();
    const markers = [];
    const activeGroups = new Set(folderGroups.map(g => g.key));
    let currentIndex = -1;
    let userScale = 1.0;

    // 줌 레벨에 따라 밀집 시 자동으로 마커를 작게, 확대 시 선명하게 조절
    function updateMarkerScale() {{
      const z = map.getZoom();
      let zoomScale = 1.0;
      if (z <= 14) zoomScale = 0.6;
      else if (z === 15) zoomScale = 0.72;
      else if (z === 16) zoomScale = 0.84;
      else if (z === 17) zoomScale = 0.94;
      else if (z >= 20) zoomScale = 1.12;
      const finalScale = (zoomScale * userScale).toFixed(2);
      document.documentElement.style.setProperty('--marker-scale', finalScale);
    }}
    map.on('zoomend', updateMarkerScale);

    function selectMarkerStyle(prevIdx, nextIdx) {{
      if (prevIdx >= 0 && prevIdx < markers.length) {{
        markers[prevIdx].setIcon(makeCustomIcon(features[prevIdx], false));
        markers[prevIdx].setZIndexOffset(0);
      }}
      if (nextIdx >= 0 && nextIdx < markers.length) {{
        markers[nextIdx].setIcon(makeCustomIcon(features[nextIdx], true));
        markers[nextIdx].setZIndexOffset(1000);
      }}
    }}

    // 사이드바에 i번째 사진의 상세 정보를 렌더링한다.
    function showDetail(i) {{
      if (i < 0 || i >= features.length) return;
      const prev = currentIndex;
      currentIndex = i;
      selectMarkerStyle(prev, i);
      const f = features[i];

      document.getElementById('sidebar-placeholder').style.display = 'none';
      const contentDiv = document.getElementById('sidebar-content');
      contentDiv.style.display = 'block';

      let imgHtml = '';
      if (f.thumb_b64) {{
        imgHtml = `<img class="sidebar-thumb" src="data:image/jpeg;base64,${{f.thumb_b64}}" onclick="openLightbox(this.src)" title="클릭하면 마우스 휠로 확대/축소 가능">`;
      }} else {{
        imgHtml = `<div style="width:100%; height:160px; background:#0f2044; border-radius:8px; display:flex; align-items:center; justify-content:center; color:#718096; margin-bottom:15px; border: 1px dashed #2d3748; font-size:0.8rem;">썸네일 이미지 없음</div>`;
      }}

      const isDrone = (f.cam_type === 'drone');
      const camBadge = `<span style="background:${{f.color}}; color:#0a0f1e; font-weight:700; padding:2px 7px; border-radius:4px; font-size:0.7rem; margin-right:6px; vertical-align:middle;">${{isDrone ? '드론' : 'DSLR'}} · ${{f.folder}}</span>`;
      const angleLabel = f.is_nadir ? '<span style="background:#e94560; color:#fff; padding:2px 6px; border-radius:4px; font-size:0.7rem; margin-left:6px; vertical-align:middle;">수직 촬영</span>' : '';
      const coordText = `${{f.lat.toFixed(6)}}, ${{f.lon.toFixed(6)}}`;

      const dirRows = isDrone ? `
          <tr><td class="label-col">🧭 촬영 방향</td><td style="font-weight:bold; color:${{f.color}}; font-size:0.9rem;">${{f.yaw_display}}</td></tr>
          <tr><td class="label-col" style="padding-left:15px;">짐벌 Yaw</td><td style="font-family:monospace; color:#e2e8f0;">${{f.gimbal_yaw}}</td></tr>
          <tr><td class="label-col" style="padding-left:15px;">짐벌 Pitch</td><td style="font-family:monospace; color:#e2e8f0;">${{f.gimbal_pitch}}</td></tr>
          <tr><td class="label-col" style="padding-left:15px;">기체 Yaw</td><td style="font-family:monospace; color:#e2e8f0;">${{f.flight_yaw}}</td></tr>
      ` : '';

      contentDiv.innerHTML = `
        <div class="nav-bar">
          <button class="nav-btn" onclick="gotoPhoto(${{i - 1}})" ${{i === 0 ? 'disabled' : ''}} title="이전 사진 (← 키)">◀ 이전</button>
          <span class="nav-count">${{i + 1}} / ${{features.length}}</span>
          <button class="nav-btn" onclick="gotoPhoto(${{i + 1}})" ${{i === features.length - 1 ? 'disabled' : ''}} title="다음 사진 (→ 키)">다음 ▶</button>
        </div>
        ${{imgHtml}}
        <div style="margin-bottom:6px;">${{camBadge}}${{angleLabel}}</div>
        <h2 style="font-size:1.1rem; font-weight:700; margin-bottom:4px; color:#fff; word-break:break-all; line-height:1.35;">${{f.fname}}</h2>
        <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:15px;">🕐 촬영시각: ${{f.dt}}</div>
        <hr style="border:none; border-top:1px solid #2d3748; margin-bottom:15px;">

        <table class="sidebar-table">
          <tr><td class="label-col">📂 입력 폴더</td><td style="color:#e2e8f0;">${{f.folder}} (${{isDrone ? '드론' : 'DSLR'}})</td></tr>
          <tr><td class="label-col">📍 위도/경도</td><td><span style="font-family:monospace; font-weight:bold; color:#fff;">${{coordText}}</span> <button class="copy-btn" onclick="copyCoord('${{coordText}}', this)" title="좌표 복사">복사</button></td></tr>
          <tr><td class="label-col">⬆ GPS 고도</td><td style="color:#fff;">${{f.alt}}</td></tr>
          ${{dirRows}}
          <tr><td class="label-col">📷 카메라 장비</td><td style="color:#fff;">${{f.make}} ${{f.model}}</td></tr>
        </table>
      `;
    }}

    // 이전/다음 사진으로 이동하며 지도도 함께 따라간다.
    function gotoPhoto(i) {{
      if (i < 0 || i >= features.length) return;
      showDetail(i);
      const m = markers[i];
      if (map.hasLayer(m)) {{
        map.panTo(m.getLatLng());
        m.openPopup();
      }}
    }}

    function copyCoord(text, btn) {{
      const done = () => {{
        const old = btn.textContent;
        btn.textContent = '복사됨';
        setTimeout(() => {{ btn.textContent = old; }}, 1200);
      }};
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
      }} else {{
        fallbackCopy(text, done);
      }}
    }}

    // file:// 로 열었을 때 clipboard API 가 막히는 경우를 위한 대체 경로
    function fallbackCopy(text, done) {{
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {{ document.execCommand('copy'); done(); }} catch (e) {{ /* 무시 */ }}
      document.body.removeChild(ta);
    }}

    features.forEach((f, i) => {{
      const marker = L.marker([f.lat, f.lon], {{ icon: makeCustomIcon(f, false) }})
        .bindPopup(f.popup, {{ maxWidth: 220 }});
      marker.on('click', () => showDetail(i));
      markers.push(marker);
      markerGroup.addLayer(marker);
    }});

    markerGroup.addTo(map);
    map.fitBounds(markerGroup.getBounds().pad(0.18), {{ maxZoom: 19 }});
    updateMarkerScale();

    function applyGroupFilter() {{
      let visibleCount = 0;
      features.forEach((f, i) => {{
        const m = markers[i];
        if (activeGroups.has(f.group_key)) {{
          if (!markerGroup.hasLayer(m)) markerGroup.addLayer(m);
          visibleCount++;
        }} else {{
          if (markerGroup.hasLayer(m)) markerGroup.removeLayer(m);
        }}
      }});
      const counterEl = document.getElementById('visible-counter');
      if (counterEl) {{
        counterEl.textContent = visibleCount === features.length
          ? `${{features.length}}장`
          : `${{visibleCount}} / ${{features.length}}장`;
      }}
    }}

    function toggleMarkersHidden() {{
      const hidden = document.body.classList.toggle('markers-hidden');
      const btn = document.getElementById('toggle-hide-btn');
      if (btn) {{
        btn.classList.toggle('active', hidden);
        btn.textContent = hidden ? '👁 마커 다시 표시 (H)' : '👁 마커 잠시 숨기기 (H)';
      }}
    }}

    // 축척 막대 — 현장 거리 감각을 잡는 데 필요하다.
    L.control.scale({{ position: 'bottomright', imperial: false, maxWidth: 180 }}).addTo(map);

    // ── 키보드 단축키 ──────────────────────────────────────────────────────
    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') {{
        if (lb.style.display === 'flex') closeLightbox();
        return;
      }}
      if (lb.style.display === 'flex') return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'ArrowLeft')  {{ e.preventDefault(); gotoPhoto(currentIndex <= 0 ? 0 : currentIndex - 1); }}
      if (e.key === 'ArrowRight') {{ e.preventDefault(); gotoPhoto(currentIndex + 1); }}
      if (e.key === 'f' || e.key === 'F') {{
        if (markerGroup.getLayers().length > 0) {{
          map.fitBounds(markerGroup.getBounds().pad(0.18), {{ maxZoom: 19 }});
        }}
      }}
      if (e.key === 'h' || e.key === 'H') {{
        toggleMarkersHidden();
      }}
    }});

    // ── 인터랙티브 범례 & 마커 가림 방지 컨트롤 ───────────────────────────
    const legend = L.control({{position:'bottomleft'}});
    legend.onAdd = () => {{
      const d = L.DomUtil.create('div', 'legend');
      L.DomEvent.disableClickPropagation(d);
      L.DomEvent.disableScrollPropagation(d);

      const groupItemsHtml = folderGroups.map((g, idx) => {{
        const isDrone = g.cam_type === 'drone';
        const badgeSvg = isDrone
          ? `<svg width="14" height="14" viewBox="0 0 14 14"><line x1="7" y1="12" x2="7" y2="3" stroke="${{g.color}}" stroke-width="2"/><polygon points="7,1 4,6 10,6" fill="${{g.color}}"/><circle cx="7" cy="10" r="2.2" fill="${{g.color}}"/></svg>`
          : `<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="4" fill="${{g.color}}" stroke="#0a0f1e" stroke-width="1"/></svg>`;
        const typeLabel = isDrone ? '드론·화살표' : 'DSLR·점';
        return `
          <label class="legend-item" title="${{g.folder}} (${{typeLabel}})">
            <input type="checkbox" checked data-group-key="${{g.key}}">
            <span class="legend-swatch">${{badgeSvg}}</span>
            <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              <b style="color:${{g.color}}">[${{isDrone ? '드론' : 'DSLR'}}]</b> ${{g.folder}}
            </span>
            <span style="color:#94a3b8; font-family:monospace;">${{g.count}}</span>
          </label>`;
      }}).join('');

      d.innerHTML = `
        <div class="legend-title">
          <span>🗂 폴더별 표시 &amp; 마커 조절</span>
        </div>
        <div class="legend-group-list">${{groupItemsHtml}}</div>
        <div style="font-size:0.7rem; color:#94a3b8; margin-bottom:6px; border-top:1px solid #2d3748; padding-top:5px;">
          • <b>드론</b>: 점 + 촬영방향 화살표 (수직촬영은 점선 과녁)<br>
          • <b>DSLR</b>: 방향 화살표 없이 폴더별 점으로만 표시
        </div>
        <div class="ctrl-row">
          <span>마커 크기</span>
          <input id="scale-slider" type="range" min="0.4" max="1.8" step="0.05" value="1.0">
        </div>
        <div class="ctrl-row">
          <span>마커 투명도</span>
          <input id="opacity-slider" type="range" min="0.15" max="1.0" step="0.05" value="0.88">
        </div>
        <button id="toggle-hide-btn" class="hide-btn" onclick="toggleMarkersHidden()">👁 마커 잠시 숨기기 (H)</button>
      `;

      d.querySelectorAll('input[type="checkbox"][data-group-key]').forEach(cb => {{
        cb.addEventListener('change', (ev) => {{
          const key = ev.target.getAttribute('data-group-key');
          if (ev.target.checked) activeGroups.add(key);
          else activeGroups.delete(key);
          applyGroupFilter();
        }});
      }});

      const scaleSlider = d.querySelector('#scale-slider');
      scaleSlider.addEventListener('input', (ev) => {{
        userScale = parseFloat(ev.target.value) || 1.0;
        updateMarkerScale();
      }});

      const opacitySlider = d.querySelector('#opacity-slider');
      opacitySlider.addEventListener('input', (ev) => {{
        document.documentElement.style.setProperty('--marker-opacity', ev.target.value);
      }});

      return d;
    }};
    legend.addTo(map);
  </script>
</body>
</html>
"""

    out = Path(output_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"🗺️  지도 저장: {output_path}  ({len(features)}개 지점, {size_mb:.1f} MB)")
    return True


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
  python archaeo_gps.py --drone ./드론사진 --dslr ./DSLR사진 -o output.csv --map map.html
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
    parser.add_argument("-i", "--input", action="append", default=[],
                        help="입력 폴더 또는 이미지 파일 경로 (드론/DSLR 자동 판별, 여러 번 지정 가능)")
    parser.add_argument("--drone", action="append", default=[],
                        help="드론 사진 폴더 또는 파일 경로 (지도에 점 + 촬영방향 화살표 표시)")
    parser.add_argument("--dslr", action="append", default=[],
                        help="DSLR/일반 카메라 사진 폴더 또는 파일 경로 (지도에 화살표 없이 점만 표시)")
    parser.add_argument("-o", "--output",
                        help="출력 CSV 파일 경로 (예: output.csv)")
    parser.add_argument("--crs", default="tm",
                        help="출력 좌표계 (기본: tm = EPSG:5186 TM중부원점)")
    parser.add_argument("--map", default=None,
                        help="Leaflet HTML 지도 출력 파일 경로 (선택)")
    parser.add_argument("--exiftool", default=None,
                        help="exiftool 실행 파일 경로 (미지정 시 자동 탐색)")
    parser.add_argument("--geojson", default=None,
                        help="GeoJSON(WGS84) 출력 파일 경로 (선택, QGIS 바로 열기용)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="하위 폴더까지 재귀적으로 사진 탐색")
    parser.add_argument("--no-transform", action="store_true",
                        help="좌표 변환 없이 WGS84만 출력")
    parser.add_argument("--thumb-size", type=int, default=THUMB_SIZE_DEFAULT,
                        metavar="PX",
                        help=f"지도 썸네일 최대 크기 px (기본: {THUMB_SIZE_DEFAULT}, "
                             "0이면 썸네일 생략 → 지도 용량 대폭 감소)")
    parser.add_argument("--list-crs", action="store_true",
                        help="지원하는 좌표계 목록을 출력하고 종료")
    parser.add_argument("--version", action="version",
                        version=f"archaeo_gps {__version__}")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # ── 좌표계 목록만 출력하고 종료 ────────────────────────────────────────
    if args.list_crs:
        print("지원 좌표계 (--crs 에 사용):")
        for key, epsg in CRS_PRESETS.items():
            print(f"  {key:<16} {epsg}")
        print("\n  또는 EPSG 코드 직접 입력: epsg:5186, epsg:32652, …")
        return

    has_inputs = bool(args.input or args.drone or args.dslr)
    if not has_inputs or not args.output:
        parser.error("입력 경로(-i/--input, --drone, --dslr 중 하나 이상)와 -o/--output 은 필수입니다. (목록만 보려면 --list-crs)")

    # ── 의존성 경고 ──────────────────────────────────────────────────────────
    if not HAS_PYPROJ and not args.no_transform:
        print("⚠️  pyproj 미설치 → 좌표 변환이 비활성화됩니다. (pip install pyproj)")
    if args.map and not HAS_PILLOW and args.thumb_size > 0:
        print("⚠️  Pillow 미설치 → 지도 썸네일 없이 생성됩니다. (pip install Pillow)")

    # ── exiftool 탐색 ────────────────────────────────────────────────────────
    try:
        exiftool_path = find_exiftool(args.exiftool)
        print(f"🔧 ExifTool: {exiftool_path}")
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # ── 이미지 목록 (드론 / DSLR / 일반 구분 수집) ──────────────────────────
    try:
        images, meta_by_path = collect_grouped_images(
            drone_inputs=args.drone,
            dslr_inputs=args.dslr,
            generic_inputs=args.input,
            recursive=args.recursive,
        )
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if not images:
        scope = "하위 폴더 포함" if args.recursive else "하위 폴더 미포함 (-r 로 재귀 탐색)"
        all_inputs = ", ".join(args.drone + args.dslr + args.input)
        print(f"⚠️  지원 이미지 파일이 없습니다: {all_inputs}  [{scope}]", file=sys.stderr)
        sys.exit(1)

    print(f"📂 이미지 {len(images)}장 발견")

    # ── EXIF 추출 ────────────────────────────────────────────────────────────
    print("📡 EXIF 데이터 추출 중…")
    raw_records, et_errors = run_exiftool(exiftool_path, images)
    if et_errors:
        for err in et_errors:
            print(f"  ⚠️ {err}", file=sys.stderr)

    if not raw_records:
        print("❌ EXIF 데이터를 추출하지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    records = []
    for r in raw_records:
        sf = r.get("SourceFile", "")
        meta = meta_by_path.get(sf, {})
        records.append(
            parse_record(
                r,
                camera_type=meta.get("camera_type"),
                folder_label=meta.get("folder"),
            )
        )

    gps_ok = sum(1 for r in records if r["Lat_DD"] is not None)
    drone_cnt = sum(1 for r in records if r.get("CameraType") == CAMERA_TYPE_DRONE)
    dslr_cnt = sum(1 for r in records if r.get("CameraType") == CAMERA_TYPE_DSLR)
    dir_ok = sum(
        1 for r in records
        if r.get("CameraType") == CAMERA_TYPE_DRONE and r["CamDirection_deg"] is not None
    )
    print(
        f"   GPS 있음: {gps_ok}/{len(records)}장  |  "
        f"드론: {drone_cnt}장 (방향 {dir_ok}장)  |  DSLR: {dslr_cnt}장"
    )
    if gps_ok == 0:
        print("⚠️  GPS 좌표가 있는 사진이 없습니다. 좌표 변환과 지도 생성은 생략됩니다.",
              file=sys.stderr)

    # ── 좌표 변환 ────────────────────────────────────────────────────────────
    target_crs = ""
    transformer = None
    crs_input = args.crs.lower().strip()
    resolved_crs = CRS_PRESETS.get(crs_input, crs_input)
    # wgs84 는 변환이 항등이므로 변환기를 만들지 않는다 (GUI 동작과 일치).
    if not args.no_transform and HAS_PYPROJ and resolved_crs != CRS_PRESETS["wgs84"]:
        target_crs = resolved_crs
        transformer = build_transformers(target_crs)
        if transformer:
            print(f"🌐 좌표 변환: WGS84 → {target_crs}")
        else:
            target_crs = ""

    # ── CSV 출력 ─────────────────────────────────────────────────────────────
    output_dir = Path(args.output).parent
    if str(output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(records, args.output, transformer=transformer, target_crs=target_crs)

    # ── GeoJSON 출력 ─────────────────────────────────────────────────────────
    if args.geojson:
        write_geojson(records, args.geojson)

    # ── HTML 지도 ────────────────────────────────────────────────────────────
    if args.map:
        thumb_size = max(0, args.thumb_size)
        write_map(records, args.map,
                  thumb_size=thumb_size if thumb_size > 0 else 0,
                  progress=_cli_thumb_progress if thumb_size > 0 else None,
                  exiftool_path=exiftool_path)

    # ── 요약 출력 ────────────────────────────────────────────────────────────
    _print_summary(records)


def _cli_thumb_progress(done: int, total: int) -> None:
    """CLI 에서 썸네일 추출 진행률을 한 줄로 갱신 표시한다."""
    if total <= 0:
        return
    pct = done * 100 // total
    end = "\n" if done >= total else ""
    print(f"\r🖼️  썸네일 생성 중… {done}/{total} ({pct}%)", end=end, flush=True)


MAX_SUMMARY_ROWS = 50


def _print_summary(records: list[dict]) -> None:
    """추출 결과 요약 표를 출력한다."""
    name_w = max([len(str(r.get("FileName", ""))) for r in records] + [8])
    name_w = min(name_w, 34)
    grp_w = 18
    line = "─" * (name_w + grp_w + 46)

    print()
    print(line)
    print(f"{'파일명':<{name_w}} {'구분/폴더':<{grp_w}} {'방향(°)':>8} {'방위':>5} {'위도':>12} {'경도':>13}")
    print(line)
    shown = records[:MAX_SUMMARY_ROWS]
    for r in shown:
        lat_s = f"{r['Lat_DD']:.6f}" if r["Lat_DD"] is not None else "N/A"
        lon_s = f"{r['Lon_DD']:.6f}" if r["Lon_DD"] is not None else "N/A"
        is_drone = r.get("CameraType") == CAMERA_TYPE_DRONE
        dir_s = (
            f"{r['CamDirection_deg']:.1f}"
            if (is_drone and r.get("CamDirection_deg") is not None)
            else "-"
        )
        card = (r.get("CamDirection_cardinal", "") or "-") if is_drone else "-"
        cam_lbl = "드론" if is_drone else "DSLR"
        folder_lbl = str(r.get("Folder", "기본"))
        grp_s = f"{cam_lbl}:{folder_lbl}"[:grp_w]
        name = str(r.get("FileName", ""))[:name_w]
        print(f"{name:<{name_w}} {grp_s:<{grp_w}} {dir_s:>8} {card:>5} {lat_s:>12} {lon_s:>13}")
    if len(records) > MAX_SUMMARY_ROWS:
        print(f"  … 외 {len(records) - MAX_SUMMARY_ROWS}개 레코드 (전체 내용은 CSV 참조)")
    print(line)


if __name__ == "__main__":
    main()
