"""GPS/direction/export/batch regressions; no camera or ExifTool binary needed."""
import csv
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import archaeo_gps as ag


def record(**updates):
    raw = {"FileName": "DSC_0001.JPG", "Make": "Nikon", "Model": "D5600",
           "GPSLatitude": 36.5, "GPSLongitude": 127.5}
    raw.update(updates)
    return ag.parse_record(raw)


def map_data(records):
    with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
        out = Path(temp) / "map.html"
        if not ag.write_map(records, str(out), thumb_size=0):
            return [], ""
        text = out.read_text(encoding="utf-8")
    data = json.loads(re.search(r"const features = (.*);", text).group(1))
    return data, text


class TestCoordinateQuality(unittest.TestCase):
    def test_invalid_numbers_do_not_crash(self):
        for value in (float("nan"), float("inf"), -float("inf"), True, "NaN", "1e9999"):
            with self.subTest(value=value):
                self.assertIsNone(ag._to_float(value))
                self.assertIsNone(ag.dms_to_dd(value))
                self.assertIsNone(ag.normalize_yaw(value))
                self.assertEqual(ag.deg_to_cardinal(value), "")
                self.assertIsNone(record(GimbalYawDegree=value)["CamDirection_deg"])

    def test_out_of_range_pairs_are_excluded_and_raw_preserved(self):
        for lat, lon in ((91, 127), (-91, 127), (36, 181), (36, -181), (math.inf, 127)):
            with self.subTest(lat=lat, lon=lon):
                r = record(GPSLatitude=lat, GPSLongitude=lon)
                self.assertIsNone(r["Lat_DD"])
                self.assertIsNone(r["Lon_DD"])
                self.assertEqual(r["GPSValidation"], "invalid")
                self.assertEqual(r["GPSLatitude_raw"], str(lat))
                self.assertTrue(r["GPSWarnings"])

    def test_boundary_and_zero_coordinates_are_valid(self):
        for lat, lon in ((90, 180), (-90, -180), (0, 127), (36, 0), (0, 0)):
            with self.subTest(lat=lat, lon=lon):
                r = record(GPSLatitude=lat, GPSLongitude=lon)
                self.assertEqual(r["GPSValidation"], "valid")
                self.assertEqual((r["Lat_DD"], r["Lon_DD"]), (lat, lon))
        self.assertIn("zero_coordinate_pair", record(GPSLatitude=0, GPSLongitude=0)["GPSWarnings"])

    def test_missing_and_incomplete_are_different(self):
        self.assertEqual(ag.parse_record({})["GPSValidation"], "missing")
        for raw in ({"GPSLatitude": 36}, {"GPSLongitude": 127}, {"GPSLatitude": ""}):
            r = ag.parse_record(raw)
            self.assertIsNone(r["Lat_DD"])
            self.assertIsNone(r["Lon_DD"])
        self.assertEqual(ag.parse_record({"GPSLatitude": 36})["GPSValidation"], "incomplete")

    def test_invalid_dms_does_not_partially_parse(self):
        for text in ('36 deg 60\' 0" N', '36 deg 0\' 60" N', '36 deg 1\' 1..2" N',
                     'prefix 36 deg 1\' 2" N', '36 deg 1\' 2" N junk', '-36 N'):
            with self.subTest(text=text):
                self.assertIsNone(ag.dms_to_dd(text))

    def test_scientific_notation_and_degree_symbol(self):
        self.assertEqual(ag.dms_to_dd("1.25e-3"), 0.00125)
        self.assertAlmostEqual(ag.dms_to_dd('36° 30\' 0" N'), 36.5)
        self.assertEqual(ag.dms_to_dd('-0 deg 30\' 0"'), -0.5)

    def test_hemisphere_conflicts_are_not_silently_reversed(self):
        for value, ref in (("36 N", "South"), ("-36", "N"), ("36 E", "N"),
                           ("36", "West"), ("36", "nonsense")):
            with self.subTest(value=value, ref=ref):
                self.assertIsNone(record(GPSLatitude=value, GPSLatitudeRef=ref)["Lat_DD"])
        self.assertEqual(record(GPSLatitude="-36", GPSLatitudeRef="South")["Lat_DD"], -36)

    def test_no_early_eight_decimal_rounding(self):
        latitude = 36.123456789123
        self.assertEqual(record(GPSLatitude=latitude)["Lat_DD"], latitude)

    def test_measurement_void_is_not_accepted(self):
        for status in ("V", "Measurement Void"):
            r = record(GPSStatus=status)
            self.assertEqual(r["GPSValidation"], "invalid")
            self.assertIsNone(r["Lat_DD"])
        self.assertEqual(record(GPSStatus="Measurement Active")["GPSValidation"], "valid")

    def test_gps_quality_metadata_does_not_invent_device_accuracy(self):
        r = record(GPSHPositioningError="4.25 m", GPSDOP="1.2", GPSStatus="A",
                   GPSProcessingMethod="GPS", GPSDateStamp="2026:10:20",
                   GPSTimeStamp="01:00:00", OffsetTimeOriginal="+09:00")
        self.assertEqual(r["GPSHPositioningError_m"], 4.25)
        self.assertEqual(r["GPSDOP"], 1.2)
        self.assertEqual(r["GPSPositionSource"], "EXIF")
        self.assertEqual(r["OffsetTimeOriginal"], "+09:00")
        self.assertIsNone(record()["GPSHPositioningError_m"])
        self.assertIsNone(record(GPSHPositioningError=-1)["GPSHPositioningError_m"])

    def test_finite_altitude_only(self):
        self.assertEqual(ag.altitude_to_float("1.2e2 m"), 120)
        for value in ("inf", "nan", True, "junk 12 meters", "1e9999 m"):
            self.assertIsNone(ag.altitude_to_float(value))

    def test_cardinal_midpoints_use_consistent_clockwise_rounding(self):
        self.assertEqual(ag.deg_to_cardinal(11.25), "NNE")
        self.assertEqual(ag.deg_to_cardinal(348.75), "N")


