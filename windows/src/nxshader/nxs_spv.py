"""A small SPIR-V module model (words in, words out) and the Vulkan edits nxshader makes to the translator's SPIR-V. Written from the SPIR-V 1.6 specification
(Khronos, the binary form and the instruction table); the edits mirror what nxnative's Metal chain did to its MSL where Vulkan needs an equivalent
(docs/m5-record-shaders.md, "The SPIR-V edits"). Every pass has a name and a version: both go into the cache key of what it produces (nxs_cache.py)."""
import struct

MAGIC = 0x07230203
# opcodes used here (SPIR-V specification, 3.52)
OpName, OpMemberName, OpExtension, OpExtInstImport, OpMemoryModel, OpEntryPoint, OpExecutionMode, OpCapability = 5, 6, 10, 11, 14, 15, 16, 17
OpTypeVoid, OpTypeBool, OpTypeInt, OpTypeFloat, OpTypeVector, OpTypeMatrix, OpTypeImage, OpTypeSampler, OpTypeSampledImage = 19, 20, 21, 22, 23, 24, 25, 26, 27
OpTypeArray, OpTypeRuntimeArray, OpTypeStruct, OpTypePointer, OpTypeFunction = 28, 29, 30, 32, 33
OpConstantTrue, OpConstantFalse, OpConstant, OpConstantComposite, OpConstantNull = 41, 42, 43, 44, 46
OpFunction, OpFunctionParameter, OpFunctionEnd, OpVariable, OpLoad, OpStore, OpAccessChain, OpInBoundsAccessChain = 54, 55, 56, 59, 61, 62, 65, 66
OpDecorate, OpMemberDecorate, OpCompositeConstruct, OpCompositeExtract, OpVectorShuffle, OpCopyObject = 71, 72, 80, 81, 79, 83
OpImageRead, OpImageWrite, OpBitcast, OpFAdd, OpFMul, OpLabel, OpReturn, OpReturnValue, OpEmitVertex, OpEmitStreamVertex = 98, 99, 124, 129, 133, 248, 253, 254, 218, 220
OpExecutionModeId, OpModuleProcessed = 331, 330
# decorations, builtins, storage classes, capabilities, execution models
DecBuiltIn, DecLocation, DecComponent, DecIndex, DecBinding, DecDescriptorSet, DecBlock, DecFlat = 11, 30, 31, 32, 33, 34, 2, 14
BuiltInPosition, BuiltInPointSize, BuiltInLayer, BuiltInBaseInstance = 0, 1, 9, 4425
SC_UniformConstant, SC_Input, SC_Uniform, SC_Output, SC_Workgroup, SC_Private, SC_Function, SC_PushConstant, SC_StorageBuffer = 0, 1, 2, 3, 4, 6, 7, 9, 12
EXEC_MODELS = {0: 'vertex', 1: 'tess_control', 2: 'tess_eval', 3: 'geometry', 4: 'fragment', 5: 'compute'}
# capability names (spirv.h of the Vulkan SDK 1.4.363, the ones these modules can carry)
CAP_NAMES = {0: 'Matrix', 1: 'Shader', 2: 'Geometry', 3: 'Tessellation', 4: 'Addresses', 5: 'Linkage', 6: 'Kernel', 7: 'Vector16', 8: 'Float16Buffer', 9: 'Float16', 10: 'Float64',
             11: 'Int64', 12: 'Int64Atomics', 13: 'ImageBasic', 14: 'ImageReadWrite', 15: 'ImageMipmap', 17: 'Pipes', 18: 'Groups', 19: 'DeviceEnqueue', 20: 'LiteralSampler', 21:
             'AtomicStorage', 22: 'Int16', 23: 'TessellationPointSize', 24: 'GeometryPointSize', 25: 'ImageGatherExtended', 27: 'StorageImageMultisample', 28:
             'UniformBufferArrayDynamicIndexing', 29: 'SampledImageArrayDynamicIndexing', 30: 'StorageBufferArrayDynamicIndexing', 31: 'StorageImageArrayDynamicIndexing', 32:
             'ClipDistance', 33: 'CullDistance', 34: 'ImageCubeArray', 35: 'SampleRateShading', 36: 'ImageRect', 37: 'SampledRect', 38: 'GenericPointer', 39: 'Int8', 40:
             'InputAttachment', 41: 'SparseResidency', 42: 'MinLod', 43: 'Sampled1D', 44: 'Image1D', 45: 'SampledCubeArray', 46: 'SampledBuffer', 47: 'ImageBuffer', 48:
             'ImageMSArray', 49: 'StorageImageExtendedFormats', 50: 'ImageQuery', 51: 'DerivativeControl', 52: 'InterpolationFunction', 53: 'TransformFeedback', 54:
             'GeometryStreams', 55: 'StorageImageReadWithoutFormat', 56: 'StorageImageWriteWithoutFormat', 57: 'MultiViewport', 58: 'SubgroupDispatch', 59: 'NamedBarrier', 60:
             'PipeStorage', 61: 'GroupNonUniform', 62: 'GroupNonUniformVote', 63: 'GroupNonUniformArithmetic', 64: 'GroupNonUniformBallot', 65: 'GroupNonUniformShuffle', 66:
             'GroupNonUniformShuffleRelative', 67: 'GroupNonUniformClustered', 68: 'GroupNonUniformQuad', 69: 'ShaderLayer', 70: 'ShaderViewportIndex', 71: 'UniformDecoration',
             4422: 'FragmentShadingRateKHR', 4423: 'SubgroupBallotKHR', 4427: 'DrawParameters', 4428: 'WorkgroupMemoryExplicitLayoutKHR', 4429:
             'WorkgroupMemoryExplicitLayout8BitAccessKHR', 4430: 'WorkgroupMemoryExplicitLayout16BitAccessKHR', 4431: 'SubgroupVoteKHR', 4433: 'StorageBuffer16BitAccess', 4434:
             'StorageUniform16', 4435: 'StoragePushConstant16', 4436: 'StorageInputOutput16', 4437: 'DeviceGroup', 4439: 'MultiView', 4441: 'VariablePointersStorageBuffer', 4442:
             'VariablePointers', 4445: 'AtomicStorageOps', 4447: 'SampleMaskPostDepthCoverage', 4448: 'StorageBuffer8BitAccess', 4449: 'UniformAndStorageBuffer8BitAccess', 4450:
             'StoragePushConstant8', 4464: 'DenormPreserve', 4465: 'DenormFlushToZero', 4466: 'SignedZeroInfNanPreserve', 4467: 'RoundingModeRTE', 4468: 'RoundingModeRTZ', 4471:
             'RayQueryProvisionalKHR', 4472: 'RayQueryKHR', 4473: 'UntypedPointersKHR', 4478: 'RayTraversalPrimitiveCullingKHR', 4479: 'RayTracingKHR', 4484:
             'TextureSampleWeightedQCOM', 4485: 'TextureBoxFilterQCOM', 4486: 'TextureBlockMatchQCOM', 4495: 'TileShadingQCOM', 4496: 'CooperativeMatrixConversionQCOM', 4498:
             'TextureBlockMatch2QCOM', 5009: 'ImageGatherBiasLodAMD', 5013: 'StencilExportEXT', 5015: 'ImageReadWriteLodAMD', 5016: 'Int64ImageEXT', 5249:
             'SampleMaskOverrideCoverageNV', 5251: 'GeometryShaderPassthroughNV', 5254: 'ShaderViewportIndexLayerEXT', 5255: 'ShaderViewportMaskNV', 5297:
             'GroupNonUniformPartitionedEXT', 5301: 'ShaderNonUniform', 5306: 'UniformBufferArrayNonUniformIndexing', 5345: 'VulkanMemoryModel', 5378:
             'FragmentShaderPixelInterlockEXT'}


