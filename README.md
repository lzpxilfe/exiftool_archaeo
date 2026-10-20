# 🏛️ ExifTool Archaeo

드론·DSLR 현장 사진의 EXIF에서 촬영 위치와 방향 메타데이터를 읽고, CSV·GeoJSON·대화형 HTML 지도로 정리하는 고고학 조사 보조 도구입니다.

**v1.3.0 — 좌표 검증, 방향 출처 구분, 분할 추출 및 안전한 지도 출력**

> 지도에 표시되는 좌표는 카메라에 기록된 **촬영 위치**이며, 사진 속 유구·유물의 위치가 아닙니다. 좌표의 소수 자릿수와 실제 측정 정확도는 별개입니다. 사진을 원래 장비에서 촬영한 상태 그대로 보관하고, 조사 도면에 적용하기 전에 위치·시각·좌표계를 확인하세요.

## 주요 기능

- JPG·TIFF·HEIC 및 Nikon NEF, DJI DNG 등 지원 확장자의 EXIF 일괄 추출. RAW·HEIC 썸네일은 Pillow에서 열지 못할 때 ExifTool 내장 프리뷰로 대체합니다.
- 드론·DSLR 폴더를 분리 입력하고, 여러 폴더·하위 폴더를 색상과 도형으로 구분합니다.
- WGS84 위치를 대상 좌표계로 변환하여 UTF-8 BOM CSV에 저장합니다. GeoJSON은 WGS84 `[경도, 위도]` 순서입니다.
- Leaflet HTML 지도에서 위성·일반 지도 전환, 폴더 필터, 사진 사이드바, 썸네일 확대, 마커 크기·투명도 조절을 지원합니다.
- 사진 원본의 EXIF는 **읽기만** 합니다. GPS가 없는 사진에 좌표를 임의로 만들어 넣지 않습니다.

## v1.3.0 변경 사항

### 1. 위치 품질 검증

위도 `-90~90`, 경도 `-180~180`, 유한 숫자 여부, 도분초 형식, 분·초 범위, 반구 표기의 모순을 확인합니다. `NaN`, 무한대, 위도만 있는 사진, 잘못된 DMS를 정상 위치로 취급하지 않습니다.

| `GPSValidation` | 의미 | 지도·GeoJSON |
| --- | --- | --- |
| `valid` | 좌표 형식·범위 검사를 통과함. 측정 정확도를 보장하지 않음 | 포함 |
| `missing` | 위도·경도 모두 없음 | 제외 |
| `incomplete` | 위도 또는 경도 누락 | 제외 |
| `invalid` | 형식·범위·반구 모순 또는 GPS 측정 무효 상태 | 제외 |

문제 있는 사진도 **CSV 행은 유지**하며 `GPSLatitude_raw`, `GPSLongitude_raw`, 반구 원본값과 `GPSWarnings`를 남깁니다. `(0, 0)`은 실제 존재하는 좌표이므로 삭제하지 않고 별도 확인 경고를 붙입니다.

기록되어 있는 경우에만 `GPSHPositioningError_m`, `GPSDOP`, `GPSStatus`, GPS 날짜·시각, `OffsetTimeOriginal`, `GPSProcessingMethod` 등을 보존합니다. DOP는 미터 단위 위치오차가 아니며, `GPSPositionSource=EXIF`는 읽어 온 메타데이터 경로를 뜻할 뿐 스마트폰·외장 수신기·RTK 여부를 판정한 결과가 아닙니다.

추출 단계에서는 DMS 초 소수점 6자리를 사용하고, 읽은 좌표를 출력 전에 소수점 8자리로 추가 반올림하지 않습니다. 이 처리는 **숫자 표현 손실을 줄이는 것**이지 GPS 정확도를 높이는 것이 아닙니다. 기존의 “0.03 mm급 측정 정확도”로 오해될 수 있는 표현과 고정 처리시간 주장은 제거했습니다.

### 2. 촬영 방향과 기체 방향 구분

카메라 방향의 선택 순서는 **`GimbalYawDegree` → `GPSImgDirection`**입니다. `FlightYawDegree`는 `FlightYaw`로 별도 보존하지만 카메라 방향으로 대체하지 않습니다. 기체 Yaw만 있는 사진에는 촬영 방향 화살표를 만들지 않습니다.

| 필드 | 의미 |
| --- | --- |
| `CamDirectionSource` | 선택한 원본 태그. `GimbalYawDegree`, `GPSImgDirection` 또는 빈 값 |
| `CamDirectionReference` | `T`: 진북, `M`: 자북, `U`: 북 기준 미확인 |
| `CamDirectionWarnings` | 기체 방향만 존재함, 북 기준 미확인, 자북 미보정 등의 경고 |