class TestDirectionQuality(unittest.TestCase):
    def test_flight_heading_is_not_camera_direction(self):
        r = record(Make="DJI", FlightYawDegree=90)
        self.assertEqual(r["FlightYaw"], 90)
        self.assertIsNone(r["CamDirection_deg"])
        self.assertIn("flight_heading", r["CamDirectionWarnings"])
        features, _ = map_data([r])
        self.assertFalse(features[0]["has_dir"])

    def test_image_direction_beats_flight_heading(self):
        r = record(FlightYawDegree=180, GPSImgDirection=90, GPSImgDirectionRef="T")
        self.assertEqual(r["CamDirection_deg"], 90)
        self.assertEqual(r["CamDirectionSource"], "GPSImgDirection")
        self.assertEqual(r["CamDirectionReference"], "T")

    def test_gimbal_source_is_preserved_without_inventing_north_reference(self):
        r = record(GimbalYawDegree=-45, GPSImgDirection=90, GPSImgDirectionRef="T")
        self.assertEqual(r["CamDirection_deg"], 315)
        self.assertEqual(r["CamDirectionSource"], "GimbalYawDegree")
        self.assertEqual(r["CamDirectionReference"], "U")
        self.assertIn("unverified", r["CamDirectionWarnings"])

    def test_invalid_gimbal_can_fall_back_to_image_direction(self):
        r = record(GimbalYawDegree="NaN", GPSImgDirection=0, GPSImgDirectionRef="True North")
        self.assertEqual(r["CamDirection_deg"], 0)
        self.assertEqual(r["CamDirectionSource"], "GPSImgDirection")

    def test_magnetic_direction_is_preserved_but_not_mapped_as_true_north(self):
        for ref in ("M", "Magnetic North"):
            r = record(GPSImgDirection=45, GPSImgDirectionRef=ref)
            self.assertEqual(r["CamDirection_deg"], 45)
            self.assertEqual(r["CamDirectionReference"], "M")
            features, _ = map_data([r])
            self.assertFalse(features[0]["direction_available"])
            self.assertFalse(features[0]["has_dir"])

    def test_invalid_image_direction_is_not_normalized_into_a_fake_bearing(self):
        for direction in (-1, 361, "Infinity"):
            self.assertIsNone(record(GPSImgDirection=direction)["CamDirection_deg"])
        self.assertEqual(record(GPSImgDirection=360)["CamDirection_deg"], 0)

    def test_d5600_location_without_direction_stays_directionless(self):
        r = record()
        self.assertIsNone(r["CamDirection_deg"])
        features, _ = map_data([r])
        self.assertFalse(features[0]["direction_available"])

    def test_dslr_default_remains_dot_and_map_has_opt_in(self):
        features, text = map_data([record(GPSImgDirection=90, GPSImgDirectionRef="T")])
        self.assertFalse(features[0]["has_dir"])
        self.assertTrue(features[0]["direction_available"])
        self.assertIn('id="dslr-direction-toggle"', text)
        self.assertIn('let showDslrDirections = false', text)


