"""Microsoft Store package entry point: AppxManifest.xml starts `runtime\\pythonw.exe -m dolmi_launch`.

A packaged app starts in a working folder Windows chooses, so the manifest cannot pass main.py as a
relative path. build-msix.ps1 puts this file next to pythonw.exe, where python312._pth (".") puts it on
sys.path, and it runs app\\src\\main.py by its absolute path.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent.parent / "app" / "src" / "main.py"), run_name="__main__")