def string_of(words):
    b = struct.pack(f'<{len(words)}I', *words)
    return b[:b.index(0)].decode('utf-8', 'replace') if 0 in b else b.decode('utf-8', 'replace')


def string_words(s):
    b = s.encode() + b'\0'
    b += b'\0' * (-len(b) % 4)
    return list(struct.unpack(f'<{len(b) // 4}I', b))


def string_len(words):   # words a nul-terminated literal string takes, from its start
    for i, w in enumerate(words):
        if any((w >> (8 * k)) & 0xff == 0 for k in range(4)): return i + 1
    return len(words)


class Module:
    def __init__(self, data):
        if len(data) % 4 or len(data) < 20: raise ValueError('not a SPIR-V module (size)')
        w = list(struct.unpack(f'<{len(data) // 4}I', data))
        if w[0] != MAGIC: raise ValueError('not a SPIR-V module (magic)')
        self.header = w[:5]
        self.ins = []
        i = 5
        while i < len(w):
            n = w[i] >> 16
            if n == 0 or i + n > len(w): raise ValueError(f'bad instruction length at word {i}')
            self.ins.append(w[i:i + n]); i += n
        self.reindex()

    def bytes(self):
        flat = self.header + [x for i in self.ins for x in i]
        return struct.pack(f'<{len(flat)}I', *flat)

    @property
    def version(self): return (self.header[1] >> 16 & 0xff, self.header[1] >> 8 & 0xff)

    def new_id(self):
        i = self.header[3]; self.header[3] += 1; return i

    def reindex(self):
        self.names, self.decor, self.member_decor, self.defs = {}, {}, {}, {}
        for k, x in enumerate(self.ins):
            op = x[0] & 0xffff
            if op == OpName: self.names[x[1]] = string_of(x[2:])
            elif op == OpDecorate: self.decor.setdefault(x[1], []).append((x[2], x[3:]))
            elif op == OpMemberDecorate: self.member_decor.setdefault(x[1], {}).setdefault(x[2], []).append((x[3], x[4:]))
            else:
                rid = result_id(x)
                if rid: self.defs[rid] = k

    def op(self, i): return self.ins[i][0] & 0xffff
    def inst(self, rid): return self.ins[self.defs[rid]] if rid in self.defs else None

    def deco(self, rid, d):
        for dd, args in self.decor.get(rid, ()):
            if dd == d: return args or [1]
        return None

    def caps(self): return [x[1] for x in self.ins if x[0] & 0xffff == OpCapability]
    def has_op(self, opcode): return any(x[0] & 0xffff == opcode for x in self.ins)

    def entry_points(self):
        out = []
        for k, x in enumerate(self.ins):
            if x[0] & 0xffff == OpEntryPoint:
                n = string_len(x[3:])
                out.append({'index': k, 'model': x[1], 'func': x[2], 'name': string_of(x[3:3 + n]), 'interface': x[3 + n:]})
        return out

    def type_desc(self, tid, depth=0):
        """a type as a small dict (recursive); pointers give their storage class and pointee"""
        x = self.inst(tid)
        if x is None or depth > 16: return {'t': '?'}
        op = x[0] & 0xffff
        if op == OpTypeVoid: return {'t': 'void'}
        if op == OpTypeBool: return {'t': 'bool'}
        if op == OpTypeInt: return {'t': 'int' if x[3] else 'uint', 'w': x[2]}
        if op == OpTypeFloat: return {'t': 'float', 'w': x[2]}
        if op == OpTypeVector: return {'t': 'vec', 'n': x[3], 'of': self.type_desc(x[2], depth + 1)}
        if op == OpTypeMatrix: return {'t': 'mat', 'n': x[3], 'of': self.type_desc(x[2], depth + 1)}
        if op == OpTypeArray: return {'t': 'array', 'n': self.const_value(x[3]), 'of': self.type_desc(x[2], depth + 1)}
        if op == OpTypeRuntimeArray: return {'t': 'array', 'n': None, 'of': self.type_desc(x[2], depth + 1)}
        if op == OpTypeStruct: return {'t': 'struct', 'id': tid, 'members': [self.type_desc(m, depth + 1) for m in x[2:]]}
        if op == OpTypePointer: return {'t': 'ptr', 'sc': x[2], 'of': self.type_desc(x[3], depth + 1)}
        if op == OpTypeImage: return {'t': 'image', 'sampled_type': self.type_desc(x[2], depth + 1), 'dim': x[3], 'depth': x[4], 'arrayed': x[5], 'ms': x[6], 'sampled': x[7], 'format': x[8]}
        if op == OpTypeSampler: return {'t': 'sampler'}
        if op == OpTypeSampledImage: return {'t': 'sampled_image', 'image': self.type_desc(x[2], depth + 1)}
        return {'t': 'op%d' % op}

    def const_value(self, cid):
        x = self.inst(cid)
        if x is None: return None
        op = x[0] & 0xffff
        if op == OpConstant: return x[3] if len(x) == 4 else x[3] | x[4] << 32
        return None

    def global_vars(self):
        """module-scope OpVariables: (id, storage class, pointer type id)"""
        out, in_func = [], False
        for x in self.ins:
            op = x[0] & 0xffff
            if op == OpFunction: in_func = True
            elif op == OpFunctionEnd: in_func = False
            elif op == OpVariable and not in_func: out.append((x[2], x[3], x[1]))
        return out

    # ---- building blocks for the edits ----
    def first_function_index(self):
        return next(k for k, x in enumerate(self.ins) if x[0] & 0xffff == OpFunction)

    def find_type(self, words_tail, op):
        """an existing type instruction with exactly these operands (after the result id), else None"""
        for x in self.ins:
            if x[0] & 0xffff == op and x[2:] == words_tail: return x[1]
        return None

    def ensure_type(self, op, tail):
        t = self.find_type(tail, op)
        if t: return t
        t = self.new_id(); k = self.first_function_index()
        self.ins.insert(self.types_end(), [(2 + len(tail)) << 16 | op, t] + tail); self.reindex(); return t

    def types_end(self):
        """where new types and constants go: after the last type, constant or global variable before the first function"""
        k = self.first_function_index()
        return k

    def ensure_const(self, type_id, value_words):
        for x in self.ins:
            if x[0] & 0xffff == OpConstant and x[1] == type_id and x[3:] == value_words: return x[2]
        c = self.new_id()
        self.ins.insert(self.types_end(), [(3 + len(value_words)) << 16 | OpConstant, type_id, c] + value_words); self.reindex(); return c

    def ensure_composite(self, type_id, parts):
        for x in self.ins:
            if x[0] & 0xffff == OpConstantComposite and x[1] == type_id and x[3:] == parts: return x[2]
        c = self.new_id()
        self.ins.insert(self.types_end(), [(3 + len(parts)) << 16 | OpConstantComposite, type_id, c] + parts); self.reindex(); return c

    def add_decoration(self, words):
        """an annotation (OpDecorate ...) at the end of the annotation section"""
        ann = [k for k, x in enumerate(self.ins) if x[0] & 0xffff in (71, 72, 73, 74, 332, 5632, 5633)]
        k = ann[-1] + 1 if ann else next(k for k, x in enumerate(self.ins) if x[0] & 0xffff in range(19, 47))
        self.ins.insert(k, words); self.reindex()

    def add_capability(self, cap):
        if cap in self.caps(): return False
        k = max(i for i, x in enumerate(self.ins) if x[0] & 0xffff == OpCapability) + 1
        self.ins.insert(k, [2 << 16 | OpCapability, cap]); self.reindex(); return True

    def function_body_start(self, func_id):
        """index of the first instruction of the function's first block after its OpLabel and leading OpVariables"""
        k = self.defs[func_id]
        while self.ins[k][0] & 0xffff != OpLabel: k += 1
        k += 1
        while self.ins[k][0] & 0xffff == OpVariable: k += 1
        return k

    def function_range(self, func_id):
        k = self.defs[func_id]; e = k
        while self.ins[e][0] & 0xffff != OpFunctionEnd: e += 1
        return k, e


