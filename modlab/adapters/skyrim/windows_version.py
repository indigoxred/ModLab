"""Read Windows executable file versions without third-party packages."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", wintypes.DWORD),
        ("structure_version", wintypes.DWORD),
        ("file_version_ms", wintypes.DWORD),
        ("file_version_ls", wintypes.DWORD),
        ("product_version_ms", wintypes.DWORD),
        ("product_version_ls", wintypes.DWORD),
        ("file_flags_mask", wintypes.DWORD),
        ("file_flags", wintypes.DWORD),
        ("file_os", wintypes.DWORD),
        ("file_type", wintypes.DWORD),
        ("file_subtype", wintypes.DWORD),
        ("file_date_ms", wintypes.DWORD),
        ("file_date_ls", wintypes.DWORD),
    ]


def read_windows_file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    version = ctypes.WinDLL("version", use_last_error=True)
    get_size = version.GetFileVersionInfoSizeW
    get_size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    get_size.restype = wintypes.DWORD
    ignored = wintypes.DWORD()
    size = get_size(str(path), ctypes.byref(ignored))
    if size == 0:
        return None

    get_info = version.GetFileVersionInfoW
    get_info.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    get_info.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(size)
    if not get_info(str(path), 0, size, buffer):
        raise ctypes.WinError(ctypes.get_last_error())

    query = version.VerQueryValueW
    query.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.UINT),
    )
    query.restype = wintypes.BOOL
    value_pointer = wintypes.LPVOID()
    value_size = wintypes.UINT()
    if not query(buffer, "\\", ctypes.byref(value_pointer), ctypes.byref(value_size)):
        raise ctypes.WinError(ctypes.get_last_error())
    if value_size.value < ctypes.sizeof(_FixedFileInfo):
        return None
    info = ctypes.cast(
        value_pointer,
        ctypes.POINTER(_FixedFileInfo),
    ).contents
    if info.signature != 0xFEEF04BD:
        return None
    parts = (
        info.file_version_ms >> 16,
        info.file_version_ms & 0xFFFF,
        info.file_version_ls >> 16,
        info.file_version_ls & 0xFFFF,
    )
    return ".".join(str(part) for part in parts)