짐벌 Yaw 태그만으로 북 기준을 확정하지 않으므로 `U`로 표시합니다. 진북은 일반 화살표, 북 기준 미확인은 점선 화살표로 구분합니다. **자북은 편각을 보정하지 않고 진북 지도에 바로 그리지 않으며**, 원래 방향값과 경고는 결과에 보존합니다. 수직 촬영은 기존 과녁 표시를 유지합니다.

DSLR은 기존처럼 **점 표시가 기본**입니다. 생성된 지도의 왼쪽 아래 **“DSLR 방향 표시”**를 선택하면 사용 가능한 방향 메타데이터가 있는 사진만 화살표로 전환합니다. 위치정보만 들어 있는 D5600 사진에 촬영 방향을 추정해서 붙이지 않습니다.

### 3. 대량 처리 및 출력 안정성

ExifTool 호출은 기본 **500장 단위**로 나눕니다. 한 배치가 실패해도 앞서 읽은 결과를 버리지 않고 다음 배치를 처리하며, 실패 배치 번호와 ExifTool 경고를 전달합니다. 성공 종료 코드에 포함된 경고와 개별 파일의 `Error`도 보존합니다.

이는 ExifTool JSON의 일회성 처리 크기를 제한하는 개선입니다. 최종 레코드·썸네일·HTML 전체를 완전히 스트리밍하거나 디스크에 중간 저장하는 구조는 아니므로, 아주 큰 조사 자료는 폴더별로 나누고 `--thumb-size 0`도 활용하세요. 자동 재시도·재개 기능은 포함하지 않습니다.

파일 경로의 개행문자가 ExifTool 옵션으로 해석되는 것을 차단합니다. HTML에는 폴더명 대신 내부 그룹 ID를 사용하고, 스크립트 삽입을 방지하도록 JSON을 직렬화합니다. GeoJSON에는 비표준 `NaN`·`Infinity` 숫자를 출력하지 않습니다.

## Windows: BAT 한 번으로 EXE 만들기

**`build_windows.bat`를 더블클릭하면 ExifTool 엔진까지 포함한 `dist/ExifToolArchaeo.exe`를 만듭니다.**
완성된 EXE를 실행할 때는 Python 설치, 별도의 `exiftool.exe` 복사, 환경 변수 설정이 필요하지 않습니다.

1. 이 저장소 전체를 내려받아 **압축을 푼 폴더**를 사용합니다. Git으로 받았다면 `git pull`로 갱신합니다.
2. **`build_windows.bat`를 더블클릭**합니다. BAT 파일만 따로 옮기지 마세요.
3. `SUCCESS`가 표시되면 **`dist/ExifToolArchaeo.exe`**를 실행합니다. 기존 EXE가 열려 있으면 닫고 빌드하세요.

