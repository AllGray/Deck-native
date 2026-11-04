// Checks against a real dump: NXW_DUMP names an ExeFS folder (main, subsdk0, sdk, rtld, main.npdm); exit 77 (skipped) without it.
// Pokopia 2.0.1 must give the census's numbers (research/04), 1.0.5 those recorded in docs/m2-nxload.md; builds are told apart by
// main's build ID. Any other dump gets the structural checks only.
#include "check.hpp"
#include "nxload/ehframe.hpp"
#include "nxload/npdm.hpp"
#include "nxload/program.hpp"

#include <cstdlib>
#include <set>

using namespace nxload;

namespace {

using Counts = std::map<std::string, size_t>;

struct Expect {
    std::string name;
    uint32_t text, ro, data, bss;
    size_t dynsym, imports, imports_weak, exports, exports_weak;
    Counts rela, jmprel;
    size_t relr;
    size_t fdes, cies, lsda;
    Counts fdes_by_aug;
    size_t text_targets, text_targets_at_fde, svc_sites;
    Counts from;
    size_t unresolved_weak;
};

// sdk, subsdk0 and rtld are byte-identical in 2.0.1 and 1.0.5
const Expect sdk = {"sdk", 0x7b7bf0, 0x5f0864, 0x5f870, 0x1e7790, 27913, 8, 0, 27904, 4632, {{"ABS64", 9818}, {"GLOB_DAT", 1449}},
                    {{"JUMP_SLOT", 9683}}, 15662, 38283, 3, 574, {{"zRB", 20238}, {"zR", 17471}, {"zPLRB", 574}}, 10323, 10320, 57,
                    {{"main", 8}}, 0};
const Expect subsdk0 = {"subsdk0", 0xb0f60, 0x69664, 0x15010, 0xfff0, 1682, 357, 0, 1324, 210,
                        {{"RELATIVE", 689}, {"ABS64", 610}, {"GLOB_DAT", 104}}, {{"JUMP_SLOT", 821}}, 0, 1909, 2, 0,
                        {{"zRB", 1276}, {"zR", 633}}, 218, 215, 0, {{"main", 40}, {"sdk", 317}}, 0};
const Expect rtld = {"rtld", 0x2680, 0xa84, 0xc008, 0xfff8, 3, 2, 2, 0, 0, {{"GLOB_DAT", 1}}, {{"JUMP_SLOT", 1}}, 39, 34, 2, 0,
                     {{"zR", 19}, {"zRB", 15}}, 8, 6, 7, {{"sdk", 2}}, 0};
const Expect main_201 = {"main", 0x2749480, 0xadfdf4, 0x2a1ec0, 0x854140, 1052, 947, 3, 104, 54, {{"ABS64", 1732}, {"GLOB_DAT", 28}},
                         {{"JUMP_SLOT", 885}}, 285495, 147875, 3, 3388, {{"zRB", 79966}, {"zR", 64521}, {"zPLRB", 3388}}, 92327, 92241, 0,
                         {{"subsdk0", 20}, {"sdk", 925}}, 2};
const Expect main_105 = {"main", 0x265a3e0, 0xac254c, 0x29da70, 0x844590, 1048, 943, 3, 104, 54, {{"ABS64", 1732}, {"GLOB_DAT", 28}},
                         {{"JUMP_SLOT", 881}}, 280778, 145121, 3, 3388, {{"zRB", 78298}, {"zR", 63435}, {"zPLRB", 3388}}, 90798, 90712, 0,
                         {{"subsdk0", 20}, {"sdk", 921}}, 2};

struct Build {
    const char* version;
    const char* main_id;
    const Expect* main;
    Counts dups;   // names exported by two modules
};
const Build builds[] = {
    {"2.0.1", "76a489f3bd3642fa5e18bd83d32c98ba", &main_201, {{"main&subsdk0", 6}, {"main&sdk", 62}, {"subsdk0&sdk", 10}}},
    {"1.0.5", "816c5358e8178d802fcec4a7ce5a741a", &main_105, {{"main&subsdk0", 6}, {"main&sdk", 62}, {"subsdk0&sdk", 10}}},
};

void check_module(const Program& p, const Expect& x) {
    const int i = p.index(x.name);
    if (!CHECK(i >= 0)) return;
    const Module& m = p.modules[size_t(i)];
    std::printf("  %s\n", m.name.c_str());
    CHECK_EQ(m.seg(seg_text).size, x.text);
    CHECK_EQ(m.seg(seg_ro).size, x.ro);
    CHECK_EQ(m.seg(seg_data).size, x.data);
    CHECK_EQ(m.nso.bss_size, x.bss);
    CHECK_EQ(m.symbols.size(), x.dynsym);
    size_t imports = 0, imports_weak = 0, exports = 0, exports_weak = 0, weak0 = 0;
    Counts from, rela, jmprel, augs;
    for (uint32_t s = 1; s < m.symbols.size(); s++) {
        const Symbol& y = m.symbols[s];
        const Resolution& r = p.resolution[size_t(i)][s];
        if (y.imported()) {
            imports++, imports_weak += y.bind == STB_WEAK;
            if (r.provider >= 0) from[p.modules[size_t(r.provider)].name]++;
            weak0 += r.kind == Bound::weak_zero;
            CHECK(r.kind != Bound::unresolved);
        }
        if (y.exported()) exports++, exports_weak += y.bind == STB_WEAK;
    }
    CHECK_EQ(imports, x.imports);
    CHECK_EQ(imports_weak, x.imports_weak);
    CHECK_EQ(exports, x.exports);
    CHECK_EQ(exports_weak, x.exports_weak);
    CHECK(from == x.from);
    CHECK_EQ(weak0, x.unresolved_weak);
    for (auto& r : m.rela) rela[reloc_name(r.type)]++;
    for (auto& r : m.jmprel) jmprel[reloc_name(r.type)]++;
    CHECK(rela == x.rela);
    CHECK(jmprel == x.jmprel);
    CHECK_EQ(m.relr.size(), x.relr);
    const EhFrame e = parse_eh_frame(m);
    CHECK_EQ(e.fdes.size(), x.fdes);
    CHECK_EQ(e.hdr.fde_count, x.fdes);
    CHECK(e.hdr_matches);
    CHECK_EQ(e.cies.size(), x.cies);
    CHECK_EQ(e.lsda_count(), x.lsda);
    for (auto& f : e.fdes) augs[e.cies[f.cie].augmentation]++;
    CHECK(augs == x.fdes_by_aug);
    std::set<uint64_t> starts, text_targets;
    for (auto& f : e.fdes) starts.insert(f.start);
    for (uint64_t t : m.relative_targets())
        if (t < m.seg_end(seg_text)) text_targets.insert(t);
    size_t at_fde = 0;
    for (uint64_t t : text_targets) at_fde += starts.count(t);
    CHECK_EQ(text_targets.size(), x.text_targets);
    CHECK_EQ(at_fde, x.text_targets_at_fde);
    CHECK_EQ(m.svc_sites().size(), x.svc_sites);
    CHECK(m.hashes_checked);
    CHECK_EQ(m.hash_mismatches(), 0u);
    CHECK(m.notes.empty());
}

// Every module: the hash tables, .eh_frame and relocations hold together, whatever the build
void check_structure(const Program& p) {
    CHECK(p.problems.empty());
    for (const auto& s : p.problems) std::printf("  problem: %s\n", s.c_str());
    for (size_t i = 0; i < p.modules.size(); i++) {
        const Module& m = p.modules[i];
        CHECK_EQ(m.hash_mismatches(), 0u);
        for (uint32_t s = 1; s < m.symbols.size(); s++)   // protected and hidden definitions are never preempted
            if (m.symbols[s].defined() && m.symbols[s].visibility != STV_DEFAULT) CHECK(p.resolution[i][s].kind == Bound::self);
        const EhFrame e = parse_eh_frame(m);
        CHECK(e.hdr_matches);
        for (const Fde& f : e.fdes) {
            if (!CHECK(f.start + f.size <= m.seg_end(seg_text))) break;
        }
        // every RELR slot now holds an address inside its own module
        size_t stray = 0;
        for (uint64_t a : m.relr) stray += p.read64(int(i), a) - p.bases[i] >= m.image_size();
        CHECK_EQ(stray, 0u);
        // init_array entries are relocated into the module's text
        if (auto a = m.dyn.get(DT_INIT_ARRAY))
            for (uint64_t k = 0; k < m.dyn.value(DT_INIT_ARRAYSZ) / 8; k++)
                CHECK(p.read64(int(i), *a + 8 * k) - p.bases[i] < m.seg_end(seg_text));
        // every symbol relocation of an in-scope module lands on a defined symbol of some module, or 0 for a weak one
        for (const auto* t : {&m.rela, &m.jmprel})
            for (const Reloc& r : *t) {
                if (!r.sym) continue;
                const Resolution& b = p.resolution[i][r.sym];
                if (!CHECK(b.kind == Bound::self || b.kind == Bound::other || b.kind == Bound::weak_zero)) break;
                if (b.kind != Bound::weak_zero) CHECK(p.module_at(b.address) == b.provider);
            }
    }
}

std::set<uint32_t> svcs(const std::bitset<256>& b) {
    std::set<uint32_t> s;
    for (uint32_t i = 0; i < 256; i++)
        if (b[i]) s.insert(i);
    return s;
}

}  // namespace

