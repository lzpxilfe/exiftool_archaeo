#!/usr/bin/env python3
"""archaeo_gps 순수 로직 단위 테스트.

실행: python3 -m unittest test_archaeo_gps -v
ExifTool 실행 파일이나 실제 사진 없이도 돌아가도록 구성되어 있다.
"""

import json
import tempfile
import unittest
from pathlib import Path

import archaeo_gps as ag


class TestDmsToDd(unittest.TestCase):
    def test_decimal_passthrough(self):
        self.assertAlmostEqual(ag.dms_to_dd("36.371791666"), 36.371791666)

    def test_dms_with_hemisphere(self):
        self.assertAlmostEqual(ag.dms_to_dd("36 deg 22' 14.45\" N"),
                               36 + 22 / 60 + 14.45 / 3600, places=8)

    def test_southern_hemisphere_is_negative(self):
        self.assertLess(ag.dms_to_dd("36 deg 22' 14.45\" S"), 0)

    def test_western_hemisphere_is_negative(self):
        self.assertLess(ag.dms_to_dd("127 deg 30' 0.00\" W"), 0)

    def test_none_and_garbage(self):
        self.assertIsNone(ag.dms_to_dd(None))
        self.assertIsNone(ag.dms_to_dd("not a coordinate"))


class TestAltitude(unittest.TestCase):
    def test_above_sea_level(self):
        self.assertEqual(ag.altitude_to_float("525.2 m Above Sea Level"), 525.2)

    def test_below_sea_level_becomes_negative(self):
        # 해수면 아래 고도가 양수로 나오던 문제에 대한 회귀 테스트
        self.assertEqual(ag.altitude_to_float("12.5 m Below Sea Level"), -12.5)

    def test_ref_flag_marks_below(self):
        self.assertEqual(ag.altitude_to_float("12.5", alt_ref=1), -12.5)
        self.assertEqual(ag.altitude_to_float("12.5", alt_ref=0), 12.5)

    def test_plain_number_and_none(self):
        self.assertEqual(ag.altitude_to_float("100"), 100.0)
        self.assertIsNone(ag.altitude_to_float(None))
        self.assertIsNone(ag.altitude_to_float("no digits"))


