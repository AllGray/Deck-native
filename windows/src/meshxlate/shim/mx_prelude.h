// Included before every file: what Eglantine's build gets from its precompiled header (fmt, and formatting enums by their value)
#pragma once
#include <type_traits>
#include <fmt/format.h>
#include <fmt/ranges.h>
template <typename T>
    requires std::is_enum_v<T>
struct fmt::formatter<T, char> : fmt::formatter<std::underlying_type_t<T>, char> {
    auto format(const T &v, fmt::format_context &ctx) const { return fmt::formatter<std::underlying_type_t<T>, char>::format(static_cast<std::underlying_type_t<T>>(v), ctx); }
};
