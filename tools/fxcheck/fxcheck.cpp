// fxcheck <effect.fx> [NAME=VALUE ...]
//
// Runs an effect through ReShade's own front end, the preprocessor and parser
// every ReShade build compiles effects with, and prints what they say. A typo,
// an identifier a pass can't see or a type that doesn't fit fails here the way
// it fails in game, where the only sign is the grade quietly not running. What
// it can't see is D3D9's own limits (32 temp registers, say), which only
// d3dcompiler_47 knows: the effect is turned into the same SM3 HLSL ReShade
// hands it, and stops there.
//
// Built against ReShade 6.8.0's source by build.sh. Exit code 0 when it parses
// with no error.

#include "effect_codegen.hpp"
#include "effect_parser.hpp"
#include "effect_preprocessor.hpp"

#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>

int main(int argc, char *argv[])
{
	if (argc < 2)
	{
		std::fputs("fxcheck <effect.fx> [NAME=VALUE ...]\n", stderr);
		return 2;
	}

	const std::filesystem::path path = argv[1];
	reshadefx::preprocessor pp;
	pp.add_include_path(path.parent_path());

	// what runtime.cpp defines ahead of every effect, here for D3D9 at 1440p.
	// They go first because the first definition of a name is the one that sticks
	pp.add_macro_definition("__RESHADE__", "60800");
	pp.add_macro_definition("__RESHADE_PERMUTATION__", "0");
	pp.add_macro_definition("__RESHADE_PERFORMANCE_MODE__", "1");
	pp.add_macro_definition("__VENDOR__", "4318");
	pp.add_macro_definition("__DEVICE__", "0");
	pp.add_macro_definition("__RENDERER__", "36864");
	pp.add_macro_definition("__APPLICATION__", "0");
	pp.add_macro_definition("BUFFER_WIDTH", "2560");
	pp.add_macro_definition("BUFFER_HEIGHT", "1440");
	pp.add_macro_definition("BUFFER_RCP_WIDTH", "(1.0 / BUFFER_WIDTH)");
	pp.add_macro_definition("BUFFER_RCP_HEIGHT", "(1.0 / BUFFER_HEIGHT)");
	pp.add_macro_definition("BUFFER_COLOR_SPACE", "1");
	pp.add_macro_definition("BUFFER_COLOR_FORMAT", "28");
	pp.add_macro_definition("BUFFER_COLOR_BIT_DEPTH", "8");

	for (int i = 2; i < argc; ++i)
	{
		const std::string pair = argv[i];
		const size_t equals = pair.find('=');
		if (equals == std::string::npos)
			pp.add_macro_definition(pair);
		else
			pp.add_macro_definition(pair.substr(0, equals), pair.substr(equals + 1));
	}

	// and the names older releases had, which SweetFX's SMAA still calls, as
	// runtime.cpp puts them ahead of every file
	pp.append_string(
		"#define tex2Doffset(s, coords, offset) tex2D(s, coords, offset)\n"
		"#define tex2Dlodoffset(s, coords, offset) tex2Dlod(s, coords, offset)\n"
		"#define tex2Dgather(s, t, c) tex2Dgather##c(s, t)\n"
		"#define tex2Dgatheroffset(s, t, o, c) tex2Dgather##c(s, t, o)\n"
		"#define tex2Dgather0 tex2DgatherR\n"
		"#define tex2Dgather1 tex2DgatherG\n"
		"#define tex2Dgather2 tex2DgatherB\n"
		"#define tex2Dgather3 tex2DgatherA\n");

	const bool preprocessed = pp.append_file(path);
	std::fputs(pp.errors().c_str(), stdout);
	if (!preprocessed)
		return 1;

	// SM3 and uniforms as constants, the way a D3D9 game in performance mode gets it
	reshadefx::parser parser;
	const std::unique_ptr<reshadefx::codegen> backend(reshadefx::create_codegen_hlsl(30, false, true));
	const bool parsed = parser.parse(pp.output(), backend.get());
	std::fputs(parser.errors().c_str(), stdout);
	// some errors don't stop the parse (X3020, a pass sampling its own render
	// target), and ReShade runs the effect anyway, drawing nothing useful
	return parsed && parser.errors().find(": error") == std::string::npos ? 0 : 1;
}
