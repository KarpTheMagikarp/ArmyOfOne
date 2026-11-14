"""
FF XIII HD Models v4.0.0 can spin for hours on a model with nothing wrong with
it. This runs a copy of the tool that doesn't.

Every recipe the mod ships declares zero name swaps, so the tool allocates an
empty swap table and never writes to it. Its swap pass still bounds the loop on
`size - strlen(table[0])`, unsigned, and table[0] is whatever the heap left
there. Junk longer than the chunk being scanned wraps the bound to about 4.29
billion, and every turn measures the junk again. n111's 4-byte chunk did it on
a live run: 15+ minutes before Stop, about a minute under wine with the heap
filled. What it writes when it does finish is right. It just costs the time.

The fix is the jump the pass takes when the table is empty. It went to the
position step, and now it goes to the return. One byte. It's only made to the
build it was worked out on, checked by hash on both sides, and only to a copy.
The mod's own exe is never written.
"""

import hashlib
from pathlib import Path

# sha256 of the tool: (file offset, byte there, byte it becomes, sha256 after)
FIXES = {
    "9b0e7af301f448136ceda857a2e7e46ae2db004bbbe028fcaa208211d96a2833": (
        0xDFDA, 0xC7, 0xE9,
        "a5769d653fb0c0fccbae8dde9545499dab7104dd9af6b17baffa50f7058fafba"),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixed_copy(support: Path, name: str, fixed_name: str) -> Path | None:
    """
    the fixed copy beside the mod's exe, written if it isn't there yet, or None
    to run theirs as it is. Never raises: theirs under the step limit is the
    fallback, so failing to make the copy is no reason to stop an install.
    """
    try:
        data = (Path(support) / name).read_bytes()
    except OSError:
        return None
    fix = FIXES.get(_sha(data))
    if fix is None:
        return None
    offset, before, after, want = fix
    target = Path(support) / fixed_name
    try:
        if target.is_file() and _sha(target.read_bytes()) == want:
            return target
        if data[offset] != before:
            return None
        fixed = bytearray(data)
        fixed[offset] = after
        if _sha(bytes(fixed)) != want:
            return None
        from patcher.fileops import write_atomic
        write_atomic(target, bytes(fixed), durable=True)
    except (OSError, IndexError):
        return None
    return target
