# swapin: the Mac swap-in harness (DESIGN D15.2, docs/m4-swapin.md). arm64 macOS only (the top CMakeLists adds it on APPLE).
# This builds the runtime half of the swap-in dylib; tools/build_dylib.sh lifts the chosen functions with nxlift --swapin and
# links them with it into swapin.dylib, which a copy of nxnative (nxnative/hle_swapin.c) loads.
enable_language(ASM)
add_library(nxw_swapin_rt STATIC swapin_rt.c swapin_entry.S)
target_include_directories(nxw_swapin_rt PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} PRIVATE ${CMAKE_SOURCE_DIR}/src/sem/include)
target_link_libraries(nxw_swapin_rt PUBLIC nxw_abi)
target_compile_options(nxw_swapin_rt PRIVATE $<$<COMPILE_LANGUAGE:C>:-O2 -fno-stack-protector>)
