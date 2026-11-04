#!/usr/bin/env python3
"""nxshader's unit tests (no game data): the SPIR-V edits on small modules assembled here, each result accepted by spirv-val; the integer-input analysis; and
the cache's invalidation with a stand-in translator: a rerun hits, a new translator misses every shader, a new version of one edit misses only the shaders it
applies to, new facts for one shader miss only that shader, and an entry made by an older chain is stale for the runtime's lookup.
  test_nxshader.py            exit 0 = pass (77 when the Vulkan SDK's spirv-as / spirv-val are missing)"""
import json, os, shutil, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import nxs_spv as S, nxs_cache as C, nxs_front as F, nxshader as N

FAIL = []
def T(cond, what):
    if not cond: FAIL.append(what); print('FAIL', what)

VS = r'''
               OpCapability Shader
               OpCapability Float64
               OpCapability GeometryStreams
          %1 = OpExtInstImport "GLSL.std.450"
               OpMemoryModel Logical GLSL450
               OpEntryPoint Vertex %main "main" %pos_in %bits_in %o0 %o1a %o1b %oi %pv
               OpDecorate %pos_in Location 0
               OpDecorate %bits_in Location 1
               OpDecorate %o0 Location 0
               OpDecorate %o1a Location 1
               OpDecorate %o1a Component 0
               OpDecorate %o1b Location 1
               OpDecorate %o1b Component 2
               OpDecorate %oi Location 2
               OpDecorate %oi Flat
               OpMemberDecorate %PerVertex 0 BuiltIn Position
               OpMemberDecorate %PerVertex 1 BuiltIn PointSize
               OpDecorate %PerVertex Block
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
       %uint = OpTypeInt 32 0
        %int = OpTypeInt 32 1
       %v4f = OpTypeVector %float 4
       %v2f = OpTypeVector %float 2
       %v4u = OpTypeVector %uint 4
 %PerVertex = OpTypeStruct %v4f %float
  %p_out_pv = OpTypePointer Output %PerVertex
  %p_in_v4f = OpTypePointer Input %v4f
 %p_out_v4f = OpTypePointer Output %v4f
 %p_out_v2f = OpTypePointer Output %v2f
 %p_out_v4u = OpTypePointer Output %v4u
     %p_in_f = OpTypePointer Input %float
     %int_0 = OpConstant %int 0
    %uint_1 = OpConstant %uint 1
     %pos_in = OpVariable %p_in_v4f Input
    %bits_in = OpVariable %p_in_v4f Input
         %o0 = OpVariable %p_out_v4f Output
        %o1a = OpVariable %p_out_v2f Output
        %o1b = OpVariable %p_out_v2f Output
         %oi = OpVariable %p_out_v4u Output
         %pv = OpVariable %p_out_pv Output
       %main = OpFunction %void None %fn
      %entry = OpLabel
          %p = OpLoad %v4f %pos_in
         %pp = OpAccessChain %p_out_v4f %pv %int_0
               OpStore %pp %p
         %bc = OpAccessChain %p_in_f %bits_in %uint_1
          %b = OpLoad %float %bc
         %bu = OpBitcast %uint %b
         %bv = OpCompositeConstruct %v4u %bu %bu %bu %bu
               OpStore %oi %bv
         %xy = OpVectorShuffle %v2f %p %p 0 1
               OpStore %o1a %xy
               OpReturn
               OpFunctionEnd
'''

FS = r'''
               OpCapability Shader
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %c0 %c1
               OpExecutionMode %main OriginUpperLeft
               OpDecorate %c0 Location 0
               OpDecorate %c1 Location 1
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
       %v4f = OpTypeVector %float 4
 %p_out_v4f = OpTypePointer Output %v4f
     %f_one = OpConstant %float 1
      %white = OpConstantComposite %v4f %f_one %f_one %f_one %f_one
         %c0 = OpVariable %p_out_v4f Output
         %c1 = OpVariable %p_out_v4f Output
       %main = OpFunction %void None %fn
      %entry = OpLabel
               OpStore %c0 %white
               OpStore %c1 %white
               OpReturn
               OpFunctionEnd
'''

CS = r'''
               OpCapability Shader
               OpMemoryModel Logical GLSL450
               OpEntryPoint GLCompute %main "main" %img
               OpExecutionMode %main LocalSize 8 8 1
               OpName %img "cp_i_tcb_12_"
               OpDecorate %img DescriptorSet 0
               OpDecorate %img Binding 1
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
        %int = OpTypeInt 32 1
       %v4f = OpTypeVector %float 4
       %v2i = OpTypeVector %int 2
       %im = OpTypeImage %float 2D 0 0 0 2 Unknown
      %p_im = OpTypePointer UniformConstant %im
      %int_0 = OpConstant %int 0
      %coord = OpConstantComposite %v2i %int_0 %int_0
       %img = OpVariable %p_im UniformConstant
       %main = OpFunction %void None %fn
      %entry = OpLabel
          %i = OpLoad %im %img
          %v = OpImageRead %v4f %i %coord
               OpImageWrite %i %coord %v
               OpReturn
               OpFunctionEnd
'''