def result_id(x):
    """the result id an instruction defines, or 0 (types and the instructions with a result type)"""
    op = x[0] & 0xffff
    if op in (OpTypeVoid, OpTypeBool, OpTypeInt, OpTypeFloat, OpTypeVector, OpTypeMatrix, OpTypeImage, OpTypeSampler, OpTypeSampledImage, OpTypeArray, OpTypeRuntimeArray,
              OpTypeStruct, 31, OpTypePointer, OpTypeFunction, 34, 35, 36, 37, 38, 39, OpLabel, OpExtInstImport, 322, 327, 5341):
        return x[1] if len(x) > 1 else 0
    if op in RESULT_TYPED and len(x) > 2: return x[2]
    return 0


# instructions with <result type> <result id> (the ones these modules use; the rest of the table only matters for ids we never look up)
RESULT_TYPED = set(range(41, 53)) | {1, 54, 55, 57, 59, 60, 61, 64, 65, 66, 67, 68, 69, 70, 73, 77, 78, 79, 80, 81, 82, 83, 84} | set(range(86, 106)) | set(range(109, 205)) | \
    set(range(207, 216)) | set(range(227, 243)) | {245, 246, 247, 251, 252} | set(range(262, 316)) | {12} | set(range(333, 367)) | {4421, 4424, 4429, 4430, 4432, 4433, 4434, 5249, 5250, 5251, 5252, 5253, 5296, 5297, 5333, 5334, 5335, 5336, 5337, 5338, 5339, 5340, 5380, 5381, 5383, 6086, 4450, 4453, 4454, 4455, 4456, 5000, 5001}