**빌드하는 PC의 준비물:** Windows 10/11 x64, 64비트 x86 Python **3.10 이상**(pip·Tcl/Tk 포함), 인터넷 연결.
Python은 [공식 설치 프로그램](https://www.python.org/downloads/windows/)으로 한 번 설치하면 됩니다.
BAT는 `py -3` 또는 `python`을 확인하고, 적합한 Python이 없으면 안내 후 종료합니다.
Python을 몰래 설치하거나 시스템 PATH를 변경하지 않으며, 관리자 권한도 요구하지 않습니다.
**Windows EXE 빌드는 Windows에서 실행해야 합니다.**

### BAT가 자동으로 처리하는 것

- 프로젝트 안에 전용 **`.venv-build`** 환경을 만들고 Python 의존성·PyInstaller를 설치합니다. 전역 Python 패키지는 건드리지 않습니다.
- 공식 SourceForge 배포처에서 **ExifTool 13.59 64비트 ZIP**을 내려받고, 고정된 **SHA-256 체크섬**을 확인합니다. HTML 오류 페이지나 변조된 ZIP은 실행하지 않습니다.
- `exiftool(-k).exe`를 `exiftool.exe`로 바꾸고 **`exiftool_files` 폴더와 배포판 안내문을 통째로** EXE에 포함합니다. 사용자가 엔진을 따로 구할 필요가 없습니다.
- 기존 앱 테스트와 새 패키징 테스트를 실행한 뒤 최신 PyInstaller 6.x 스펙 형식으로 빌드합니다.
- **EXE만 별도의 한글·공백 경로로 복사**하고, 외부 ExifTool 경로 설정을 제거한 상태에서 자동 검사합니다. 엔진 실행, 합성 JPEG의 GPS 읽기, 좌표 변환, 썸네일, CSV·GeoJSON·HTML 생성을 검사하며 **통과한 EXE만** `dist`로 옮깁니다.

자동 검사는 임시로 만든 합성 사진만 사용합니다. 실제 조사 사진의 EXIF는 수정하지 않습니다.
엔진은 재현 가능한 빌드를 위해 버전과 해시를 함께 고정했습니다. 임의의 최신 버전으로 자동 교체하지 않습니다.
출처와 체크섬은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 기록되어 있습니다.

```text
프로젝트 폴더/
  build_windows.bat           ← 이것을 실행
  .venv-build/                ← 빌드용 Python 환경 (자동 생성)
  .build-cache/               ← 검증한 ZIP·런타임·로그 (자동 생성)
    build_windows.log        ← 실패 원인 확인용 전체 로그
    self-test.json           ← 완성 EXE의 자동 검사 결과
  dist/
    ExifToolArchaeo.exe       ← 엔진이 내장된 실행 파일
    THIRD_PARTY_NOTICES.md    ← 배포 시 보존할 외부 구성 요소 안내
```

`exiftool.exe`·`exiftool_files`를 EXE 옆에 따로 둘 필요는 없습니다. EXE는 실행 시 내부 구성 요소를 임시 폴더에 풀어 사용합니다.
명시적으로 지정한 `EXIFTOOL_PATH`나 CLI의 `--exiftool`은 계속 사용할 수 있으나, 일반 사용자는 지정하지 않아도 됩니다.
이전에 잘못 설정한 환경 변수가 있다면 제거하고 내장 엔진을 사용하세요.

### 빌드에 실패했을 때

성공으로 표시하지 않고 창을 유지하며 **`.build-cache/build_windows.log`**에 전체 오류를 남깁니다.
최종 자동 검사가 실패하면 기존 `dist/ExifToolArchaeo.exe`를 덮어쓰지 않습니다.
검사 보고서가 생성된 경우 `.build-cache/self-test.json`에도 원인이 남습니다.

네트워크가 공식 다운로드를 차단하는 경우에만 공식 `exiftool-13.59_64.zip`을 `.build-cache`에 넣고 다시 실행하세요.
캐시 ZIP도 같은 해시 검사를 거치며, 해시가 다르면 중단합니다. 손상된 ZIP은 해당 파일만 삭제하고 재실행하세요.
회사 PC 보안 정책이나 백신이 실행 파일을 차단하는 경우에는 보안 기능을 끄지 말고 관리자에게 확인하세요.

명령줄에서 종료 대기를 생략하려면 `build_windows.bat --no-pause`를 사용합니다.

### Python 소스로 직접 실행 (개발자·macOS·Linux)

소스 실행에는 Python **3.10 이상**과 별도 ExifTool이 필요합니다. 위의 Windows BAT로 만든 EXE와는 다른 실행 방식입니다.

```bash
git clone https://github.com/lzpxilfe/exiftool_archaeo.git
cd exiftool_archaeo
pip install -r requirements.txt
python archaeo_gps_gui.py
```

`pyproj`는 좌표 변환, `Pillow`는 썸네일 생성에 사용합니다. 소스 실행용 ExifTool은
[공식 배포처](https://exiftool.sourceforge.net/)에서 준비하고 `EXIFTOOL_PATH` 또는 CLI의 `--exiftool`로 지정합니다.
Windows 분리형 엔진을 직접 사용할 때는 `exiftool.exe`와 `exiftool_files`를 함께 보관해야 합니다.

```powershell
# 소스 실행 시에만 필요한 예시
$env:EXIFTOOL_PATH = 'D:\tools\exiftool\exiftool.exe'
python archaeo_gps_gui.py
```

```bash
# macOS / Linux 소스 실행
export EXIFTOOL_PATH=/usr/local/bin/exiftool
python archaeo_gps_gui.py
```

## CLI 사용 예시

```bash
# 드론과 DSLR 폴더를 함께 처리
python archaeo_gps.py --drone "D:\조사\드론" --dslr "D:\조사\DSLR" -o "D:\조사\결과.csv" --map "D:\조사\사진지도.html" --geojson "D:\조사\결과.geojson"

# 여러 입력 폴더·하위 폴더 처리
python archaeo_gps.py --dslr "D:\조사\1일차" --dslr "D:\조사\2일차" -r -o "D:\조사\결과.csv"

# 장비 자동 분류, 썸네일 없이 지도 생성
python archaeo_gps.py -i ./photos -r -o result.csv --map map.html --thumb-size 0

# WGS84만 출력 / 좌표계 코드 직접 지정
python archaeo_gps.py -i ./photos -o result.csv --no-transform
python archaeo_gps.py -i ./photos -o result.csv --crs epsg:5186
python archaeo_gps.py --list-crs
```

| 옵션 | 설명 |
| --- | --- |
| `--drone`, `--dslr` | 장비 종류를 지정하는 폴더·파일 입력. 반복 사용 가능 |
| `-i`, `--input` | 메타데이터로 장비 종류를 자동 분류. 반복 사용 가능 |
| `-o`, `--output` | CSV 출력 경로 |
| `-r`, `--recursive` | 하위 폴더 탐색 |
| `--crs` | 대상 좌표계 별칭 또는 EPSG 코드. 기본 `tm` = EPSG:5186 |
| `--no-transform` | 투영 좌표 변환 생략 |
| `--map`, `--geojson` | HTML 지도·GeoJSON 출력 경로 |
| `--thumb-size` | 썸네일 최대 크기. 기본 640, 0이면 생략 |
| `--exiftool` | ExifTool 실행 파일 또는 폴더 지정 |
| `--list-crs`, `--version` | 좌표계 목록·버전 표시 |

프로그램에서 직접 호출할 때는 `run_exiftool(path, images, batch_size=500, progress=callback)`으로 배치 크기와 진행 콜백을 지정할 수 있습니다. 기존 두 인자 호출도 유지됩니다. GUI는 공통 추출·검증·출력 함수를 그대로 사용합니다.

## 좌표계와 현장 적용

주요 별칭은 `tm` = EPSG:5186, `tm_west` = EPSG:5185, `tm_east` = EPSG:5187, `tm_eastsea` = EPSG:5188, `utmk` = EPSG:5179입니다. **기본값이 모든 현장 도면에 맞는 것은 아닙니다.** 현장 기준점과 도면의 정확한 EPSG 코드를 확인하세요.

CSV의 `Proj_X`는 동향(Easting), `Proj_Y`는 북향(Northing)입니다. 국내 측량에서 X를 북향, Y를 동향으로 표기하는 도면·장비에 입력할 때는 열 순서를 확인해야 합니다. GeoJSON에는 `[경도, 위도]`를 저장하고 별도의 CRS 멤버를 붙이지 않습니다.

과거 버전의 `tm_mod`, `tm_mod_west`, `tm_mod_east` 별칭은 호환성을 위해 각각 EPSG:5181, 5182, 5183을 유지합니다. 이름만으로 지리적 범위나 Bessel 좌표계를 판단하지 말고 **EPSG 코드 자체를 기준으로 선택**하세요.

현장에 나가기 전에는 원본 사진 몇 장을 실제로 처리해 GPS 포함 여부를 확인하세요. 카메라·휴대전화의 시계와 연결 상태도 확인해야 합니다. GPS 시각 등을 결과에 보존하지만 위치정보가 최신인지, 기록된 수평오차가 실제 오차를 대표하는지를 자동으로 보장하지 않습니다.

HTML의 지도 타일과 Leaflet 라이브러리는 외부 서버에서 가져오므로 인터넷이 필요합니다. HTML에는 사진 썸네일과 위치가, CSV·GeoJSON에는 원본 경로가 포함될 수 있으므로 조사자료를 공유하기 전에 내용을 확인하세요.

## 테스트

```bash
# 기존 앱 테스트 + 패키징·다운로드·런타임 회귀 테스트
python -m unittest discover -p "test_*.py" -v
```

ExifTool 실행 파일이나 실사 사진 없이 수행할 수 있습니다. 배치 호출은 모의 프로세스로 검증하며, `pyproj`가 있으면 실제 좌표 변환 왕복 테스트도 실행합니다. Node.js가 있으면 생성된 지도 JavaScript 문법과 DSLR 방향 토글의 아이콘 동작도 확인합니다. 선택 도구가 없는 경우 해당 테스트는 건너뜁니다.

새 패키징 단위 테스트는 네트워크·실제 Windows 엔진 없이 실행합니다. 실제 EXE 자동 검사는 Windows에서 BAT를 실행할 때 별도로 수행합니다. 실제 D5600·DJI 사진, 현장 GPS 오차, 외부 지도 타일 및 GUI 조작은 별도의 통합 검증 대상입니다. **GPX 기반 사후 위치 보완, 자북 편각 보정, 자동 실패 재시도는 이번 버전에 포함되지 않습니다.**

## 라이선스

ExifTool 엔진과 그 배포 파일의 라이선스는 원본 ExifTool 문서 및 저장소의 라이선스 파일을 따릅니다. 고고학 전용 커스텀 코드의 MIT 라이선스 안내는 기존 배포 정책을 유지합니다. 엔진과 커스텀 코드를 함께 재배포할 때는 각각의 조건을 확인하세요.