def tool(n): return N.vk_tool(n)


def assemble(text, w, name):
    s, o = os.path.join(w, name + '.spvasm'), os.path.join(w, name + '.spv')
    open(s, 'w').write(text)
    r = subprocess.run([tool('spirv-as'), '--target-env', 'vulkan1.3', s, '-o', o], capture_output=True, text=True)
    if r.returncode: raise RuntimeError('spirv-as: ' + r.stderr)
    return open(o, 'rb').read()


def val(spv, w, name):
    p = os.path.join(w, name + '.val.spv'); open(p, 'wb').write(spv)
    return N.spirv_val(p)


def test_edits(w):
    vs = assemble(VS, w, 'vs'); fs = assemble(FS, w, 'fs'); cs = assemble(CS, w, 'cs')
    T(val(vs, w, 'vs0') == 'ok', 'the test vertex shader is valid as written')
    m = S.Module(vs)
    applied = [p.ident() for p in S.DEFAULT_PASSES if p.applies(m, 'vs') and p.run(m, 'vs')]
    T(applied == ['caps_trim v1', 'vout_init v1'], f'edits on the vertex shader: {applied}')
    T(10 not in m.caps() and 54 not in m.caps(), 'Float64 and GeometryStreams without a use are trimmed')
    out = m.bytes(); T(val(out, w, 'vs1') == 'ok', 'the edited vertex shader is valid: ' + val(out, w, 'vs1'))
    ep = m.entry_points()[0]; k = m.function_body_start(ep['func'])
    stores = []
    while m.ins[k + len(stores)][0] & 0xffff == S.OpStore: stores.append(m.ins[k + len(stores)])
    T(len(stores) == 4, f'one (0,0,0,1) store per user output at the entry: {len(stores)}')
    # the values: vec4 (0,0,0,1); the component-2 vec2 gets (0,1); the integer output the bits of 1.0f
    vals = {}
    for st in stores:
        c = m.inst(st[2]); vals[st[1]] = [m.inst(p)[3] for p in c[3:]] if c[0] & 0xffff == S.OpConstantComposite else None
    byloc = {(m.deco(v, S.DecLocation)[0], (m.deco(v, S.DecComponent) or [0])[0]): vv for v, vv in vals.items()}
    T(byloc.get((0, 0)) == [0, 0, 0, S.float_bits(1.0)], f'vec4 output starts (0,0,0,1): {byloc.get((0, 0))}')
    T(byloc.get((1, 2)) == [0, S.float_bits(1.0)], f'a component-2 vec2 takes z, w: {byloc.get((1, 2))}')
    T(byloc.get((2, 0)) == [0, 0, 0, 0x3f800000], f'an integer output gets the bits of 1.0f: {byloc.get((2, 0))}')
    # variants
    g = S.Module(out); T(S.DepthGL().run(g, 'vs') and val(g.bytes(), w, 'gl') == 'ok', 'depth_gl variant is valid')
    T(any(x[0] & 0xffff == S.OpFMul for x in g.ins), 'depth_gl rewrites z')
    pmod = S.Module(out); T(S.PadOutputs([5, 7]).run(pmod, 'vs') and val(pmod.bytes(), w, 'pad') == 'ok', 'pad_outputs variant is valid')
    T({(pmod.deco(v, S.DecLocation) or [None])[0] for v, sc, _ in pmod.global_vars() if sc == S.SC_Output} >= {5, 7}, 'pad_outputs adds locations 5 and 7')
    f = S.Module(fs); T(S.DualSource().run(f, 'ps') and val(f.bytes(), w, 'dual') == 'ok', 'dual_source variant is valid')
    T(any(x[0] & 0xffff == S.OpDecorate and x[2] == S.DecIndex and x[3] == 1 for x in f.ins), 'output 1 becomes location 0 index 1')
    c = S.Module(cs)
    T(val(cs, w, 'cs0') != 'ok', 'reading an image without a format is invalid without the capability')
    T(S.StorageFormatCaps().run(c, 'cs') and val(c.bytes(), w, 'cs1') == 'ok', 'storage_fmt_caps makes it valid')
    T(55 in c.caps() and 56 in c.caps(), 'StorageImageRead/WriteWithoutFormat added')
    # the integer-input analysis (on the SSA form, as nxshader runs it)
    a = N.ssa_copy(vs, w, 'an')
    T(a is not None and S.int_only_inputs(a) == {'1': 'uint'}, f'location 1 is used only as integer bits: {S.int_only_inputs(a) if a else None}')
    return vs, fs, cs