def float_bits(f): return struct.unpack('<I', struct.pack('<f', f))[0]


# ===================================================================================== the edits

class Pass:
    name, version, options = '', 1, ''
    def applies(self, m, stage): return False
    def run(self, m, stage): raise NotImplementedError
    def ident(self): return f'{self.name} v{self.version} {self.options}'.strip()


class VertexOutputInit(Pass):
    """research/01 lesson 3 (nxnative's vs_init in MSL): every user output of a vertex, tessellation-evaluation or geometry stage starts as (0,0,0,1), the value an
    unwritten attribute component reads on the hardware; the translator's outputs start undefined in Vulkan (RADV gives NaN, NVIDIA and AMD Windows 0). The stores
    go at the start of the entry function, before the translated body, which overwrites what it writes. A geometry shader is initialised once, at its start: after
    an EmitVertex the outputs are undefined again, and the translator's code writes every output it emits. Integer outputs get the bits of the same values
    (1.0f = 0x3f800000), as the hardware's untyped attribute slots hold them. A component-decorated output takes its part of the vector."""
    name, version = 'vout_init', 1

    def applies(self, m, stage): return stage in ('vs', 'gs', 'ds')

    def run(self, m, stage):
        ep = m.entry_points()[0]
        stores = []
        for vid, sc, ptr in m.global_vars():
            if sc != SC_Output or m.deco(vid, DecBuiltIn) is not None or m.deco(vid, DecLocation) is None: continue
            x = m.inst(vid)
            if len(x) > 4: continue   # has an initializer
            pointee = m.inst(ptr)[3]
            comp = (m.deco(vid, DecComponent) or [0])[0]
            c = self.value(m, pointee, comp)
            if c is not None: stores.append((vid, c))
        if not stores: return False
        k = m.function_body_start(ep['func'])
        m.ins[k:k] = [[3 << 16 | OpStore, v, c] for v, c in stores]
        m.reindex()
        return True

    def value(self, m, tid, comp):
        x = m.inst(tid); op = x[0] & 0xffff
        dflt = [0.0, 0.0, 0.0, 1.0]
        def scalar(st, i):
            s = m.inst(st); sop = s[0] & 0xffff
            v = dflt[i] if i < 4 else 0.0
            if sop == OpTypeFloat and s[2] == 32: return m.ensure_const(st, [float_bits(v)])
            if sop == OpTypeInt and s[2] == 32: return m.ensure_const(st, [float_bits(v)])
            return None
        if op in (OpTypeFloat, OpTypeInt): return scalar(tid, comp)
        if op == OpTypeVector:
            parts = [scalar(x[2], comp + i) for i in range(x[3])]
            return None if None in parts else m.ensure_composite(tid, parts)
        if op == OpTypeArray:
            e = self.value(m, x[2], comp)
            n = m.const_value(x[3])
            return None if e is None or not n or n > 64 else m.ensure_composite(tid, [e] * n)
        return None


