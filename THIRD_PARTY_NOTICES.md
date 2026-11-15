# Third-party software

The macOS app bundles the following software. Each keeps its own license.

| Component | Use | License |
|---|---|---|
| [Python](https://www.python.org) | runtime | PSF License |
| [pyaaf2](https://github.com/markreidvfx/pyaaf2) | reading AAF files | MIT |
| [NumPy](https://numpy.org) | audio sample handling | BSD-3-Clause |
| [zstandard](https://github.com/indygreg/python-zstandard) | compression inside .drt files | BSD-3-Clause |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | ships the ffmpeg binary | BSD-2-Clause |
| [FFmpeg](https://ffmpeg.org) | decoding MXF audio, reading media info | GPL / LGPL (the build shipped by imageio-ffmpeg); source at https://ffmpeg.org/download.html |
| [PyInstaller](https://pyinstaller.org) bootloader | packaging | GPL with bootloader exception |

DaVinci Resolve, Fairlight and Avid Media Composer are trademarks of their owners. This project is not affiliated
with Blackmagic Design or Avid. The .drt structure was worked out from timelines that Resolve itself exported, for
interoperability.
