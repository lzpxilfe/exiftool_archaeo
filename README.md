# ExifTool Archaeo

> **고고학 현장 사진 GPS·촬영방향 추출 도구**  
> Archaeological field photo GPS & orientation extractor (DJI drone / DSLR)

드론·DSLR 사진의 EXIF에서 GPS 좌표와 촬영 방향(Yaw/Pitch/Roll)을 추출하고,  
한국 좌표계(TM중부원점, UTM-K 등)로 변환하여 CSV와 Leaflet 지도로 출력합니다.

---

## 배경

고고학 발굴 현장, 특히 우거진 산림 지형에서 드론 사진만으로는  
**어느 방향을 바라보며 찍었는지** 파악하기가 매우 어렵습니다.  
이 도구는 DJI 드론 EXIF에 내장된 `GimbalYawDegree` / `FlightYawDegree` 값을  
파싱하여 방위각과 나침반 방향(N/NE/E…)을 함께 출력합니다.

---

## 기능

| 기능 | 설명 |
|------|------|
| EXIF 일괄 추출 | 폴더 내 JPG/DNG/ARW 등 전체 처리 |
| 방향 파싱 | DJI: `GimbalYawDegree` / DSLR: `GPSImgDirection` 자동 선택 |
| 좌표 변환 | WGS84 → TM중부원점(5186), UTM-K(5179), UTM 52N(32652) 등 |
| CSV 출력 | 위경도(DD), 고도, Yaw/Pitch/Roll, 변환 좌표(X/Y), 촬영시각 |
| HTML 지도 | Leaflet.js 기반 방향 화살표 지도 (위성+OSM) |

---

## 설치

```bash
# Python 3.10+ 필요
pip install -r requirements.txt
```

ExifTool은 별도 설치 필요:
- Windows: [exiftool.org](https://exiftool.org) 에서 `exiftool.exe` 다운로드
- macOS: `brew install exiftool`
- Linux: `apt install libimage-exiftool-perl`

---

## 사용법

```bash
# 기본 (TM중부원점 EPSG:5186 변환 + CSV)
python archaeo_gps.py -i ./photos -o output.csv

# 좌표계 지정 + HTML 지도 함께 생성
python archaeo_gps.py -i ./photos -o output.csv --crs tm --map map.html

# exiftool 경로 직접 지정 (Windows)
python archaeo_gps.py -i ./photos -o output.csv --exiftool ./exiftool.exe

# 좌표 변환 없이 WGS84만
python archaeo_gps.py -i ./photos -o output.csv --no-transform
```

### 지원 좌표계 (`--crs`)

| 코드 | EPSG | 설명 |
|------|------|------|
| `tm` | 5186 | **TM중부원점 GRS80** (한국 국가 기준, 기본값) |
| `utmk` | 5179 | UTM-K GRS80 |
| `utm52n` | 32652 | UTM Zone 52N |
| `wgs84` | 4326 | WGS84 (변환 없음) |
| 직접 입력 | — | `epsg:5186`, `epsg:32652` 등 |

---

## 출력 CSV 컬럼

```
FileName, DateTime, Make, Model,
Lat_DD, Lon_DD, Alt_m,
CamDirection_deg, CamDirection_cardinal,
GimbalYaw, GimbalPitch, GimbalRoll,
FlightYaw, FlightPitch, FlightRoll,
GPSImgDirection, GPSImgDirectionRef,
Proj_X, Proj_Y, Proj_CRS,
SourceFile
```

- `CamDirection_deg`: 카메라가 향한 방위각 (0–360°, 북=0°)
- `CamDirection_cardinal`: 16방위 표기 (N/NNE/NE/ENE…)
- `Proj_X`, `Proj_Y`: 지정 좌표계의 변환 좌표

---

## 추출되는 EXIF 태그 (DJI 기준)

```
GimbalYawDegree    카메라(짐벌) 방위각  ← 촬영방향 판단에 가장 중요
GimbalPitchDegree  짐벌 피치 (수직 기울기)
GimbalRollDegree   짐벌 롤
FlightYawDegree    드론 기체 방위각
FlightPitchDegree  기체 피치
FlightRollDegree   기체 롤
GPSLatitude / GPSLongitude / GPSAltitude
```

---

## 지원 카메라

| 종류 | 방향 태그 |
|------|----------|
| DJI 드론 (Mavic, Air, Mini 등) | `GimbalYawDegree` (우선) |
| DSLR/미러리스 + 외장 GPS | `GPSImgDirection` |
| 스마트폰 | `GPSImgDirection` |

---

## 라이선스

This project is a fork of [ExifTool](https://github.com/exiftool/exiftool) by Phil Harvey.  
ExifTool is distributed under the same terms as Perl itself.

Custom scripts (`archaeo_gps.py`) are released under the MIT License.