class TrimCapabilities(Pass):
    """The translator declares capabilities a module never uses: Float64 and the other widths in modules without such a type, GeometryStreams without a stream,
    pixel interlock without an interlock. Vulkan requires the device feature for every declared capability before it creates the module (VkShaderModuleCreateInfo
    pCode-08740), and some GPUs lack them (no shaderFloat64 on Intel Arc, no geometryStreams without VK_EXT_transform_feedback, no fragment shader interlock on AMD).
    Removed when the module has nothing that needs it; a module that does use one keeps it, and its reflection says so"""
    name, version = 'caps_trim', 1
    WIDTH = {10: (OpTypeFloat, 64), 11: (OpTypeInt, 64), 9: (OpTypeFloat, 16), 22: (OpTypeInt, 16), 39: (OpTypeInt, 8)}
    KEEP_IF = {11: {12}}   # Int64Atomics implies Int64
    USES = {54: ({220, 221}, {29}, set()),                              # GeometryStreams: OpEmitStreamVertex / OpEndStreamPrimitive, the Stream decoration
            5378: ({5364, 5365}, set(), {5366, 5367, 5368, 5369, 5370, 5371})}   # FragmentShaderPixelInterlockEXT: Begin / EndInvocationInterlock, the interlock modes

    def applies(self, m, stage): return any(c in self.WIDTH or c in self.USES for c in m.caps())

    def run(self, m, stage):
        caps = set(m.caps()); drop = set()
        for c, (op, w) in self.WIDTH.items():
            if c in caps and not (self.KEEP_IF.get(c, set()) & caps) and not any(x[0] & 0xffff == op and x[2] == w for x in m.ins): drop.add(c)
        for c, (ops, decos, modes) in self.USES.items():
            if c in caps and not any(x[0] & 0xffff in ops or (x[0] & 0xffff == OpDecorate and x[2] in decos) or (x[0] & 0xffff == OpExecutionMode and x[2] in modes) for x in m.ins): drop.add(c)
        if not drop: return False
        m.ins = [x for x in m.ins if not (x[0] & 0xffff == OpCapability and x[1] in drop)]; m.reindex()
        return True


