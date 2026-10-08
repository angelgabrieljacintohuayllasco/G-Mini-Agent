"""La msvcp140.dll más nueva entra al proceso antes que la que trae winrt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.utils import msvc_runtime

ROOT = Path(__file__).resolve().parent.parent

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


def _bundled_with_winrt() -> Path | None:
    for path in msvc_runtime.candidates():
        if "winrt" in path.parts:
            return path
    return None


def _loaded_after(snippet: str) -> dict:
    code = (
        "import json\n"
        f"{snippet}\n"
        "import winrt.windows.media.ocr\n"
        "from backend.utils.msvc_runtime import loaded_runtime\n"
        "path, version = loaded_runtime()\n"
        "print(json.dumps({'path': path, 'version': list(version or ())}))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_does_nothing_outside_windows(monkeypatch):
    monkeypatch.setattr(msvc_runtime.sys, "platform", "linux")
    monkeypatch.setattr(msvc_runtime, "_preloaded", None)
    assert msvc_runtime.preload_msvc_runtime() is None
    assert msvc_runtime.loaded_runtime() is None
    assert msvc_runtime.file_version("msvcp140.dll") is None


@windows_only
def test_reads_the_version_of_the_system_copy():
    system = Path(msvc_runtime.os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "msvcp140.dll"
    if not system.is_file():
        pytest.skip("sin el runtime de Visual C++ instalado")
    version = msvc_runtime.file_version(system)
    assert version is not None and version[0] == 14


@windows_only
def test_newest_copy_wins_over_the_one_bundled_with_winrt():
    bundled = _bundled_with_winrt()
    if bundled is None:
        pytest.skip("winrt no está instalado o no trae msvcp140.dll")
    newest = max(msvc_runtime.file_version(path) or (0, 0, 0, 0) for path in msvc_runtime.candidates())

    loaded = _loaded_after("from backend.utils.msvc_runtime import preload_msvc_runtime\npreload_msvc_runtime()")
    assert tuple(loaded["version"]) == newest

    # La OCR de Windows lo hace sola al importarse.
    loaded = _loaded_after("import backend.vision.windows_ocr")
    assert tuple(loaded["version"]) == newest
