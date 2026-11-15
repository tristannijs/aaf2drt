#!/usr/bin/env python3
"""Entry point of the app.

  main.py              start the local UI (opens the browser)
  main.py --engine …   run the converter itself (used by the UI as a child process)
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "engine"))   # source checkout
sys.path.insert(0, _here)


def _engine(argv):
    if not os.environ.get("FFMPEG"):
        try:
            import imageio_ffmpeg
            os.environ["FFMPEG"] = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    sys.argv = [sys.argv[0]] + argv
    import aaf2drt
    aaf2drt.main()


if __name__ == "__main__":
    if "--engine" in sys.argv:
        _engine(sys.argv[sys.argv.index("--engine") + 1:])
    else:
        import server
        server.main()