class StorageFormatCaps(Pass):
    """Reading or writing a storage image declared without a format needs StorageImageReadWithoutFormat / WriteWithoutFormat in Vulkan (Metal needed nothing):
    the translator declares Unknown for an image the answer table gives no format. The capabilities are added where the module does it"""
    name, version = 'storage_fmt_caps', 1

    def applies(self, m, stage):
        return any(x[0] & 0xffff == OpTypeImage and x[7] == 2 and x[8] == 0 for x in m.ins)

    def run(self, m, stage):
        ch = False
        if m.has_op(OpImageRead): ch |= m.add_capability(55)    # StorageImageReadWithoutFormat
        if m.has_op(OpImageWrite): ch |= m.add_capability(56)   # StorageImageWriteWithoutFormat
        return ch


class DepthGL(Pass):
    """Variant .gl: NVN's default depth convention (near = -w, the GL clip space) for a device without VK_EXT_depth_clip_control: z' = (z + w) / 2 before every
    return of the last pre-rasterisation stage (nxnative's main0 entry point in MSL, vs_depth). Pokopia sets near = 0 (nvnDeviceSetDepthMode), Vulkan's own convention,
    so its renderer needs no variant; the pass is here for the convention's other value"""
    name, version = 'depth_gl', 1

    def applies(self, m, stage): return stage in ('vs', 'gs', 'ds')

    def run(self, m, stage):
        pos = self.position(m)
        if not pos: return False
        var, chain = pos
        f32 = m.ensure_type(OpTypeFloat, [32])
        u32 = m.ensure_type(OpTypeInt, [32, 0])
        pf = m.ensure_type(OpTypePointer, [SC_Output, f32])
        half = m.ensure_const(f32, [float_bits(0.5)])
        idx = [m.ensure_const(u32, [i]) for i in chain]
        i2, i3 = m.ensure_const(u32, [2]), m.ensure_const(u32, [3])
        ep = m.entry_points()[0]
        lo, hi = m.function_range(ep['func'])
        stop = (OpEmitVertex, OpEmitStreamVertex) if stage == 'gs' else (OpReturn,)
        k, n = lo, 0
        while k < hi:
            if m.ins[k][0] & 0xffff in stop:
                pz, pw, z, w, s, h = (m.new_id() for _ in range(6))
                seq = [[(4 + len(idx) + 1) << 16 | OpAccessChain, pf, pz, var] + idx + [i2], [(4 + len(idx) + 1) << 16 | OpAccessChain, pf, pw, var] + idx + [i3],
                       [4 << 16 | OpLoad, f32, z, pz], [4 << 16 | OpLoad, f32, w, pw], [5 << 16 | OpFAdd, f32, s, z, w], [5 << 16 | OpFMul, f32, h, s, half], [3 << 16 | OpStore, pz, h]]
                m.ins[k:k] = seq; k += len(seq); hi += len(seq); n += 1
            k += 1
        m.reindex()
        return n > 0

    @staticmethod
    def position(m):
        """(variable, member indices of the vec4) of the Position output: a gl_PerVertex block member or a variable of its own"""
        for vid, sc, ptr in m.global_vars():
            if sc != SC_Output: continue
            if (m.deco(vid, DecBuiltIn) or [None])[0] == BuiltInPosition: return vid, []
            st = m.inst(m.inst(ptr)[3])
            if st[0] & 0xffff == OpTypeStruct:
                for mi, ds in m.member_decor.get(st[1], {}).items():
                    if any(d == DecBuiltIn and a and a[0] == BuiltInPosition for d, a in ds): return vid, [mi]
        return None


class PadOutputs(Pass):
    """Variant .pad<mask>: outputs at the given locations that the paired fragment shader reads but this vertex shader does not write, (0,0,0,1) (nxnative's vs_pad).
    Vulkan links such a pair (the input reads an undefined value); the renderer asks for the variant when a pipeline's pair needs it (the reflection names both sides' locations)"""
    name, version = 'pad_outputs', 1

    def __init__(self, locations): self.locations = sorted(set(locations)); self.options = 'locations=' + ','.join(map(str, self.locations))

    def applies(self, m, stage): return stage in ('vs', 'gs', 'ds') and bool(self.locations)

    def run(self, m, stage):
        have = {(m.deco(v, DecLocation) or [None])[0] for v, sc, _ in m.global_vars() if sc == SC_Output}
        miss = [l for l in self.locations if l not in have]
        if not miss: return False
        f32 = m.ensure_type(OpTypeFloat, [32]); v4 = m.ensure_type(OpTypeVector, [f32, 4]); pv = m.ensure_type(OpTypePointer, [SC_Output, v4])
        c = VertexOutputInit().value(m, v4, 0)
        ep = m.entry_points()[0]
        new = []
        for l in miss:
            v = m.new_id(); new.append(v)
            k = m.first_function_index(); m.ins.insert(k, [4 << 16 | OpVariable, pv, v, SC_Output])
            m.add_decoration([4 << 16 | OpDecorate, v, DecLocation, l])
        m.reindex()
        epi = m.entry_points()[0]['index']; x = m.ins[epi]; m.ins[epi] = [(len(x) + len(new)) << 16 | OpEntryPoint] + x[1:] + new
        k = m.function_body_start(ep['func'])
        m.ins[k:k] = [[3 << 16 | OpStore, v, c] for v in new]
        m.reindex()
        return True


