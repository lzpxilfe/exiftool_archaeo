"""Packaging regressions. No network, real Windows binaries or GUI required."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

import build_windows as bw
import archaeo_launcher as launcher


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, entries=None):
        path = self.root / "fixture.zip"
        with zipfile.ZipFile(path, "w") as out:
            for name, data in (entries or {
                "package/exiftool(-k).exe": b"MZfixture",
                "package/exiftool_files/perl532.dll": b"runtime",
                "package/exiftool_files/lib/Image/ExifTool.pm": b"perl",
                "package/license.txt": b"original license",
            }).items():
                out.writestr(name, data)
        return path

    def engine(self, root):
        engine = root / "exiftool.exe"
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_bytes(b"MZfixture")
        (root / "exiftool_files").mkdir()
        (root / "exiftool_files" / "perl532.dll").write_bytes(b"runtime")
        return engine


class TestDownload(TemporaryTest):
    def response(self, data, url="https://downloads.example.test/exiftool.zip"):
        response = io.BytesIO(data)
        response.geturl = lambda: url
        return response

    def test_verified_cache_does_not_use_network(self):
        cached = self.root / bw.EXIFTOOL_ARCHIVE
        cached.write_bytes(b"verified")
        with patch.object(bw, "EXIFTOOL_SHA256", bw.sha256_file(cached)), patch.object(bw, "urlopen") as network:
            self.assertEqual(bw.download_archive(self.root), cached)
            network.assert_not_called()

    def test_corrupt_cache_is_not_executed_or_silently_replaced(self):
        cached = self.root / bw.EXIFTOOL_ARCHIVE
        cached.write_bytes(b"corrupt")
        with patch.object(bw, "urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                bw.download_archive(self.root)
            network.assert_not_called()
        self.assertEqual(cached.read_bytes(), b"corrupt")

    def test_download_verified_before_cache_publication(self):
        content = b"official archive fixture"
        with patch.object(bw, "EXIFTOOL_SHA256", hashlib.sha256(content).hexdigest()), \
                patch.object(bw, "urlopen", return_value=self.response(content)):
            result = bw.download_archive(self.root)
        self.assertEqual(result.read_bytes(), content)
        self.assertFalse(list(self.root.glob("*.part")))

    def test_bad_hash_never_publishes_archive(self):
        with patch.object(bw, "urlopen", side_effect=lambda *a, **k: self.response(b"<html>Error</html>")):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                bw.download_archive(self.root)
        self.assertFalse((self.root / bw.EXIFTOOL_ARCHIVE).exists())
        self.assertFalse(list(self.root.glob("*.part")))

    def test_http_redirect_rejected(self):
        with patch.object(bw, "urlopen", side_effect=lambda *a, **k: self.response(b"x", "http://example.test/x")):
            with self.assertRaisesRegex(RuntimeError, "non-HTTPS"):
                bw.download_archive(self.root)

    def test_download_size_limit(self):
        with patch.object(bw, "MAX_DOWNLOAD", 2), \
                patch.object(bw, "urlopen", side_effect=lambda *a, **k: self.response(b"abc")):
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                bw.download_archive(self.root)

    def test_network_error_falls_back_to_second_official_url(self):
        content = b"verified"
        with patch.object(bw, "EXIFTOOL_SHA256", hashlib.sha256(content).hexdigest()), \
                patch.object(bw, "urlopen", side_effect=[OSError("first unavailable"), self.response(content)]) as network:
            self.assertEqual(bw.download_archive(self.root).read_bytes(), content)
        self.assertEqual(network.call_count, 2)


class TestArchive(TemporaryTest):
    def test_traversal_and_windows_paths_are_rejected(self):
        for name in ("../outside.txt", "/absolute.txt", "C:/evil", "C:evil", "a\\..\\evil", "file:stream"):
            with self.subTest(name=name):
                path = self.archive({name: b"x"})
                with self.assertRaises(ValueError):
                    bw.safe_extract(path, self.root / "out")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_symlink_rejected(self):
        archive = self.root / "links.zip"
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr(info, "../outside")
        with self.assertRaises(ValueError):
            bw.safe_extract(archive, self.root / "out")

    def test_unpacked_size_limit(self):
        archive = self.archive()
        with patch.object(bw, "MAX_UNPACKED", 2):
            with self.assertRaisesRegex(ValueError, "limits"):
                bw.safe_extract(archive, self.root / "out")

    def test_nested_unicode_and_spaces_are_supported(self):
        archive = self.archive({"사진 도구/문서.txt": "안내".encode()})
        bw.safe_extract(archive, self.root / "out")
        self.assertEqual((self.root / "out" / "사진 도구" / "문서.txt").read_text(), "안내")

    def test_prepare_renames_launcher_and_preserves_runtime_and_license(self):
        archive = self.archive()
        dest = self.root / "runtime"
        with patch.object(bw, "EXIFTOOL_SHA256", bw.sha256_file(archive)):
            engine = bw.prepare_engine(archive, dest)
        self.assertEqual(engine.name, "exiftool.exe")
        self.assertEqual((dest / "license.txt").read_bytes(), b"original license")
        self.assertTrue((dest / "exiftool_files" / "lib" / "Image" / "ExifTool.pm").is_file())
        self.assertFalse((dest / "exiftool(-k).exe").exists())

    def test_prepare_accepts_archive_without_enclosing_folder(self):
        archive = self.archive({"exiftool.exe": b"MZfixture", "exiftool_files/perl532.dll": b"dll"})
        with patch.object(bw, "EXIFTOOL_SHA256", bw.sha256_file(archive)):
            self.assertTrue(bw.prepare_engine(archive, self.root / "runtime").is_file())

    def test_incomplete_archive_does_not_replace_old_runtime(self):
        archive = self.archive({"package/exiftool(-k).exe": b"MZfixture"})
        dest = self.root / "runtime"
        previous = self.engine(dest)
        with patch.object(bw, "EXIFTOOL_SHA256", bw.sha256_file(archive)):
            with self.assertRaises(ValueError):
                bw.prepare_engine(archive, dest)
        self.assertEqual(previous.read_bytes(), b"MZfixture")

    def test_prepare_checks_hash_before_extraction(self):
        archive = self.archive()
        with patch.object(bw, "safe_extract") as extract:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                bw.prepare_engine(archive, self.root / "runtime")
            extract.assert_not_called()

    def test_two_launchers_are_rejected(self):
        archive = self.archive({"a/exiftool.exe": b"MZa", "b/exiftool.exe": b"MZb"})
        with patch.object(bw, "EXIFTOOL_SHA256", bw.sha256_file(archive)):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                bw.prepare_engine(archive, self.root / "runtime")

    def test_missing_perl_dll_is_rejected(self):
        dest = self.root / "runtime"
        self.engine(dest)
        (dest / "exiftool_files" / "perl532.dll").unlink()
        with self.assertRaisesRegex(ValueError, "DLL"):
            bw.validate_engine_layout(dest)

    def test_non_windows_executable_is_rejected(self):
        dest = self.root / "runtime"
        self.engine(dest).write_bytes(b"not an exe")
        with self.assertRaisesRegex(ValueError, "Windows"):
            bw.validate_engine_layout(dest)


class TestLauncher(TemporaryTest):
    def frozen(self):
        return patch.multiple(sys, frozen=True, _MEIPASS=str(self.root), create=True)

    def test_source_mode_does_not_change_engine_settings(self):
        with patch.object(sys, "frozen", False, create=True), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(launcher.configure_runtime())
            self.assertNotIn("EXIFTOOL_PATH", os.environ)

    def test_frozen_mode_uses_bundled_engine_by_default(self):
        engine = self.engine(self.root / "vendor" / "exiftool")
        with self.frozen(), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(launcher.configure_runtime(), engine)
            self.assertEqual(os.environ["EXIFTOOL_PATH"], str(engine))

    def test_explicit_override_is_preserved(self):
        self.engine(self.root / "vendor" / "exiftool")
        with self.frozen(), patch.dict(os.environ, {"EXIFTOOL_PATH": "user-engine"}):
            launcher.configure_runtime()
            self.assertEqual(os.environ["EXIFTOOL_PATH"], "user-engine")

    def test_blank_override_uses_bundle(self):
        engine = self.engine(self.root / "vendor" / "exiftool")
        with self.frozen(), patch.dict(os.environ, {"EXIFTOOL_PATH": "  "}):
            launcher.configure_runtime()
            self.assertEqual(os.environ["EXIFTOOL_PATH"], str(engine))

    def test_incomplete_bundle_is_not_silently_accepted(self):
        with self.frozen():
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                launcher.configure_runtime()

    def test_windowed_streams_are_safe_for_flush(self):
        self.engine(self.root / "vendor" / "exiftool")
        with self.frozen(), patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            launcher.configure_runtime()
            streams = (sys.stdout, sys.stderr)
            try:
                print("progress", flush=True)
                print("error", file=sys.stderr, flush=True)
            finally:
                for stream in streams:
                    stream.close()

    def test_gui_and_cli_dispatch(self):
        gui = types.ModuleType("archaeo_gps_gui")
        gui.run_gui, gui.run_cli = Mock(), Mock()
        with patch.dict(sys.modules, {"archaeo_gps_gui": gui}), patch.object(launcher, "configure_runtime"):
            with patch.object(sys, "argv", ["app.exe"]):
                self.assertEqual(launcher.main(), 0)
                gui.run_gui.assert_called_once()
                gui.run_cli.assert_not_called()
            with patch.object(sys, "argv", ["app.exe", "--version"]):
                self.assertEqual(launcher.main(), 0)
                gui.run_cli.assert_called_once()

    def test_self_test_dispatch_does_not_open_gui(self):
        with patch.object(sys, "argv", ["app.exe", "--self-test", "result.json"]), \
                patch.object(launcher, "configure_runtime", return_value=Path("engine")), \
                patch.object(launcher, "self_test", return_value=0) as check:
            self.assertEqual(launcher.main(), 0)
            check.assert_called_once_with(Path("result.json"), Path("engine"))


class TestPortableCheck(TemporaryTest):
    def call_check(self, callback):
        exe = self.root / "ExifToolArchaeo.exe"
        exe.write_bytes(b"MZfixture")
        report = self.root / "reports" / "self-test.json"
        with patch.dict(os.environ, {"SystemRoot": str(self.root / "Windows"), "EXIFTOOL_PATH": "external", "PYTHONPATH": "outside"}), \
                patch.object(bw.subprocess, "run", side_effect=callback):
            bw.test_portable_exe(exe, report)
        return report

    def test_runs_only_relocated_exe_with_sanitized_environment(self):
        def execute(command, **kwargs):
            cwd = Path(kwargs["cwd"])
            self.assertIn("한글", str(cwd))
            self.assertEqual([p.name for p in cwd.iterdir()], ["ExifToolArchaeo.exe"])
            self.assertNotIn("EXIFTOOL_PATH", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertEqual(command[1], "--self-test")
            Path(command[2]).write_text(json.dumps({"ok": True, "exiftool_version": bw.EXIFTOOL_VERSION}))
        report = self.call_check(execute)
        self.assertTrue(json.loads(report.read_text())["ok"])

    def test_success_exit_without_report_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "did not write"):
            self.call_check(lambda *args, **kwargs: None)

    def test_wrong_embedded_version_is_failure(self):
        def execute(command, **kwargs):
            Path(command[2]).write_text('{"ok":true,"exiftool_version":"wrong"}')
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.call_check(execute)

    def test_failed_process_preserves_error_report(self):
        def execute(command, **kwargs):
            Path(command[2]).write_text('{"ok":false,"error":"diagnostic"}')
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            self.call_check(execute)
        self.assertIn("diagnostic", (self.root / "reports" / "self-test.json").read_text())


class TestBuildConfiguration(TemporaryTest):
    def test_bat_is_ascii_crlf_and_stops_on_errors(self):
        data = (bw.ROOT / "build_windows.bat").read_bytes()
        data.decode("ascii")
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"DisableDelayedExpansion", data)
        self.assertIn(b"if errorlevel 1 goto failed", data)
        self.assertIn(b"--no-pause", data)

    def test_spec_uses_modern_api_and_keeps_perl_files_together(self):
        engine = self.root / "runtime"
        self.engine(engine)
        fake_proj = types.ModuleType("pyproj")
        fake_proj.datadir = Mock(get_data_dir=Mock(return_value="proj-data"))
        fake_tree = types.ModuleType("PyInstaller.building.datastruct")
        fake_tree.Tree = Mock(return_value=[("vendor/exiftool/exiftool.exe", "source", "DATA")])
        analysis = Mock(return_value=types.SimpleNamespace(datas=[], pure=[], scripts=[], binaries=[]))
        exe, pyz = Mock(), Mock()
        spec = (bw.ROOT / "archaeo_gps.spec").read_text(encoding="utf-8")
        with patch.dict(sys.modules, {"pyproj": fake_proj, "PyInstaller.building.datastruct": fake_tree}), \
                patch.dict(os.environ, {"ARCHAEO_EXIFTOOL_BUNDLE": str(engine)}):
            exec(compile(spec, "archaeo_gps.spec", "exec"), {"SPECPATH": str(bw.ROOT), "Analysis": analysis, "PYZ": pyz, "EXE": exe})
        fake_tree.Tree.assert_called_once_with(str(engine), prefix="vendor/exiftool", typecode="DATA")
        self.assertFalse(exe.call_args.kwargs["upx"])
        self.assertFalse(exe.call_args.kwargs["console"])
        self.assertTrue(analysis.call_args.args[0][0].endswith("archaeo_launcher.py"))
        self.assertNotIn("a.zipfiles", spec)
        self.assertNotIn("a.zipped_data", spec)

    def test_builder_rejects_non_windows_hosts(self):
        with patch.object(sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                bw.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
