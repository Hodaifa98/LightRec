"""Build script for LightRec (PyInstaller)"""
# pip install pyinstaller
# python build.py
# Output goes to dist/. Put ffmpeg next to it or let LightRec download it.
import subprocess
import sys

args = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "LightRec",
    "--collect-binaries", "sounddevice",
    "--collect-binaries", "pyaudiowpatch",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "LightRec.py",
]
sys.exit(subprocess.call(args))
