NovaFix proxy, shipped as d3d9.dll.

Committed with the author's permission. NovaFix is by Kartin8 and is GPL-3.0,
the same licence as this project, so redistributing it here is fine. Credit is
in the top-level README.

    source   https://github.com/Kartin8/FF13NovaFix
    size     2,053,632 bytes
    sha256   1e2c96f2101f81bf7d6097e9725c508b127cd143aeb07decbcdda56291b6a458
    build    PE32 x86, linked 2026-10-14, release 1.0.2

The sha256 is pinned in all three manifests. That is the right call now and was
the wrong call before: pinning a file the USER supplies refuses any build but
the exact one whose hash we recorded, which is what made a perfectly good
NovaFix download fail on a second machine. Pinning one we commit is us
vouching for what we ship, and it catches a corrupted bundle or a swapped file
before it reaches a game folder.

Shape validation still runs underneath the pin, so a bad replacement gets a
useful sentence rather than a hash mismatch.

Replacing it
------------
These games stopped receiving updates, so this is not expected to go stale. If
it ever needs replacing:

    1. drop the new d3d9.dll in this folder
    2. python tools/pin_bundled.py        (rewrites the sha256 in every manifest)
    3. pytest -q

Anything that reads NovaFix.ini is version-coupled, so check the settings
schema in patcher/novafix_config.py still matches. tests/test_novafix_config.py
has a check that fails if a key we write is not present in the shipped binary.
