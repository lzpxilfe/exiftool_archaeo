# ExifTool Archaeo

드론이나 DSLR로 찍은 사진에서 **GPS 좌표**와 **촬영 방향**을 꺼내,  
고고학 현장에서 바로 쓸 수 있는 형태로 정리해주는 도구입니다.

특히 **우거진 산림 지형**에서 사진만 보고 어느 방향을 찍었는지 알기 어려울 때,  
DJI 드론 사진에 기록된 짐벌 방위각(GimbalYaw)을 읽어 나침반 방향(N/NE/SE…)까지 알려줍니다.

---

## 어떤 상황에 쓰나요?

- 드론·DSLR로 촬영한 현장 사진의 좌표를 **한 번에 CSV로** 정리하고 싶을 때
- GPS 좌표를 한국 측량 표준 좌표계(TM중부원점 등)로 **변환**해야 할 때
- 각 사진을 **지도에서 위치와 방향**으로 확인하고 싶을 때
- QGIS 등 GIS 소프트웨어에 바로 불러올 수 있는 형태로 **내보내고 싶을 때**

---

## 다운로드 및 실행 방법

> GitHub는 대용량 바이너리 파일 업로드가 제한되어 exe 파일을 직접 제공하지 않습니다.  
> 아래 두 가지 방법 중 하나로 사용할 수 있습니다.

### 방법 1 — 직접 빌드 (권장)

Python이 설치된 환경에서 아래 명령어를 순서대로 실행하면 `dist/` 폴더에 `ExifToolArchaeo.exe`가 생성됩니다.

```bash
# 1. 저장소 내려받기
git clone https://github.com/lzpxilfe/exiftool_archaeo.git
cd exiftool_archaeo

# 2. 의존성 설치
pip install pyproj pandas pyinstaller

# 3. exe 빌드
pyinstaller archaeo_gps.spec

# 4. exiftool.exe 복사 (https://exiftool.org 에서 다운로드)
copy exiftool.exe dist\exiftool.exe
```

빌드 후 `dist/` 폴더 안의 두 파일만 있으면 됩니다:

```
dist/
  ├── ExifToolArchaeo.exe   ← 더블클릭으로 실행
  └── exiftool.exe          ← 반드시 같은 폴더에 있어야 함
```

### 방법 2 — Python 스크립트로 직접 실행

exe 없이 Python으로 바로 실행할 수도 있습니다.

```bash
pip install pyproj pandas
python archaeo_gps.py -i ./사진폴더 -o output.csv --crs tm --map map.html
```

---

## 사용 방법

### GUI (더블클릭 실행)

`ExifToolArchaeo.exe`를 더블클릭하면 창이 열립니다.

1. **사진 폴더** — 분석할 사진이 들어있는 폴더를 선택
2. **출력 CSV** — 결과를 저장할 파일 이름 지정
3. **좌표계 변환** — 드롭다운에서 원하는 좌표계 선택
4. **지도 생성** 체크 → 방향 화살표가 표시된 HTML 지도도 함께 저장
5. **추출 실행** 클릭

### CLI (명령줄)

```bash
# 기본 실행
ExifToolArchaeo.exe -i C:\현장사진 -o output.csv

# 좌표계 지정 + 지도 생성
ExifToolArchaeo.exe -i C:\현장사진 -o output.csv --crs tm --map map.html

# 구좌표계 사용 (Bessel 구TM중부원점)
ExifToolArchaeo.exe -i C:\현장사진 -o output.csv --crs tm_old
```

---

## 지원 좌표계

### 한국 현행 (GRS80, 2010년 이후 국가 표준)

| 코드 | EPSG | 이름 | 적용 지역 |
|------|------|------|----------|
| `tm` ⭐ | 5186 | TM중부원점 GRS80 | 한반도 내륙 대부분 |
| `tm_west` | 5185 | TM서부원점 GRS80 | 서해안·서부 |
| `tm_east` | 5187 | TM동부원점 GRS80 | 동해안·영남동부 |
| `tm_eastsea` | 5188 | TM동해원점 GRS80 | 울릉도·독도 |
| `utmk` | 5179 | UTM-K GRS80 | 한반도 통합 |

### 한국 구좌표 (Bessel 1841, 2010년 이전 지형도·문화재 도면)

| 코드 | EPSG | 이름 |
|------|------|------|
| `tm_old` | 5174 | 구TM중부원점 Bessel |
| `tm_old_west` | 5173 | 구TM서부원점 Bessel |
| `tm_old_east` | 5176 | 구TM동부원점 Bessel |
| `tm_mod` | 5181 | 수정중부원점 Bessel (2010 경과조치) |
| `tm_mod_west` | 5182 | 수정서부원점 Bessel |
| `tm_mod_east` | 5183 | 수정동부원점 Bessel |

### 국제 / 기타

| 코드 | EPSG | 이름 |
|------|------|------|
| `utm52n` | 32652 | UTM Zone 52N (WGS84) |
| `web_mercator` | 3857 | Web Mercator (Google·Kakao지도) |
| `wgs84` | 4326 | WGS84 위경도 (변환 없음) |

> EPSG 코드를 직접 입력할 수도 있습니다: `--crs epsg:5186`

---

## 출력 결과

### CSV 컬럼

| 컬럼 | 설명 |
|------|------|
| `FileName` | 파일명 |
| `DateTime` | 촬영 일시 |
| `Lat_DD` / `Lon_DD` | 위경도 (십진수) |
| `Alt_m` | 고도 (m) |
| `CamDirection_deg` | 카메라 방위각 (0–360°) |
| `CamDirection_cardinal` | 16방위 (N/NNE/NE…) |
| `GimbalYaw/Pitch/Roll` | 짐벌 자세 (DJI) |
| `FlightYaw/Pitch/Roll` | 기체 자세 (DJI) |
| `Proj_X` / `Proj_Y` | 지정 좌표계 변환 좌표 |

### HTML 지도

- 위성 사진 배경 (Esri, OSM 전환 가능)
- 각 사진 위치에 방향 화살표 표시
- 클릭하면 방위각·고도·짐벌 정보 팝업

---

## 지원 카메라

| 종류 | 방향 정보 출처 |
|------|--------------|
| DJI 드론 (Mavic·Air·Mini 시리즈) | `GimbalYawDegree` (우선 적용) |
| DSLR / 미러리스 + 외장 GPS | `GPSImgDirection` |
| 스마트폰 | `GPSImgDirection` |

---

## 요구사항

- **Python** 3.10 이상 (스크립트 직접 실행 시)
- **ExifTool** — [exiftool.org](https://exiftool.org) 에서 다운로드
- **의존성** — `pip install pyproj pandas` (또는 `pip install -r requirements.txt`)

---

## 라이선스

이 저장소는 [ExifTool](https://github.com/exiftool/exiftool) (by Phil Harvey)의 포크입니다.  
ExifTool은 Perl 라이선스를 따릅니다.  
커스텀 스크립트(`archaeo_gps.py`, `archaeo_gps_gui.py`)는 MIT 라이선스로 배포됩니다.