class DualSource(Pass):
    """Variant .dual: NVN's SRC1 blend factors read the pixel shader's output 1 as target 0's second source (Maxwell feeds MRT 1 to it); Vulkan reads it from
    Location 0 Index 1 (nxnative's fs_dual for Metal's [[color(0), index(1)]]). For a shader that writes exactly outputs 0 and 1"""
    name, version = 'dual_source', 1

    def applies(self, m, stage): return stage == 'ps'

    def run(self, m, stage):
        outs = {(m.deco(v, DecLocation) or [None])[0]: v for v, sc, _ in m.global_vars() if sc == SC_Output and m.deco(v, DecBuiltIn) is None}
        if sorted(k for k in outs if k is not None) != [0, 1]: return False
        v1 = outs[1]
        for x in m.ins:
            if x[0] & 0xffff == OpDecorate and x[1] == v1 and x[2] == DecLocation: x[3] = 0
        k = next(i for i, x in enumerate(m.ins) if x[0] & 0xffff == OpDecorate and x[1] == v1 and x[2] == DecLocation)
        m.ins.insert(k + 1, [4 << 16 | OpDecorate, v1, DecIndex, 1]); m.reindex()
        return True


class Fetch1DTexelBuffer(Pass):
    """The translator reads a 1D integer fetch (texelFetch of a buffer texture) as a 2D fetch at (x & 16383, x >> 14) of a combined image sampler, for Metal,
    whose host binds a texture view of the buffer with rows of 16384 texels there (nvn_mtl.m tbuf_as_2d). Vulkan cannot view a buffer as an image: such a
    binding becomes a uniform texel buffer and each fetch reads element x + (y << 14) of it, the same element. The bindings are the translator's fetch1d slots
    (its res.json, given to the module as m.fetch1d_vars before the passes run); a variable used any other way than load → OpImage → OpImageFetch is left as it is"""
    name, version = 'fetch1d_tbuf', 1
    OpImageFetch, OpImage, OpShiftLeftLogical, OpBitwiseOr = 95, 100, 196, 197

    def applies(self, m, stage): return bool(getattr(m, 'fetch1d_vars', None))

    def uses(self, m, rid):   # instructions that take rid as an operand (debug, name, decoration and constant literals are not operands)
        return [k for k, x in enumerate(m.ins) if x[0] & 0xffff not in (3, 5, 6, 7, 8, 43, 71, 72) and rid in x[1:] and result_id(x) != rid]

    def run(self, m, stage):
        changed = False
        for v in sorted(m.fetch1d_vars):
            var = m.inst(v)
            if not var or var[0] & 0xffff != OpVariable: continue
            si = m.inst(m.inst(var[1])[3])
            if si[0] & 0xffff != OpTypeSampledImage: continue
            img = m.inst(si[2])
            if img[3] != 1 or img[5] or img[6]: continue   # 2D, not arrayed, not multisampled
            loads = [k for k, x in enumerate(m.ins) if x[0] & 0xffff == OpLoad and x[3] == v]
            ok, plan = True, []
            for k in loads:
                for u in self.uses(m, m.ins[k][2]):
                    x = m.ins[u]
                    if x[0] & 0xffff != self.OpImage or x[3] != m.ins[k][2]: ok = False; break
                    for f in self.uses(m, x[2]):
                        y = m.ins[f]
                        if y[0] & 0xffff != self.OpImageFetch or y[3] != x[2] or (len(y) > 5 and y[5] != 2) or len(y) > 7: ok = False; break
                        plan.append(f)
                    plan.append(u)
                if not ok: break
            if not ok or not loads: continue
            fetches = [m.ins[f] for f in plan if m.ins[f][0] & 0xffff == self.OpImageFetch]   # taken now: the insertions below move indices
            tb = m.ensure_type(OpTypeImage, [img[2], 5, 0, 0, 0, 1, img[8]])   # Dim Buffer, sampled
            ptb = m.ensure_type(OpTypePointer, [SC_UniformConstant, tb])
            # the coordinate's component type (int or uint) for the index arithmetic
            ctype = None
            for y in fetches:
                c = m.inst(y[4]); vt = m.inst(c[1]) if c else None
                if vt and vt[0] & 0xffff == OpTypeVector and vt[3] == 2: ctype = vt[2]; break
            if fetches and ctype is None: continue
            c14 = m.ensure_const(ctype, [14]) if ctype else None
            for t in (tb, ptb):   # new types go after the module's globals (ensure_type): the variable needs them before it
                if m.defs[t] > m.defs[v]: x = m.ins.pop(m.defs[t]); m.ins.insert(m.defs[v], x); m.reindex()
            var = m.inst(v); var[1] = ptb
            for k in [k for k, x in enumerate(m.ins) if x[0] & 0xffff == OpLoad and x[3] == v]: m.ins[k][1] = tb
            for k, x in enumerate(m.ins):
                if x[0] & 0xffff == self.OpImage and m.inst(x[3]) is not None and m.inst(x[3])[0] & 0xffff == OpLoad and m.inst(x[3])[3] == v:
                    m.ins[k] = [4 << 16 | OpCopyObject, tb, x[2], x[3]]
            out = []
            fetch_ids = {y[2] for y in fetches}
            for x in m.ins:
                if x[0] & 0xffff == self.OpImageFetch and x[2] in fetch_ids:
                    e0, e1, sh, idx = m.new_id(), m.new_id(), m.new_id(), m.new_id()
                    out += [[5 << 16 | OpCompositeExtract, ctype, e0, x[4], 0], [5 << 16 | OpCompositeExtract, ctype, e1, x[4], 1],
                            [5 << 16 | self.OpShiftLeftLogical, ctype, sh, e1, c14], [5 << 16 | self.OpBitwiseOr, ctype, idx, e0, sh],
                            [5 << 16 | self.OpImageFetch, x[1], x[2], x[3], idx]]
                else: out.append(x)
            m.ins = out; m.reindex(); changed = True
        return changed


