#!/usr/bin/env bash
# builds tools/fxcheck/fxcheck from ReShade 6.8.0's effect compiler, the release the
# shader row ships, so a check passing here means the same parser in game takes it.
# Needs git and a C++17 compiler; RESHADE_SRC can point at a checkout of that tag
# instead. The source and the binary stay out of git.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
src="${RESHADE_SRC:-$here/.reshade}"

# a checkout somebody points at is theirs: say so rather than clear it out
if [ ! -f "$src/source/effect_parser.hpp" ]; then
  if [ -n "${RESHADE_SRC:-}" ]; then
    echo "RESHADE_SRC=$src has no source/effect_parser.hpp; point it at a checkout of v6.8.0" >&2
    exit 1
  fi
  rm -rf "$src"
  git clone --quiet --depth 1 --branch v6.8.0 https://github.com/crosire/reshade.git "$src"
fi

sources=()
for name in effect_codegen_hlsl effect_expression effect_lexer effect_parser_exp \
            effect_parser_stmt effect_preprocessor effect_symbol_table; do
  sources+=("$src/source/$name.cpp")
done
"${CXX:-c++}" -std=c++17 -O1 -w -I "$src/source" "$here/fxcheck.cpp" "${sources[@]}" \
  -o "$here/fxcheck"
echo "built $here/fxcheck"
