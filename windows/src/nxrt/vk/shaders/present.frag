#version 450
// Mode 0: the presented texture. Mode 1: the test pattern shown until the replay draws frames: colour bars, a 16:9 grid that shows the letterboxing,
// and a bar that moves one step per game present (so the window visibly runs at the game's pace)
layout(location = 0) in vec2 uv;
layout(location = 0) out vec4 o;
layout(set = 0, binding = 0) uniform sampler2D src;
layout(push_constant) uniform PC { vec4 rect; vec4 misc; } pc;
void main() {
    if (pc.misc.x < 0.5) { o = vec4(texture(src, uv).rgb, 1.0); return; }
    vec2 t = clamp((uv - pc.rect.xy) / (pc.rect.zw - pc.rect.xy), 0.0, 1.0);
    const vec3 bars[8] = vec3[](vec3(0.75), vec3(0.75, 0.75, 0.0), vec3(0.0, 0.75, 0.75), vec3(0.0, 0.75, 0.0),
                                vec3(0.75, 0.0, 0.75), vec3(0.75, 0.0, 0.0), vec3(0.0, 0.0, 0.75), vec3(0.1));
    vec3 c = t.y < 0.62 ? bars[int(min(t.x * 8.0, 7.0))] : vec3(0.06 + 0.5 * t.x);
    vec2 px = t * pc.misc.zw;
    vec2 g = abs(fract(px / 120.0 + 0.5) - 0.5) * 120.0;
    if (min(g.x, g.y) < 1.0) c = mix(c, vec3(1.0), 0.35);
    float f = mod(pc.misc.y, 120.0) / 120.0;
    if (t.y > 0.66 && t.y < 0.9 && abs(t.x - f) < 0.012) c = vec3(1.0);
    if (t.y > 0.92 && t.x < mod(pc.misc.y, 60.0) / 60.0) c = vec3(0.95, 0.55, 0.1);
    if (t.x < 0.004 || t.x > 0.996 || t.y < 0.007 || t.y > 0.993) c = vec3(1.0, 0.2, 0.2);
    o = vec4(c, 1.0);
}
