// meshxlate: a Switch 2 task or mesh program to SPIR-V, through Eglantine's shader recompiler (src/shader_recompiler, GPL-3.0-or-later).
// usage: meshxlate <key.ctl> <key.code> <out.spv> <out.json>
// The registers the emulator reads from the GPU come from the program's control block: +0x54 the stage (0x60000 task, 0x70000 mesh), +0x58 SET_MESH_INIT_SHADER's fields
// (threads 5:0, local lines 20:12, lines passed on 30:22), +0x60 maximum vertices and primitives (16 bits each), +0x64 where the primitive header sits in the code (0 = none).
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
#include <fmt/format.h>
#include "common/common_types.h"
#include "shader_recompiler/backend/bindings.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/environment.h"
#include "shader_recompiler/exception.h"
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
#include "shader_recompiler/frontend/ampere/program_address_map.h"
#include "shader_recompiler/frontend/ampere/program_capture.h"
#include "shader_recompiler/frontend/ir/basic_block.h"
#include "shader_recompiler/frontend/ir/program.h"
#include "shader_recompiler/frontend/maxwell/control_flow.h"
#include "shader_recompiler/frontend/maxwell/translate_program.h"
#include "shader_recompiler/host_translate_info.h"
#include "shader_recompiler/object_pool.h"
#include "shader_recompiler/profile.h"
#include "shader_recompiler/runtime_info.h"

