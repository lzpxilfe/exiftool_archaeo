# Bundled software notices

The Windows build includes the unmodified ExifTool 13.59 64-bit distribution by
Phil Harvey and its Windows/Perl runtime. Only the launcher name is changed from
`exiftool(-k).exe` to `exiftool.exe`, as described by its installation guide.
The complete archive package, including its original notices and license files,
is retained inside the application under `vendor/exiftool`.

- ExifTool source and project: https://github.com/exiftool/exiftool
- Official distribution: https://sourceforge.net/projects/exiftool/files/
- Installation and distribution layout: https://exiftool.sourceforge.net/install.html
- Official checksums: https://exiftool.sourceforge.net/checksums.txt
- Pinned archive: `exiftool-13.59_64.zip`
- SHA-256: `44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec`

ExifTool is distributed under the same terms as Perl (Artistic License or GNU
General Public License). Its bundled components retain their own notices and
terms. See the original package files and ExifTool's LICENSE documentation.
This build does not change the licensing of any upstream component or of this
repository. Keep this notice and the upstream license notices when distributing.

The application also bundles Python/Tcl/Tk, Pillow and pyproj/PROJ components
using PyInstaller. Refer to their upstream distributions for their respective
license terms. PyInstaller is the build tool; it is not an ExifTool installer.
