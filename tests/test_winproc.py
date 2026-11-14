"""
the main window's native procedure, run in an x64 emulator: the message
hold_aspect handles reaches the Python callback with every register as user32
left it, everything else reaches CallWindowProcW as its five arguments on an
aligned stack, and the unwind entry describes the frame that call runs in.
On Windows the last test puts it on a real window.
"""

import struct

import pytest

from patcher import winproc

BASE = 0x10000000
PREV, CALL, PYTHON, BACK = 0x20000000, 0x20001000, 0x20002000, 0x20003000
STACK = 0x30000000
HWND, WPARAM, LPARAM = 0x1234_5678_9ABC, 0x7777_0000_1111, 0x4444_3333_2222_1111
SAVED = ("RBX", "RBP", "RSI", "RDI", "R12", "R13", "R14", "R15")


def run(message, high=0):
    """
    one message through the stub, from a call site with user32's frame above
    it. What CALL and PYTHON saw on entry, and the registers it came back with
    """
    unicorn = pytest.importorskip("unicorn")
    from unicorn import x86_const as x

    emu = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
    image = winproc.assemble()
    emu.mem_map(BASE, len(image.body))
    emu.mem_write(BASE, winproc.fill(image.body, PREV, CALL, PYTHON))
    emu.mem_map(CALL, 0x3000)
    emu.mem_write(CALL, bytes.fromhex("48c7c0c0000000c3"))       # mov rax, 0xc0; ret
    emu.mem_write(PYTHON, bytes.fromhex("48c7c050000000c3"))     # mov rax, 0x50; ret
    emu.mem_write(BACK, b"\x90")
    emu.mem_map(STACK, 0x10000)

    entry = STACK + 0x8000 - 8          # a call just pushed its return address
    above = bytes(range(256)) * 2       # the caller's frame, which nothing may touch
    emu.mem_write(entry + 8 + 0x20, above)
    emu.mem_write(entry, struct.pack("<Q", BACK))
    reg = lambda name: getattr(x, "UC_X86_REG_" + name)
    emu.reg_write(reg("RSP"), entry)
    emu.reg_write(reg("RCX"), HWND)
    emu.reg_write(reg("RDX"), high << 32 | message)
    emu.reg_write(reg("R8"), WPARAM)
    emu.reg_write(reg("R9"), LPARAM)
    for i, name in enumerate(SAVED):
        emu.reg_write(reg(name), 0x5A5A_0000 + i)

    seen = {}

    def arrive(uc, address, size, _):
        if address in (CALL, PYTHON) and address not in seen:
            rsp = uc.reg_read(reg("RSP"))
            seen[address] = {
                "rcx": uc.reg_read(reg("RCX")), "rdx": uc.reg_read(reg("RDX")),
                "r8": uc.reg_read(reg("R8")), "r9": uc.reg_read(reg("R9")), "rsp": rsp,
                "back": struct.unpack("<Q", uc.mem_read(rsp, 8))[0],
                "fifth": struct.unpack("<Q", uc.mem_read(rsp + 0x28, 8))[0]}

    emu.hook_add(unicorn.UC_HOOK_CODE, arrive)
    emu.emu_start(BASE, BACK, count=100)
    after = {name.lower(): emu.reg_read(reg(name)) for name in SAVED + ("RAX", "RSP")}
    assert bytes(emu.mem_read(entry + 8 + 0x20, len(above))) == above, "the caller's frame changed"
    return seen, after, entry


@pytest.mark.parametrize("message", [winproc.WM_SIZING])
def test_the_one_it_handles_goes_to_python_as_it_came(message):
    seen, after, entry = run(message, high=0xDEAD)
    assert list(seen) == [PYTHON]
    got = seen[PYTHON]
    assert (got["rcx"], got["rdx"], got["r8"], got["r9"]) == (HWND, 0xDEAD << 32 | message,
                                                               WPARAM, LPARAM)
    assert got["rsp"] == entry and got["back"] == BACK, "a tail call, so Python returns to user32"
    assert after["rax"] == 0x50 and after["rsp"] == entry + 8


# WM_GETMINMAXINFO among them: maximise and fullscreen are the window's own now
@pytest.mark.parametrize("message", [0x0020, 0x0200, 0x0021, 0x0210, 0x0000, 0x8001,
                                     winproc.WM_GETMINMAXINFO])