class TestSafeExports(unittest.TestCase):
    def test_geojson_filters_invalid_geometry_and_sanitizes_properties(self):
        records = [record(), dict(record(), Lat_DD=math.inf),
                   dict(record(), Lon_DD=181), dict(record(), Lat_DD=True),
                   dict(record(), Alt_m=math.nan, custom={"nested": [math.inf]})]
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            out = Path(temp) / "result.geojson"
            self.assertTrue(ag.write_geojson(records, str(out)))
            data = json.loads(out.read_text(), parse_constant=lambda s: self.fail(s))
        self.assertEqual(len(data["features"]), 2)
        self.assertIsNone(data["features"][1]["properties"]["Alt_m"])
        self.assertEqual(data["features"][1]["properties"]["custom"], {"nested": [None]})

    def test_map_filters_invalid_coordinates_without_crashing(self):
        features, _ = map_data([dict(record(), Lat_DD=math.nan), dict(record(), Lon_DD=-181), record()])
        self.assertEqual(len(features), 1)

    def test_folder_name_cannot_inject_script_or_checkbox_attributes(self):
        hostile = '\"><img src=x onerror=alert(1)></script><script>alert(2)</script>'
        features, text = map_data([dict(record(), Folder=hostile)])
        self.assertNotIn(hostile, text)
        self.assertEqual(features[0]["group_key"], "group-0")
        class Scripts(HTMLParser):
            count = 0
            def handle_starttag(self, tag, attrs):
                if tag == "script":
                    self.count += 1
        parser = Scripts()
        parser.feed(text)
        self.assertEqual(parser.count, 2)  # Leaflet plus one inline script.

    def test_script_json_roundtrips_special_characters(self):
        value = {"value": "</script>\u2028\u2029&한글"}
        encoded = ag._script_json(value)
        self.assertNotIn("</script>", encoded)
        self.assertEqual(json.loads(encoded), value)

    def test_projection_rejects_non_finite_output(self):
        transformer = SimpleNamespace(transform=lambda *args, **kwargs: (math.inf, math.inf))
        self.assertEqual(ag.transform_coords(transformer, 36, 127), (None, None))
        self.assertEqual(ag.transform_coords(transformer, 91, 127), (None, None))

    @unittest.skipUnless(ag.HAS_PYPROJ, "pyproj unavailable")
    def test_real_pyproj_roundtrip(self):
        from pyproj import Transformer
        forward = ag.build_transformers("epsg:5186")
        x, y = ag.transform_coords(forward, 36.5, 127.5)
        self.assertTrue(math.isfinite(x) and math.isfinite(y))
        reverse = Transformer.from_crs("epsg:5186", "epsg:4326", always_xy=True)
        lon, lat = reverse.transform(x, y, errcheck=True)
        self.assertAlmostEqual(lat, 36.5, places=8)
        self.assertAlmostEqual(lon, 127.5, places=8)

    def test_csv_retains_invalid_photo_and_failure_reason(self):
        r = record(GPSLatitude=91, Error="bad metadata")
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            out = Path(temp) / "new" / "result.csv"
            ag.write_csv([r], str(out))
            rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["GPSValidation"], "invalid")
        self.assertEqual(rows[0]["GPSLatitude_raw"], "91")
        self.assertEqual(rows[0]["Lat_DD"], "")
        self.assertEqual(rows[0]["ExifToolError"], "bad metadata")

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_generated_javascript_parses_and_dslr_toggle_controls_icon(self):
        _, text = map_data([record(GPSImgDirection=90, GPSImgDirectionRef="T")])
        js = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
        start = js.index("    function renderPointShape(")
        end = js.index("    // ── 데이터 연동", start)
        factory = js[start:end]
        harness = "const assert = require('node:assert/strict'); const L = {divIcon: x => x};\n"
        harness += factory + "\n"
        harness += '''
const f = {cam_type:'dslr', color:'#123456', shape:'circle', yaw:90,
           has_dir:false, direction_available:true, direction_reference:'T'};
assert(!makeCustomIcon(f).html.includes('rotate('));
showDslrDirections = true;
assert(makeCustomIcon(f).html.includes('rotate(90,14,14)'));
assert(!makeCustomIcon({...f,direction_available:false}).html.includes('rotate('));
assert(makeCustomIcon({...f,direction_reference:'U'}).html.includes('stroke-dasharray="2 2"'));
'''
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "map.js"
            script.write_text(js, encoding="utf-8")
            subprocess.run([shutil.which("node"), "--check", str(script)], check=True, capture_output=True, timeout=10)
            script.write_text(harness, encoding="utf-8")
            subprocess.run([shutil.which("node"), str(script)], check=True, capture_output=True, timeout=10)


