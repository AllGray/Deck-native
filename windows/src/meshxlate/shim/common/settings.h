// What the recompiler reads of the emulator's settings, fixed
#pragma once
namespace Settings {
template <typename T> struct V { T v; const T &GetValue() const { return v; } operator T() const { return v; } };
struct Res { bool active = false; unsigned up_scale = 1, down_shift = 0; float up_factor = 1, down_factor = 1; };
struct Values { V<bool> disable_shader_loop_safety_checks{true}, fix_bloom_effects{false}, renderer_debug{false}; Res resolution_info; };
inline Values values;
}
