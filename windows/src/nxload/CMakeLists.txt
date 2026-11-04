# nxload: NSO/MOD0/dynamic/symbols/relocations, .eh_frame, npdm, and rtld-style program linking
add_library(nxload STATIC src/lz4.cpp src/sha256.cpp src/module.cpp src/ehframe.cpp src/npdm.cpp src/program.cpp)
target_include_directories(nxload PUBLIC include)
target_link_libraries(nxload PUBLIC nxw_common PRIVATE $<$<PLATFORM_ID:Windows>:bcrypt>)

add_executable(nxload-info tools/nxload_info.cpp)
target_link_libraries(nxload-info PRIVATE nxload)

add_executable(nxload-tests tests/synthetic.cpp)
target_link_libraries(nxload-tests PRIVATE nxload)
add_test(NAME nxload.synthetic COMMAND nxload-tests)

# Reads the ExeFS folder named by NXW_DUMP at run time; skipped when it is not set
add_executable(nxload-dump-tests tests/dump.cpp)
target_link_libraries(nxload-dump-tests PRIVATE nxload)
add_test(NAME nxload.dump COMMAND nxload-dump-tests)
set_tests_properties(nxload.dump PROPERTIES SKIP_RETURN_CODE 77)