def test_everything_else_goes_to_the_window_without_python(message):
    # the top half of rdx is garbage: the message is a UINT
    seen, after, entry = run(message, high=0xBEEF)
    assert list(seen) == [CALL]
    got = seen[CALL]
    assert (got["rcx"], got["rdx"], got["r8"], got["r9"], got["fifth"]) == (
        PREV, HWND, message, WPARAM, LPARAM), "CallWindowProcW(prev, hwnd, message, wparam, lparam)"
    assert got["rsp"] % 16 == 8, "the call has to land on a 16 byte boundary"
    assert after["rax"] == 0xC0 and after["rsp"] == entry + 8
    assert [after[name.lower()] for name in SAVED] == [0x5A5A_0000 + i for i in range(len(SAVED))]


def test_the_unwind_entry_describes_the_frame_the_call_runs_in():
    image = winproc.assemble()
    body = image.body
    begin, end, info = struct.unpack_from("<III", body, winproc.TABLE)
    assert (begin, end) == (image.forward, image.end) and info == winproc.UNWIND
    version, prolog, count, frame = body[info:info + 4]
    assert (version & 7, version >> 3, prolog, count, frame) == (1, 0, 4, 1, 0)
    offset, op = body[info + 4], body[info + 5]
    assert (offset, op & 15, (op >> 4) * 8 + 8) == (4, winproc.UWOP_ALLOC_SMALL, winproc.FRAME)
    assert body[begin:begin + 4] == bytes.fromhex("4883ec") + bytes([winproc.FRAME])
    assert body[end - 5:end] == bytes.fromhex("4883c4") + bytes([winproc.FRAME]) + b"\xc3"
    assert info % 4 == 0 and winproc.TABLE % 4 == 0


def test_unwinding_from_inside_the_call_lands_back_in_user32():
    """what RtlVirtualUnwind does with the entry, done by hand off a real run"""
    seen, _, entry = run(0x0200)
    at_call = seen[CALL]["rsp"] + 8          # the stub's own rsp, past CALL's return address
    assert BASE + winproc.assemble().forward < seen[CALL]["back"] < BASE + winproc.assemble().end
    assert at_call + winproc.FRAME == entry, "ALLOC_SMALL undone puts rsp on user32's return"


def test_a_message_list_of_one_or_three_still_lands_right():
    for messages in ((winproc.WM_SIZING,), (0x10, 0x214, 0x24)):
        image = winproc.assemble(messages)
        assert image.forward == 12 * len(messages) + 6
    with pytest.raises(ValueError):
        winproc.assemble(())


@pytest.mark.skipif(not winproc.native_ok(), reason="the stub only runs on x64 Windows")
def test_on_a_real_window_python_hears_only_its_one():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                                 wintypes.LPARAM)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD] + [ctypes.c_int] * 4 + [
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                       wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT

    hwnd = user32.CreateWindowExW(0, "STATIC", "winproc test", 0, 0, 0, 10, 10,
                                  None, None, None, None)
    assert hwnd
    heard, prev = [], user32.GetWindowLongPtrW(hwnd, -4)

    def python(handle, message, wparam, lparam):
        heard.append(message)
        return 0x51

    hooked = WNDPROC(python)
    stub = winproc.Native(ctypes.cast(hooked, ctypes.c_void_p).value)
    stub.set_prev(prev)
    assert user32.SetWindowLongPtrW(hwnd, -4, stub.address) == prev
    try:
        assert user32.SendMessageW(hwnd, winproc.WM_SIZING, 0, 0) == 0x51
        # WM_SETTEXT goes through to the static control's own procedure
        assert user32.SendMessageW(hwnd, 0x000C, 0, ctypes.cast(ctypes.c_wchar_p("ok"),
                                                                ctypes.c_void_p).value)
        title = ctypes.create_unicode_buffer(8)
        user32.SendMessageW(hwnd, 0x000D, 8, ctypes.addressof(title))
        assert title.value == "ok" and heard == [winproc.WM_SIZING]
    finally:
        user32.SetWindowLongPtrW(hwnd, -4, prev)
        user32.DestroyWindow(hwnd)