class FakeTranslator:
    """the translator's interface; writes the test modules by stage"""
    def __init__(self, mods, ident): self.mods, self.id, self.runs = mods, ident, 0
    def run(self, jobs, w, low_priority=True):
        self.runs += len(jobs)
        for j in jobs:
            open(os.path.join(w, j['key'] + '.spv'), 'wb').write(self.mods[j['stage']])
            json.dump({'key': j['key'], 'stage': j['stage'], 'notes': [], 'cbufs': [], 'sbufs': [], 'textures': [], 'images': []}, open(os.path.join(w, j['key'] + '.res.json'), 'w'))
        return {}


def record(code):
    ctl = bytearray(0x880); ctl[0:4] = F.CTL_MAGIC; struct.pack_into('<I', ctl, 0x20, 0x880); struct.pack_into('<2I', ctl, 0x700, len(code), len(code))
    rec = bytearray(b'BTCDBTCD' + bytes(0x30)); struct.pack_into('<2I', rec, 0x28, len(ctl), len(code))
    return bytes(rec + code + ctl)


class FakeCorpus:
    def __init__(self):
        self.recs = {'aaaa000000000001': ('vs_test.bin', record(bytes(0x80) + b'\x01' * 32)), 'aaaa000000000002': ('ps_test.bin', record(bytes(0x80) + b'\x02' * 32)),
                     'aaaa000000000003': ('ps_test.bin', record(bytes(0x80) + b'\x03' * 32))}
        self.facts, self.facts_raw, self.source = {}, {}, {}
    def keys(self): return sorted(self.recs)
    def record(self, k): return self.recs[k]


def test_cache(w, vs, fs):
    root = os.path.join(w, 'cache'); keys = FakeCorpus().keys()
    def build(xl, corpus=None, label=''):
        cache = C.Cache(root); wd = tempfile.mkdtemp(dir=w)
        b = N.Builder(corpus or FakeCorpus(), cache, xl, wd, validate=True)
        cache.set_chain(b.chain)
        res = b.make(keys)
        T(all(r['status'] == 'ok' and r['spirv_val'] == 'ok' for r in res.values()), f'{label}: every shader builds and validates: {[(r["key"], r["status"], r.get("spirv_val")) for r in res.values()]}')
        return cache.stats, b
    mods = {'vs': vs, 'ps': fs}
    st, b1 = build(FakeTranslator(mods, 'xlate A'), label='first build')
    T(st['xlate miss'] == 3 and st['final miss'] == 3, f'first build misses everything: {st}')
    st, _ = build(FakeTranslator(mods, 'xlate A'), label='rerun')
    T(st['xlate hit'] == 3 and st['final hit'] == 3, f'a rerun hits everything: {st}')
    st, _ = build(FakeTranslator(mods, 'xlate B'), label='new translator')
    T(st['xlate miss'] == 3, f'a new translator misses every shader: {st}')
    old = S.VertexOutputInit.version
    try:
        S.VertexOutputInit.version = old + 1
        st, b2 = build(FakeTranslator(mods, 'xlate B'), label='new vout_init version')
        T(st['xlate hit'] == 3 and st['final miss'] == 1 and st['final hit'] == 2, f'a new version of the vertex-output edit misses only the vertex shader: {st}')
        T(b2.chain != b1.chain, 'the chain id follows the edit versions')
        stale = C.Cache(root)
        T(stale.lookup('aaaa000000000001', chain=b1.chain) is None and stale.lookup('aaaa000000000001') is not None, 'an entry made by another chain is stale; the current one is found')
    finally: S.VertexOutputInit.version = old
    fc = FakeCorpus(); fc.facts['aaaa000000000002'] = {'fout': {'0': 'uint'}}
    st, _ = build(FakeTranslator(mods, 'xlate B'), fc, label='new facts')
    T(st['xlate miss'] == 1 and st['xlate hit'] == 2, f'new facts for one shader miss only that shader: {st}')
    # gc drops the older generations and keeps what the published shaders need (a vertex shader's first translation too): the rerun still hits everything
    n, freed = C.Cache(root).gc()
    T(n > 0 and freed > 0, f'gc removes the entries no published shader refers to: {n}')
    st, _ = build(FakeTranslator(mods, 'xlate B'), fc, label='after gc')
    T(st['xlate hit'] == 3 and st['final hit'] == 3, f'after gc a rerun hits everything: {st}')


def main():
    if not tool('spirv-as') or not tool('spirv-val') or not tool('spirv-opt'): print('the Vulkan SDK tools are missing: skipped'); return 77
    w = tempfile.mkdtemp(prefix='nxshader_test_')
    try:
        vs, fs, cs = test_edits(w)
        test_cache(w, vs, fs)
    finally: shutil.rmtree(w, ignore_errors=True)
    print('nxshader.unit: ' + ('FAILED' if FAIL else 'ok'))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
