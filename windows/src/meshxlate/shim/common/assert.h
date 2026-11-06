#pragma once
#include <cstdio>
#include <cstdlib>
#define ASSERT(x) do { if (!(x)) { std::fprintf(stderr, "assert: %s\n", #x); std::abort(); } } while (0)
#define ASSERT_MSG(x, ...) ASSERT(x)
#define DEBUG_ASSERT(x) ((void)0)
#define DEBUG_ASSERT_MSG(x, ...) ((void)0)
#define UNREACHABLE() std::abort()
#define UNREACHABLE_MSG(...) std::abort()
#define UNIMPLEMENTED() ((void)0)
#define UNIMPLEMENTED_MSG(...) ((void)0)
