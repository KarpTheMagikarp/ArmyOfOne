"""
the main window's procedure in machine code, so the GIL stays out of its way.

hold_aspect() only needs one message: WM_SIZING, to keep a drag 16:9.
Maximise and fullscreen are the window's own. A ctypes callback as the window's
procedure runs Python for every message the window gets regardless, and each
one waits for the GIL first. Some of those are sent from WebView2's own
process and block it until they're answered, so a click or a focus change sat
behind whatever Python was doing on another thread: the asset server streaming
a clip, a scan, an unpack.

So the procedure is a few dozen bytes. What Python handles jumps to the
callback, untouched, and everything else goes to the window's own procedure
through CallWindowProcW without the interpreter ever hearing about it.

x64 only. Anything else, or anything here failing, and hold_aspect keeps the
Python procedure, which is the same lock at the old cost.
"""

import struct
from dataclasses import dataclass

WM_SIZING, WM_GETMINMAXINFO = 0x0214, 0x0024
PAGE = 0x1000
# home space for the four register arguments, the fifth one, and 8 more so
# the call lands 16 byte aligned (entry is 8 off, one return address in)
FRAME = 0x38

# the data page: three pointers, then the unwind info, then the function table
PREV, CALL, PYTHON = PAGE, PAGE + 8, PAGE + 16
UNWIND, TABLE = PAGE + 24, PAGE + 32
UWOP_ALLOC_SMALL = 2


@dataclass(frozen=True)
class Image:
    """two pages: code, then data. Offsets are from the start of the first"""
    body: bytes
    forward: int
    end: int


def _rel(at: int, size: int, target: int) -> bytes:
    """disp32 of an instruction `size` long at `at` that reaches `target`"""
    return struct.pack("<i", target - (at + size))


def assemble(messages=(WM_SIZING,)) -> Image:
    if not messages:
        raise ValueError("no messages for Python")
    code = bytearray()
    python_jump = 12 * len(messages)
    forward = python_jump + 6
    # cmp edx, message, then je to the Python jump. The last one is jne past
    # it, so a match falls through onto it. edx, not rdx: a message is a UINT
    # and the top half of rdx is whatever the caller left there
    for i, message in enumerate(messages):
        code += b"\x81\xfa" + struct.pack("<I", message)
        at = len(code)
        last = i == len(messages) - 1
        code += (b"\x0f\x85" + _rel(at, 6, forward)) if last else \
            (b"\x0f\x84" + _rel(at, 6, python_jump))
    # jmp [python]: a tail call, so the callback returns straight to user32
    code += b"\xff\x25" + _rel(len(code), 6, PYTHON)
    assert len(code) == forward

    # CallWindowProcW(prev, hwnd, message, wparam, lparam), five arguments, so
    # this one needs a frame of its own, and an unwind entry to go with it
    code += bytes.fromhex("4883ec38")                     # sub rsp, 0x38
    code += bytes.fromhex("4c894c2420")                   # mov [rsp+0x20], r9
    code += bytes.fromhex("4d89c1")                       # mov r9, r8
    code += bytes.fromhex("4189d0")                       # mov r8d, edx
    code += bytes.fromhex("4889ca")                       # mov rdx, rcx
    code += b"\x48\x8b\x0d" + _rel(len(code), 7, PREV)    # mov rcx, [prev]
    code += b"\xff\x15" + _rel(len(code), 6, CALL)        # call [CallWindowProcW]
    code += bytes.fromhex("4883c438")                     # add rsp, 0x38
    code += b"\xc3"                                       # ret
    end = len(code)

    body = bytearray(2 * PAGE)
    body[:end] = code
    # UNWIND_INFO version 1, a 4 byte prolog, one code: the sub rsp at offset
    # 4 as ALLOC_SMALL, padded to an even count. The add/ret epilog is one
    # the unwinder recognises on its own
    body[UNWIND:UNWIND + 8] = bytes([1, 4, 1, 0, 4, UWOP_ALLOC_SMALL | ((FRAME - 8) // 8) << 4,
                                     0, 0])
    # RUNTIME_FUNCTION for the framed part only. The dispatch before it never
    # moves rsp, which is what the unwinder assumes of code it has no entry for
    body[TABLE:TABLE + 12] = struct.pack("<III", forward, end, UNWIND)
    return Image(bytes(body), forward, end)


def fill(body: bytes, prev: int, call: int, python: int) -> bytes:
    out = bytearray(body)
    out[PREV:PREV + 24] = struct.pack("<QQQ", prev, call, python)
    return bytes(out)


class Native:
    """one stub in memory with its unwind entry registered. Windows x64 only"""

    def __init__(self, python: int, messages=(WM_SIZING,)):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        alloc = kernel32.VirtualAlloc
        alloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        alloc.restype = ctypes.c_void_p
        protect = kernel32.VirtualProtect
        protect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD)]
        protect.restype = wintypes.BOOL
        flush = kernel32.FlushInstructionCache
        flush.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
        flush.restype = wintypes.BOOL
        register = kernel32.RtlAddFunctionTable
        register.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_uint64]
        register.restype = ctypes.c_ubyte

        image = assemble(messages)
        base = alloc(None, len(image.body), 0x3000, 0x04)    # commit|reserve, read/write
        if not base:
            raise OSError(ctypes.get_last_error(), "VirtualAlloc")
        call = ctypes.cast(user32.CallWindowProcW, ctypes.c_void_p).value
        ctypes.memmove(base, fill(image.body, 0, call, python), len(image.body))
        was = wintypes.DWORD()
        # the code page goes read/execute and never writable again. The data
        # page stays writable for set_prev()
        if not protect(base, PAGE, 0x20, ctypes.byref(was)):
            raise OSError(ctypes.get_last_error(), "VirtualProtect")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        flush(kernel32.GetCurrentProcess(), base, PAGE)
        if not register(base + TABLE, 1, base):
            raise OSError(0, "RtlAddFunctionTable")
        self.address = base

    def set_prev(self, prev: int) -> None:
        """where everything else goes. One aligned 8 byte store, so a message
        mid-dispatch sees the old procedure or the new one, never half of each"""
        import ctypes

        ctypes.c_uint64.from_address(self.address + PREV).value = prev


def native_ok() -> bool:
    import os
    import platform

    return (os.name == "nt" and struct.calcsize("P") == 8
            and platform.machine().lower() in ("amd64", "x86_64"))
