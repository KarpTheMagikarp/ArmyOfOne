# 32-bit only: the games are x86, and NovaFix refuses any add-on whose PE
# machine is not IMAGE_FILE_MACHINE_I386, so a 64-bit build would install
# cleanly and then never load.
cmake_minimum_required(VERSION 3.21)
project(ff13_exe_redirect LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
  minhook
  GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
  GIT_TAG        v1.3.3
)
FetchContent_MakeAvailable(minhook)

add_library(ff13_exe_redirect SHARED ff13_exe_redirect.cpp redirect_policy.h)
target_link_libraries(ff13_exe_redirect PRIVATE minhook)

# the name matters only for ordering: NovaFix loads add-ons alphabetically, and
# this one wants to be early so its hook is in place before anything else looks
# at the executable
set_target_properties(ff13_exe_redirect PROPERTIES
  OUTPUT_NAME "00_ff13_exe_redirect"
  PREFIX ""
  SUFFIX ".asi")

if(MSVC)
  target_compile_options(ff13_exe_redirect PRIVATE /W4 /permissive-)
  # no CRT dependency to chase at load time
  set_property(TARGET ff13_exe_redirect PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

# The decision logic builds and runs anywhere, so it is tested on whatever
# machine is to hand rather than only on Windows.
#   g++ -std=c++20 -Wall -Wextra -Werror -o policy_test redirect_policy_test.cpp
enable_testing()
add_executable(redirect_policy_test redirect_policy_test.cpp)
add_test(NAME redirect_policy COMMAND redirect_policy_test)