int main() {
    const char* dir = std::getenv("NXW_DUMP");
    if (!dir || !*dir) {
        std::printf("NXW_DUMP is not set: skipped\n");
        return 77;
    }
    try {
        const Program p = load_program(dir, true);
        const std::string id = p.modules.at(0).build_id();
        const Build* b = nullptr;
        for (const Build& x : builds)
            if (id.starts_with(x.main_id)) b = &x;
        std::printf("%s: main %s (%s)\n", dir, id.c_str(), b ? b->version : "unknown build: structural checks only");
        check_structure(p);
        if (b) {
            for (const Expect* x : {b->main, &subsdk0, &sdk, &rtld}) check_module(p, *x);
            Counts dups;
            for (size_t i = 0; i < 3; i++)
                for (size_t j = i + 1; j < 3; j++)
                    for (auto& [n, k] : p.modules[i].by_name) dups[p.modules[i].name + "&" + p.modules[j].name] += p.modules[j].by_name.count(n);
            CHECK(dups == b->dups);

            // main.npdm allows 0x01-0x29, 0x2c, 0x2d, 0x32-0x36 and 0x80; 11 of sdk's 57 SVCs are outside that
            const Npdm n = parse_npdm(nxw::read_file(std::filesystem::path(dir) / "main.npdm"));
            std::set<uint32_t> allowed = {0x2c, 0x2d, 0x32, 0x33, 0x34, 0x35, 0x36, 0x80};
            for (uint32_t s = 1; s <= 0x29; s++) allowed.insert(s);
            CHECK(svcs(n.aci0.kc.svcs) == allowed);
            CHECK(svcs(n.acid.kc.svcs) == allowed);
            CHECK_EQ(n.aci0.program_id, 0x0400a3a01ee4c000u);
            CHECK_EQ(n.aci0.services.size(), 46u);
            std::set<uint32_t> used, denied;
            for (auto& s : p.modules[size_t(p.index("sdk"))].svc_sites()) used.insert(s.imm);
            for (uint32_t s : used)
                if (!allowed.count(s)) denied.insert(s);
            CHECK_EQ(used.size(), 57u);
            CHECK(denied == (std::set<uint32_t>{0x40, 0x41, 0x43, 0x44, 0x45, 0x4b, 0x50, 0x51, 0x52, 0x70, 0x72}));
        }
    } catch (const std::exception& e) {
        std::printf("FAIL: %s\n", e.what());
        return 1;
    }
    std::printf("%d failed checks\n", check::failures);
    return check::failures ? 1 : 0;
}
