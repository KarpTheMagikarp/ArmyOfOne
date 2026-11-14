00_ff13_exe_redirect.asi goes here

Build it from addon-src/ff13-exe-redirect:

    cmake -B build -A Win32
    cmake --build build --config Release

Output lands at build/Release/00_ff13_exe_redirect.asi. Copy it into this
folder and the patcher installs it into NovaFix's addons directory, which is
what lets XIII take the 4GB patch on the stock Steam executable.

32-bit only. NovaFix checks IMAGE_FILE_MACHINE_I386 and skips anything else, so
a 64-bit build installs cleanly and then never loads.

Not committed because it is a compiled binary. Record its sha256 in
manifests/ffxiii.json once you settle on a build.