class TestCardinal(unittest.TestCase):
    def test_known_bearings(self):
        for deg, expected in [(0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
                              (180, "S"), (225, "SW"), (270, "W"), (315, "NW")]:
            self.assertEqual(ag.deg_to_cardinal(deg), expected, f"{deg}°")

    def test_wraparound(self):
        self.assertEqual(ag.deg_to_cardinal(360), "N")
        self.assertEqual(ag.deg_to_cardinal(-45), "NW")

    def test_none(self):
        self.assertEqual(ag.deg_to_cardinal(None), "")


class TestNormalizeYaw(unittest.TestCase):
    def test_dji_negative_range(self):
        # DJI 는 -180~+180 으로 기록하므로 0~360 으로 정규화해야 한다
        self.assertEqual(ag.normalize_yaw(-90), 270)
        self.assertEqual(ag.normalize_yaw(90), 90)
        self.assertIsNone(ag.normalize_yaw(None))


class TestParseRecord(unittest.TestCase):
    def test_gimbal_yaw_takes_priority(self):
        rec = ag.parse_record({
            "FileName": "DJI_0001.JPG",
            "GPSLatitude": "36.5", "GPSLongitude": "127.5",
            "GimbalYawDegree": "-45", "FlightYawDegree": "100",
            "GPSImgDirection": "200",
        })
        self.assertEqual(rec["CamDirection_deg"], 315.0)
        self.assertEqual(rec["CamDirection_cardinal"], "NW")

    def test_falls_back_to_gps_direction(self):
        rec = ag.parse_record({"FileName": "a.JPG", "GPSImgDirection": "90"})
        self.assertEqual(rec["CamDirection_deg"], 90.0)

    def test_missing_gps_is_none(self):
        rec = ag.parse_record({"FileName": "a.JPG"})
        self.assertIsNone(rec["Lat_DD"])
        self.assertIsNone(rec["CamDirection_deg"])

    def test_hemisphere_ref_applied(self):
        rec = ag.parse_record({"FileName": "a.JPG", "GPSLatitude": "36.5",
                               "GPSLatitudeRef": "S", "GPSLongitude": "127.5",
                               "GPSLongitudeRef": "W"})
        self.assertLess(rec["Lat_DD"], 0)
        self.assertLess(rec["Lon_DD"], 0)


class TestCollectImages(unittest.TestCase):
    def test_case_insensitive_and_recursive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            for name in ("a.JPG", "b.jpg", "c.DNG", "ignore.txt"):
                (root / name).touch()
            (root / "sub" / "deep.jpg").touch()

            flat = ag.collect_images(str(root))
            self.assertEqual(len(flat), 3, "대소문자 무관하게 3장만 잡혀야 한다")
            self.assertFalse(any("ignore.txt" in f for f in flat))

            deep = ag.collect_images(str(root), recursive=True)
            self.assertEqual(len(deep), 4)
            self.assertTrue(any("deep.jpg" in f for f in deep))

    def test_single_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "one.jpg"
            f.touch()
            self.assertEqual(ag.collect_images(str(f)), [str(f.resolve())])

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            ag.collect_images("/definitely/not/here")


class TestFindExiftool(unittest.TestCase):
    def test_env_var_is_honoured(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "exiftool_files"
            fake.mkdir()
            exe = Path(d) / "exiftool"
            exe.write_text("#!/bin/sh\necho 13.10\n")
            exe.chmod(0o755)
            old = os.environ.get(ag.ENV_EXIFTOOL_PATH)
            os.environ[ag.ENV_EXIFTOOL_PATH] = str(exe)
            try:
                self.assertEqual(ag.find_exiftool(), str(exe.resolve()))
            finally:
                if old is None:
                    os.environ.pop(ag.ENV_EXIFTOOL_PATH, None)
                else:
                    os.environ[ag.ENV_EXIFTOOL_PATH] = old

    def test_no_personal_paths_hardcoded(self):
        # 특정 PC 의 사용자 폴더가 소스에 남아 있으면 안 된다
        src = Path(ag.__file__).read_text(encoding="utf-8")
        for leaked in ("nuri9", "C:\\\\Users\\\\", "D:\\\\ExifTool", "G:\\\\ExifTool"):
            self.assertNotIn(leaked, src, f"하드코딩된 경로 발견: {leaked}")


class TestOutputs(unittest.TestCase):
    SAMPLE = [{
        "FileName": "DJI_0001.JPG", "SourceFile": "/tmp/DJI_0001.JPG",
        "DateTime": "2026:01:01 10:00:00", "Make": "DJI", "Model": "M3E",
        "Lat_DD": 36.371791, "Lon_DD": 127.123456, "Alt_m": 525.2,
        "CamDirection_deg": 135.0, "CamDirection_cardinal": "SE",
        "GimbalPitch": -30.0, "GimbalYaw": 135.0, "GimbalRoll": 0.0,
        "FlightYaw": 130.0,
    }]

    def test_geojson_structure(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self.assertTrue(ag.write_geojson(self.SAMPLE, str(out)))
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["type"], "FeatureCollection")
            # GeoJSON 은 [경도, 위도] 순서다
            self.assertEqual(data["features"][0]["geometry"]["coordinates"],
                             [127.123456, 36.371791])

    def test_geojson_skips_records_without_gps(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self.assertFalse(ag.write_geojson([{"FileName": "x", "Lat_DD": None,
                                                "Lon_DD": None}], str(out)))

    def test_map_escapes_filenames(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.html"
            recs = [dict(self.SAMPLE[0], FileName="<script>alert(1)</script>.jpg",
                         SourceFile="")]
            self.assertTrue(ag.write_map(recs, str(out), thumb_size=0))
            html = out.read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>.jpg", html)
            self.assertIn("&lt;script&gt;", html)

    def test_map_returns_false_without_gps(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "m.html"
            self.assertFalse(ag.write_map([{"FileName": "x", "Lat_DD": None,
                                            "Lon_DD": None}], str(out)))
            self.assertFalse(out.exists())

    def test_csv_written_without_pandas_path(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.csv"
            ag.write_csv(self.SAMPLE, str(out))
            text = out.read_text(encoding="utf-8-sig")
            self.assertIn("DJI_0001.JPG", text)
            self.assertIn("FileName", text.splitlines()[0])


class TestCrsPresets(unittest.TestCase):
    def test_all_presets_look_like_epsg(self):
        for key, value in ag.CRS_PRESETS.items():
            self.assertRegex(value, r"^epsg:\d+$", f"{key} → {value}")

    def test_default_is_tm_central(self):
        self.assertEqual(ag.CRS_PRESETS["tm"], "epsg:5186")


if __name__ == "__main__":
    unittest.main(verbosity=2)
