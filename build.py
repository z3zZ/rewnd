"""Build a standalone rewnd app (a folder you can zip and share) with PyInstaller.

    pip install pyinstaller
    python build.py

Output: dist/rewnd/  (run rewnd.exe on Windows, ./rewnd on Linux)
"""

import os
import shutil
import subprocess
import sys

sep = ";" if os.name == "nt" else ":"
UNUSED_QT = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets",
             "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQml", "Qt3DCore", "Qt3DRender", "Qt3DExtras",
             "QtCharts", "QtDataVisualization", "QtGraphs", "QtMultimedia", "QtMultimediaWidgets", "QtPdf",
             "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors",
             "QtSerialPort", "QtSql", "QtTest", "QtDesigner", "QtHelp", "QtOpenGL", "QtOpenGLWidgets",
             "QtRemoteObjects", "QtScxml", "QtSpatialAudio", "QtTextToSpeech", "QtHttpServer"]
args = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
    "--name", "rewnd",
    "--add-data", f"rewnd/assets{sep}rewnd/assets",
    "--collect-binaries", "imageio_ffmpeg",
    "--collect-data", "tzdata",              # offline city list for sunrise/sunset scheduling
    *(["--collect-submodules", "jeepney"] if sys.platform.startswith("linux") else []),
    "--icon", "rewnd/assets/rewnd.ico",
    "--exclude-module", "tkinter",
    *[x for m in UNUSED_QT for x in ("--exclude-module", f"PySide6.{m}")],
    "rewnd/__main__.py",
]
code = subprocess.call(args)
if code == 0:
    # MIT requires the licence notice to travel with copies of the app.
    shutil.copy("LICENSE", os.path.join("dist", "rewnd", "LICENSE.txt"))
sys.exit(code)
