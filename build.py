"""Build script for LightRec (PyInstaller)"""
# pip install pyinstaller
# python build.py
# Output goes to dist/. Put ffmpeg next to it or let LightRec download it.
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "docs", "icon.ico")

args = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "LightRec",
    "--collect-binaries", "sounddevice",
    "--collect-binaries", "pyaudiowpatch",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
]
# The .ico only makes sense for the Windows exe (and Linux PyInstaller doesn't take one).
if sys.platform == "win32" and os.path.isfile(ICON):
    args += ["--icon", ICON]
args.append(os.path.join(HERE, "LightRec.py"))
sys.exit(subprocess.call(args, cwd=HERE))