using namespace Shader;
namespace {
std::vector<u8> read_file(const char *p) { std::ifstream f(p, std::ios::binary); return {std::istreambuf_iterator<char>(f), {}}; }
u32 r32(const std::vector<u8> &v, size_t o) { u32 x = 0; if (o + 4 <= v.size()) std::memcpy(&x, &v[o], 4); return x; }

class Env final : public Environment {
public:
    Env(const std::vector<u8> &ctl, const std::vector<u8> &bytes, Stage st) {
        stage = st; start_address = 0;
        std::memcpy(&sph, bytes.data(), sizeof(sph));
        const u32 insn_end = r32(ctl, 0x38), init = r32(ctl, 0x58), limits = r32(ctl, 0x60), prim_at = r32(ctl, 0x64);
        const u32 threads = init & 0x3f, a = 4 | (limits & 0x1ff) << 4 | ((limits >> 16) & 0x3ff) << 13;
        mesh_registers = {a, threads << 20, init};
        if (prim_at && prim_at + sizeof(mesh_primitive_header) <= bytes.size()) std::memcpy(&mesh_primitive_header, &bytes[prim_at], sizeof(mesh_primitive_header));
        using Ampere::Instruction;
        std::vector<Instruction> native; Ampere::ProgramCaptureState capture;
        for (u32 off = 128, i = 0; off + sizeof(Instruction) <= insn_end && off + sizeof(Instruction) <= bytes.size(); off += sizeof(Instruction), ++i) {
            Instruction in; std::memcpy(&in, &bytes[off], sizeof(in)); native.push_back(in);
            const auto s = capture.Observe(in, i);
            if (s == Ampere::ProgramCaptureState::Result::InvalidTarget) throw RuntimeError("branch outside the capture at +{:#x}", i * 16);
            if (s == Ampere::ProgramCaptureState::Result::Complete) break;
        }
        auto low = Ampere::LowerToMaxwell(native, {.geometry = false, .tensor = false, .native_code_address = 128});
        if (!low) throw RuntimeError("lowering failed at +{:#x}: {}", low.error_offset, low.error);
        tb = low.required_texture_buffer != ~0U ? low.required_texture_buffer : 2;
        code.resize(sizeof(sph) / sizeof(u64)); std::memcpy(code.data(), &sph, sizeof(sph));
        for (const u64 ins : low.instructions) { if ((code.size() * sizeof(u64)) % 32 == 0) code.push_back(0); code.push_back(ins); }
        code.push_back(0);
        Ampere::AppendNativeAddressMap(code, low.source_instruction_offsets);
        local = static_cast<u32>(sph.LocalMemorySize()) + sph.common3.shader_local_memory_crs_size;
    }
    u64 ReadInstruction(u32 a) override { if (a / 8 >= code.size()) throw RuntimeError("instruction {:#x} past the end", a); return code[a / 8]; }
    u32 TranslateProgramAddress(u32 a) const override { if (const auto t = Ampere::NativeProgramAddress(code, a)) return *t; throw RuntimeError("indirect branch to {:#x}", a); }
    bool IsNativeProgram() const override { return true; }
    bool IsCbufStorageBufferPointer(u32, u32) override { return true; }
    u32 ReadCbufValue(u32, u32) override { return 0; }
    TextureType ReadTextureType(u32) override { return TextureType::Color2D; }
    TexturePixelFormat ReadTexturePixelFormat(u32) override { return TexturePixelFormat{}; }
    bool IsTexturePixelFormatInteger(u32) override { return false; }
    u32 ReadViewportTransformState() override { return 1; }
    u32 TextureBoundBuffer() const override { return tb; }
    u32 LocalMemorySize() const override { return local; }
    u32 SharedMemorySize() const override { return 0; }
    std::array<u32, 3> WorkgroupSize() const override { return {}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<ReplaceConstant> GetReplaceConstBuffer(u32, u32) override { return std::nullopt; }
    void Dump(u64, u64) override {}
private:
    std::vector<u64> code; u32 tb = 2, local = 0;
};

Profile radv_profile() {
    Profile p{};
    p.supported_spirv = 0x00010400; p.unified_descriptor_binding = true; p.support_descriptor_aliasing = false;
    p.support_int8 = p.support_int16 = p.support_int64 = true;
    p.support_uniform_and_storage_buffer_8bit = p.support_storage_buffer_8bit = true;
    p.support_uniform_and_storage_buffer_16bit = p.support_storage_buffer_16bit = true;
    p.support_float_controls = false; p.support_vote = true; p.support_shuffle_relative = true;
    p.supported_subgroup_stages = ~0u; p.support_typeless_image_loads = true; p.support_demote_to_helper_invocation = true;
    p.support_int64_atomics = true; p.support_derivative_control = true; p.support_multi_viewport = true;
    p.support_viewport_index_layer_non_geometry = true; p.support_explicit_workgroup_layout = false;
    p.has_broken_spirv_clamp = false; p.lower_left_origin_mode = false;
    return p;
}
}

int main(int argc, char **argv) {
    if (argc != 5) { std::fprintf(stderr, "usage: meshxlate key.ctl key.code out.spv out.json\n"); return 2; }
    try {
        const auto ctl = read_file(argv[1]), bytes = read_file(argv[2]);
        if (ctl.size() < 0xe0 || bytes.size() < 0x100) throw RuntimeError("a short dump");
        const u32 kind = r32(ctl, 0x54);
        if (kind != 0x60000 && kind != 0x70000) throw RuntimeError("not a task or mesh program ({:#x})", kind);
        const Stage st = kind == 0x60000 ? Stage::Task : Stage::Mesh;
        Env env(ctl, bytes, st);
        ObjectPool<Maxwell::Flow::Block> flow; ObjectPool<IR::Inst> inst; ObjectPool<IR::Block> block;
        Maxwell::Flow::CFG cfg(env, flow, static_cast<u32>(sizeof(ProgramHeader)));
        HostTranslateInfo host{};
        host.support_float64 = true; host.support_float16 = true; host.support_int64 = true; host.needs_demote_reorder = false; host.support_snorm_render_buffer = true;
        host.support_viewport_index_layer = true; host.min_ssbo_alignment = 16; host.support_geometry_shader_passthrough = false; host.support_conditional_barrier = true;
        IR::Program program = Maxwell::TranslateProgram(inst, block, env, cfg, host);
        RuntimeInfo rt{};
        Backend::Bindings bind{};
        const std::vector<u32> spv = Backend::SPIRV::EmitSPIRV(radv_profile(), rt, program, bind);
        { std::ofstream f(argv[3], std::ios::binary); f.write(reinterpret_cast<const char *>(spv.data()), static_cast<std::streamsize>(spv.size() * 4)); }
        const Info &in = program.info;
        std::string j = fmt::format("{{\"stage\":\"{}\",\"entry\":\"main\",\"local_size\":[{},{},{}],\"bindings\":[", st == Stage::Task ? "ts" : "ms", program.workgroup_size[0], program.workgroup_size[1], program.workgroup_size[2]);
        u32 b = 0; bool first = true;
        auto add = [&](const std::string &desc, const std::string &nvn) { j += fmt::format("{}{{\"set\":0,\"binding\":{},\"descriptor\":\"{}\",\"count\":1,\"nvn\":{{{}}}}}", first ? "" : ",", b++, desc, nvn); first = false; };
        // constant banks as nxs_refl.py maps them: 3..16 are NVN's uniform slots 0..13, 1 the program's constants, 0 and 2 the driver's banks
        std::string drv;
        for (const auto &d : in.constant_buffer_descriptors) {
            if (d.index >= 3 && d.index <= 16) add("uniform_buffer", fmt::format("\"kind\":\"cbuf\",\"reg\":{}", d.index - 3));
            else if (d.index == 1) add("uniform_buffer", "\"kind\":\"c1\",\"bank\":1");
            else { add("uniform_buffer", fmt::format("\"kind\":\"drvcb\",\"bank\":{}", d.index)); drv += fmt::format("{}\"{}\":{{}}", drv.empty() ? "" : ",", d.index); }
        }
        const u32 base = st == Stage::Task ? 0x610 : 0x710;   // the storage slots' rows in driver bank 0 (16 bytes each; Mario Kart World's programs read rows 6 and 7)
        for (const auto &d : in.storage_buffers_descriptors) add("storage_buffer", fmt::format("\"kind\":\"sbuf\",\"reg\":{},\"rw\":{},\"bank\":{},\"c0_offset\":{}", (d.cbuf_offset - base) / 0x10, d.is_written ? "true" : "false", d.cbuf_index, d.cbuf_offset));
        for (const auto &d : in.texture_buffer_descriptors) add("uniform_texel_buffer", fmt::format("\"kind\":\"tbuf\",\"reg\":{}", d.cbuf_offset));
        for (const auto &d : in.image_buffer_descriptors) add("storage_texel_buffer", fmt::format("\"kind\":\"img\",\"reg\":{}", d.cbuf_offset));
        for (const auto &d : in.texture_descriptors) add("combined_image_sampler", fmt::format("\"kind\":\"tex\",\"reg\":{},\"bank\":{}", d.cbuf_offset, d.cbuf_index));
        for (const auto &d : in.image_descriptors) add("storage_image", fmt::format("\"kind\":\"img\",\"reg\":{},\"bank\":{}", d.cbuf_offset, d.cbuf_index));
        j += "],\"drv_reads\":{" + drv + "}";
        j += fmt::format(",\"mesh\":{{\"max_vertices\":{},\"max_primitives\":{},\"payload_words\":{},\"vertex_lines\":{},\"primitive_lines\":{}}}}}\n", program.mesh.max_vertices, program.mesh.max_primitives, program.mesh.payload_words, program.mesh.vertex_lines.size(), program.mesh.primitive_lines.size());
        std::ofstream(argv[4]) << j;
        std::printf("OK %s %zu words, %u bindings\n", st == Stage::Task ? "task" : "mesh", spv.size(), b);
        return 0;
    } catch (const std::exception &e) { std::fprintf(stderr, "meshxlate: %s\n", e.what()); return 4; }
}
