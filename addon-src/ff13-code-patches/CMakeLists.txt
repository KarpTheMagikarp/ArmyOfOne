# 32-bit only: the games are x86, and NovaFix refuses any add-on whose PE
# machine is not IMAGE_FILE_MACHINE_I386, so a 64-bit build would install
# cleanly and then never load.
cmake_minimum_required(VERSION 3.21)
project(ff13_code_patches LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(ff13_code_patches SHARED ff13_code_patches.cpp nccp.h version.rc)

# after 00_ff13_exe_redirect, which wants to be first: NovaFix loads add-ons
# alphabetically, and nothing here needs to beat it
set_target_properties(ff13_code_patches PROPERTIES
  OUTPUT_NAME "01_ff13_code_patches"
  PREFIX ""
  SUFFIX ".asi")

if(MSVC)
  target_compile_options(ff13_code_patches PRIVATE /W4 /permissive-)
  set_property(TARGET ff13_code_patches PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded")
else()
  # mingw: no libgcc or libstdc++ dll for the game's folder to be missing
  target_link_options(ff13_code_patches PRIVATE -static -static-libgcc -static-libstdc++)
endif()

enable_testing()
add_executable(nccp_test nccp_test.cpp)
add_test(NAME nccp COMMAND nccp_test)
