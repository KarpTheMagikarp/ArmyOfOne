#include "nccp.h"

#include <cstdio>
#include <cstdlib>

using namespace ff13_nccp;

static int failures = 0;

static void Expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

int main() {
    auto a = Parse("\"Patch\"\n{\n  \"1\" \"0x1A2B|9090\"\n  \"2\" \"ff|EB 05\"\n  \"Name\" \"x\"\n}\n");
    Expect(a.patches.size() == 2, "two digit keys read, the name skipped");
    Expect(a.patches[0].rva == 0x1A2B && a.patches[0].bytes == std::vector<std::uint8_t>{0x90, 0x90},
           "0x rva and plain bytes");
    Expect(a.patches[1].rva == 0xFF && a.patches[1].bytes == std::vector<std::uint8_t>{0xEB, 0x05},
           "spaces between bytes forgiven");

    auto b = Parse("\xEF\xBB\xBF\"P\" { // a comment\n \"1\" \"10|ABC\" \"2\" \"zz|90\" \"3\" { \"4\" \"1|90\" } \"5\" \"20|C3\" }");
    Expect(b.patches.size() == 1 && b.patches[0].rva == 0x20, "bad entries and nested objects skipped");
    Expect(b.skipped.size() == 2, "the odd-length and non-hex ones are said");

    auto c = Parse("P { 1 \"100|90\" [$WIN32] 2 \"200|90\" }");
    Expect(c.patches.size() == 2, "unquoted tokens and conditionals");

    Expect(Parse("").patches.empty() && Parse("\"P\"").patches.empty() && Parse("{").patches.empty(),
           "nothing to read is nothing applied");

    Patch p{0x10, {1, 2, 3}};
    Expect(Inside(p, 0x13) && !Inside(p, 0x12) && !Inside(Patch{0x20, {1}}, 0x20),
           "a patch has to land wholly inside the image");

    if (failures == 0) std::printf("all good\n");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
