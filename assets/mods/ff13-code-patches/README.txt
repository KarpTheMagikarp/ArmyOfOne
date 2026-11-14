01_ff13_code_patches.asi goes here

Build it from addon-src/ff13-code-patches:

    cmake -B build -A Win32
    cmake --build build --config Release

Output lands at build/Release/01_ff13_code_patches.asi. Copy it into this
folder and the patcher puts it into NovaFix's addons directory the first time
someone drops in a mod with Nova Chrysalia code patches (Code\*.nccp).

32-bit only. NovaFix checks IMAGE_FILE_MACHINE_I386 and skips anything else, so
a 64-bit build installs cleanly and then never loads.