DEFAULT_PASSES = [TrimCapabilities(), StorageFormatCaps(), VertexOutputInit(), Fetch1DTexelBuffer()]
VARIANTS = {'gl': lambda opts: [DepthGL()], 'dual': lambda opts: [DualSource()], 'pad': lambda opts: [PadOutputs(opts)]}


# ===================================================================================== analysis

def int_only_inputs(m):
    """Vertex inputs used only as integers (every use of a loaded value is a bit cast to an integer type, possibly through extracts, shuffles and copies; a store
    of the value somewhere is neutral): {location: 'uint'}. nxnative's int_inputs did this on the MSL; here on the SPIR-V after spirv-opt's SSA rewrite (a copy
    made for the analysis only). The translator declares every attribute float without an answer, and Vulkan then converts an integer vertex format to float
    values instead of passing its bits (bone indices: skinned characters stretched into streaks)"""
    uses, in_func = {}, False   # uses inside functions only: annotations and types hold literals that look like ids
    for x in m.ins:
        op = x[0] & 0xffff
        if op == OpFunction: in_func = True
        if not in_func: continue
        operands = x[3:] if op in RESULT_TYPED else x[1:]
        for o in operands:
            if o in m.defs: uses.setdefault(o, []).append(x)
    def classify(rid, seen):
        n_int, bad = 0, False
        for u in uses.get(rid, []):
            op = u[0] & 0xffff
            if op == OpBitcast:
                t = m.inst(u[1]); tt = m.inst(t[2]) if t and t[0] & 0xffff == OpTypeVector else t
                if tt and tt[0] & 0xffff == OpTypeInt: n_int += 1
                else: bad = True
            elif op in (OpCompositeExtract, OpVectorShuffle, OpCopyObject, OpCompositeConstruct) and u[2] not in seen:
                seen.add(u[2]); a, b = classify(u[2], seen); n_int += a; bad |= b
            elif op == OpStore and u[2] == rid: pass
            else: bad = True
            if bad: break
        return n_int, bad
    out = {}
    for vid, sc, ptr in m.global_vars():
        loc = m.deco(vid, DecLocation)
        if sc != SC_Input or loc is None: continue
        ptrs, loads = {vid}, []
        for x in m.ins:
            op = x[0] & 0xffff
            if op in (OpAccessChain, OpInBoundsAccessChain) and x[3] in ptrs: ptrs.add(x[2])
        for x in m.ins:
            if x[0] & 0xffff == OpLoad and x[3] in ptrs: loads.append(x[2])
        n_int, bad = 0, False
        for r in loads:
            a, b = classify(r, {r}); n_int += a; bad |= b
        if n_int and not bad: out[str(loc[0])] = 'uint'
    return out
