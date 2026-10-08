"""Runtime de C++ de Windows (msvcp140.dll) cargado antes que winrt.

winrt-runtime trae su propia msvcp140.dll (14.29, de VS 2019). Windows usa
la primera copia que entra al proceso para todos los módulos, así que si el
OCR de Windows se importa primero, ctranslate2 (faster-whisper) y torch,
compilados con un MSVC más nuevo, se enlazan contra la vieja: crear el modelo
de Whisper termina en access violation y torch no inicia c10.dll. Cargando
antes la copia más nueva disponible, todos usan esa, que es compatible hacia
atrás con lo compilado con versiones anteriores.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
from pathlib import Path

DLL_NAME = "msvcp140.dll"

Version = tuple[int, int, int, int]

_preloaded: str | None = None


def file_version(path: str | os.PathLike) -> Version | None:
    """Versión de archivo de una DLL (VS_FIXEDFILEINFO), o None si no se puede leer."""
    if sys.platform != "win32":
        return None
    api = ctypes.windll.version
    path = str(path)
    size = api.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(path, 0, size, buffer):
        return None
    info = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not api.VerQueryValueW(buffer, "\\", ctypes.byref(info), ctypes.byref(length)) or not info.value:
        return None
    # VS_FIXEDFILEINFO: firma, versión de la estructura, FileVersionMS, FileVersionLS...
    fixed = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
    high, low = fixed[2], fixed[3]
    return (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)


def candidates() -> list[Path]:
    """Copias de msvcp140.dll que pueden terminar en el proceso: la del sistema y la de winrt."""
    found = []
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / DLL_NAME
    if system32.is_file():
        found.append(system32)
    try:
        spec = importlib.util.find_spec("winrt")
    except (ImportError, ValueError):
        spec = None
    for location in (spec.submodule_search_locations or []) if spec else []:
        bundled = Path(location) / DLL_NAME
        if bundled.is_file():
            found.append(bundled)
    return found


def loaded_runtime() -> tuple[str, Version | None] | None:
    """Ruta y versión de la msvcp140.dll que ya está en el proceso, si hay una."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    handle = kernel32.GetModuleHandleW(DLL_NAME)
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetModuleFileNameW(handle, buffer, len(buffer)):
        return None
    return buffer.value, file_version(buffer.value)


def preload_msvc_runtime() -> str | None:
    """Carga la msvcp140.dll más nueva entre la del sistema y la de winrt.

    Hay que llamarla antes de importar winrt. Devuelve la ruta cargada, o None
    fuera de Windows o si no hay ninguna copia. Si otra ya estaba cargada no
    se puede reemplazar: devuelve esa.
    """
    global _preloaded
    if _preloaded or sys.platform != "win32":
        return _preloaded
    current = loaded_runtime()
    if current:
        _preloaded = current[0]
        return _preloaded
    versions = [(file_version(path) or (0, 0, 0, 0), path) for path in candidates()]
    if not versions:
        return None
    _, newest = max(versions, key=lambda item: item[0])
    try:
        ctypes.WinDLL(str(newest))
    except OSError:
        return None
    _preloaded = str(newest)
    return _preloaded