class TestBoundedExiftool(unittest.TestCase):
    def test_empty_input_does_not_start_process(self):
        with patch.object(ag.subprocess, "run") as process:
            self.assertEqual(ag.run_exiftool("missing", []), ([], []))
        process.assert_not_called()

    def test_invalid_batch_sizes_fail_early(self):
        for size in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                ag.run_exiftool("unused", [], batch_size=size)

    def test_batch_failure_preserves_successes_and_continues(self):
        responses = [([{"FileName": "1"}], []), ([], ["timed out"]), ([{"FileName": "5"}], [])]
        progress = []
        with patch.object(ag, "_run_exiftool_batch", side_effect=responses) as batch:
            records, warnings = ag.run_exiftool("tool", list("12345"), batch_size=2,
                                               progress=lambda done, total: progress.append((done, total)))
        self.assertEqual([len(c.args[1]) for c in batch.call_args_list], [2, 2, 1])
        self.assertEqual([r["FileName"] for r in records], ["1", "5"])
        self.assertIn("배치 2", warnings[0])
        self.assertEqual(progress, [(2, 5), (4, 5), (5, 5)])

    def test_argument_file_blocks_newline_option_injection(self):
        with patch.object(ag, "_run_exiftool_batch", return_value=([], [])) as batch:
            _, errors = ag.run_exiftool("tool", ["normal.jpg", "photo\n-overwrite_original\n.jpg"])
        self.assertEqual(batch.call_args.args[1], ["normal.jpg"])
        self.assertEqual(len(errors), 1)

    def test_unicode_argfile_warning_records_and_cleanup(self):
        argfiles = []
        def fake_run(cmd, **kwargs):
            argfile = Path(cmd[cmd.index("-@") + 1])
            argfiles.append(argfile)
            text = argfile.read_text(encoding="utf-8")
            self.assertIn("한글 공백.JPG", text)
            self.assertIn("-GPSHPositioningError", text)
            return SimpleNamespace(returncode=0, stderr="minor warning",
                                   stdout=json.dumps([{"SourceFile": "한글 공백.JPG", "Warning": "tag warning"},
                                                      {"Error": "corrupt file"}, "malformed"]))
        with patch.object(ag.subprocess, "run", side_effect=fake_run):
            records, errors = ag.run_exiftool("tool", ["한글 공백.JPG"])
        self.assertEqual(len(records), 2)
        self.assertEqual(len(errors), 4)
        self.assertTrue(Path(records[0]["SourceFile"]).is_absolute())
        self.assertTrue(all(not path.exists() for path in argfiles))

    def test_timeout_removes_argument_file(self):
        paths = []
        def fake_run(cmd, **kwargs):
            paths.append(Path(cmd[cmd.index("-@") + 1]))
            raise subprocess.TimeoutExpired(cmd, 1)
        with patch.object(ag.subprocess, "run", side_effect=fake_run):
            records, errors = ag.run_exiftool("tool", ["photo.jpg"])
        self.assertEqual(records, [])
        self.assertTrue(errors)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_malformed_json_is_reported(self):
        response = SimpleNamespace(returncode=0, stdout="not JSON", stderr="")
        with patch.object(ag.subprocess, "run", return_value=response):
            records, errors = ag.run_exiftool("tool", ["photo.jpg"])
        self.assertEqual(records, [])
        self.assertIn("JSON", errors[0])


if __name__ == "__main__":
    unittest.main()
