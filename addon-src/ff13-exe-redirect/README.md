# ff13-exe-redirect

Retired. Not installed by the patcher; the source stays because the dinput8
shim shares `redirect_policy.h`, and the engine still needs to know this
add-on's file names to remove it from older installs.

It hooked `CreateFileA`/`CreateFileW` so XIII's CEG self-check read
`untouched.exe` while the 4GB-patched exe ran (rebtd7's FF13Fix technique,
`d3d9ex/AutoFix.cpp`). The hook worked, but it could never run in time:
NovaFix loads add-ons from its one-time `Initialize()`, reached only on the
game's first Direct3D9 factory call (render init), and the CEG check runs in
the exe's entry point. A refused process never gets to render init, which is
why a failed launch left no `ff13-exe-redirect.log` at all.

The working version is `addon-src/ff13-dinput8-shim`, a statically imported
`dinput8.dll` whose `DllMain` runs before the entry point. See
[docs/4gb-patch.md](../../docs/4gb-patch.md).

## Testing the policy

`redirect_policy.h` has no Win32 dependency, and the shim uses it, so the
suite still compiles and runs its test:

```
g++ -std=c++20 -Wall -Wextra -Werror -o policy_test redirect_policy_test.cpp
./policy_test
```
