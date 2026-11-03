#!/usr/bin/env python3
"""nxs2.py: build Pokemon Pokopia (Nintendo Switch 2) for your own Mac and iPhone, from your own dump.

Everything derived from the game is made here, on your machine, from the dump you give; nothing of the
game is in this repository (see LEGAL.md). The build goes through nxnative (the native host for Switch
games on Apple systems, with this project's Switch 2 changes), the game's shaders through ampere-lower
(Eden-NS2's Switch 2 shader lowering) and nxnative's Metal shader chain.

  nxs2.py setup                             fetch nxnative at the pinned commit, apply the Switch 2 patch, build ampere-lower
  nxs2.py doctor                            check the tools this Mac has (what setup and build need)
  nxs2.py build <dump> [options]            the whole build into <dump folder>/<title>_native (or --out)
       --mac-app         a double-clickable Mac app (the game files cloned inside it)
       --ipa             an unsigned IPA with the entitlements the game needs (sign it with your own team)
       --xcode --team-id ID   an Xcode project signed with your Apple developer team (then: nxs2.py sign)
       --dumps DIR...    shader dumps from an earlier run (DM_NVN_SHDUMP folders) to include from the start
       --no-ios          Mac libraries only (faster; --ipa / --xcode need the iOS ones)
  nxs2.py run <build dir>                   play in a window on this Mac; new shaders are recorded for refresh
  nxs2.py refresh <build dir> [DIR...]      add the shaders recorded while playing (Mac run, Mac app, iPhone) and rebuild the outputs
  nxs2.py sign <build dir> --team-id ID     archive and export the development IPA from the prepared Xcode project
  nxs2.py status <build dir>                what the build folder holds and what it still lacks

Needs: a Mac with Apple silicon, Xcode (27.x) with its command line tools (an iPhone build also needs the iOS
platform), Python 3.12 or newer with capstone (5.x), lz4 and fontTools, the .NET 10 runtime (the shader
translator), xcodegen for the iPhone build. `nxs2.py doctor` lists what is missing and how to get it.
"""
import argparse, concurrent.futures as cf, hashlib, json, os, pathlib, platform, re, shutil, subprocess, sys, time

TITLE = '0400A3A01EE4C000'
VERSION = 327680
NAME = 'Pokemon Pokopia'
UPSTREAM = 'https://github.com/seedds/nxnative'
UPSTREAM_COMMIT = 'c1df4dd'   # the nxnative commit host/nxnative-switch2.patch is made against
FORK = 'https://github.com/zenvyzenshiro/NXNativeWindows'   # nxnative's Windows port: its win/ folder builds the iPhone app with LLVM, Metal Developer Tools for Windows and .NET
FORK_COMMIT = '269d61ff974b2e8e073d7357466ae130c3f6812f'   # fetched by its full id (a sparse checkout of its win/ folder only)
WIN = sys.platform == 'win32'
HERE = pathlib.Path(__file__).resolve().parent
PROFILE_SRC = HERE / 'profile' / TITLE
TOOLS = HERE / 'tools'
AMPERE = HERE / 'ampere-lower' / ('windows/ampere-lower.exe' if WIN else 'ampere-lower')
NX = pathlib.Path(os.environ.get('NXNATIVE') or HERE / 'nxnative')
PY = sys.executable
T0 = time.time()

# the shader tables a profile has before the package is made (as nxnative's NSP script writes them for a provisional profile)
EMPTY_TABLES = {
    'shader_map.tsv': '# kind: evidence  filled by the shader package (nxs2.py package)\npackage\tindex\tstage\tdm_ctl_sha16\tsource\n',
    'shader_facts.tsv': '# Answer table for translation queries: key <TAB> fact JSON. Filled by the shader package (nxs2.py package)\n',
    'shader_pairs.tsv': '# kind: evidence  vertex / pixel shader pairs within BNSH variations: vertex key <TAB> pixel key\n',
    'shader_map.tsv.conflicts.tsv': '# kind: evidence  control-section key conflict rulings: empty\ndm_ctl_sha16\tchosen_pc\tclass\tcandidates\trecords\n',
    'bf_want.tsv': '# generated\n',
    'pso_manifest.tsv': '# Prewarm seed list (shipped in the app, next to shader_map.tsv). Rows: S<TAB>shader key<TAB>stage / R<TAB>render pipeline key (pso_skey hex) / C<TAB>compute shader key.\n'
                        '# Empty until recorded on the Mac (DM_PSO_MANIFEST=off DM_PSO_RECORD).\n'}
# switches baked into the apps (the player's env.txt overrides them): the shaders the game makes while it runs are recorded for `nxs2.py refresh`
APP_ENV = '# nxs2.py: switches the build bakes in; Documents/env.txt (iPhone) or Application Support/nxnative/<title>/env.txt (Mac) override them\nDM_NVN_SHDUMP=shdump\n'
PHONE_ENV = APP_ENV + 'DM_OPMODE=0\nDM_FPS=30\n'   # handheld mode and 30 fps on the phone: what the Switch 2 does in handheld, and what keeps the phone cool


class Fail(Exception): pass


def hms(s):
    s = int(s); return f'{s // 60}:{s % 60:02d}' if s < 3600 else f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}'


def say(msg): print(f'[{hms(time.time() - T0)}] {msg}', flush=True)


def run(cmd, log, env=None, cwd=None, ok=(0,), heartbeat=30, show=None):
    """Run cmd with its output in log (a file), a heartbeat line on the console while it runs (and the lines show matches, or all of them with
    -v). A non-zero exit not in ok stops the build with the log's last lines."""
    cmd = [str(c) for c in cmd]
    log = pathlib.Path(log); log.parent.mkdir(parents=True, exist_ok=True)
    e = dict(os.environ); e.update(env or {})
    last, t_beat, t_start = '', time.time(), time.time()
    with open(log, 'w', encoding='utf-8', errors='replace') as f:
        f.write('$ ' + ' '.join(cmd) + '\n'); f.flush()
        p = subprocess.Popen(cmd, env=e, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        for line in p.stdout:
            f.write(line); last = line.rstrip()
            if VERBOSE or (show and re.search(show, line)): print('    ' + last[:160], flush=True)
            if time.time() - t_beat > heartbeat:
                t_beat = time.time(); print(f'    ... {hms(time.time() - t_start)}  {last[:120]}', flush=True)
        rc = p.wait()
    if rc not in ok:
        tail = ''.join(log.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)[-25:])
        raise Fail(f'{pathlib.Path(cmd[0]).name} failed ({rc}); log: {log}\n{tail}')
    return rc


def which(name, *extra):
    p = shutil.which(name)
    if p: return p
    for x in extra:
        x = pathlib.Path(os.path.expanduser(x))
        if x.is_file() and os.access(x, os.X_OK): return str(x)
    return None


def dotnet_root():   # the .NET install that holds the runtime: on PATH, in ~/.dotnet, or Homebrew's
    d = which('dotnet', '~/.dotnet/dotnet', '/usr/local/share/dotnet/dotnet', '/opt/homebrew/opt/dotnet/libexec/dotnet')
    return pathlib.Path(d).resolve().parent if d else None


# ---- Windows: where the fork's toolchain lives (win/toolchain.py reads the same names: NXW_IOS_SDK, SPIRV_CROSS, win/toolchain.json)
def program_files(): return [pathlib.Path(os.environ.get(k, d)) for k, d in (('ProgramFiles', r'C:\Program Files'), ('ProgramFiles(x86)', r'C:\Program Files (x86)'))]


def spirv_cross():
    p = os.environ.get('SPIRV_CROSS') or shutil.which('spirv-cross')
    if p and pathlib.Path(p).is_file(): return pathlib.Path(p)
    for root in (pathlib.Path('C:/VulkanSDK'),) + tuple(pathlib.Path(v) for v in (os.environ.get('VULKAN_SDK'),) if v):
        hits = sorted(root.glob('*/Bin/spirv-cross.exe')) + sorted(root.glob('Bin/spirv-cross.exe'))
        if hits: return hits[-1]
    return None


def ios_sdk():
    p = os.environ.get('NXW_IOS_SDK')
    if p and pathlib.Path(p).is_dir(): return pathlib.Path(p)
    hits = sorted((NX / 'win/sdk/out').glob('**/iPhoneOS*.sdk')) if (NX / 'win/sdk/out').is_dir() else []
    return hits[-1] if hits else None


def metal_tools():
    for pf in program_files():
        hits = sorted(pf.glob('Metal Developer Tools*'))
        if hits: return hits[-1]
    return None


def llvm_tool(name):
    p = shutil.which(name)
    if p: return pathlib.Path(p)
    for pf in program_files():
        q = pf / 'LLVM/bin' / f'{name}.exe'
        if q.is_file(): return q
    return None


def fingerprint(*parts):
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, pathlib.Path):
            st = p.stat() if p.exists() else None
            h.update(f'{p}:{st.st_size if st else -1}:{int(st.st_mtime) if st else 0}\n'.encode())
        else: h.update(repr(p).encode() + b'\n')
    return h.hexdigest()[:16]


class Build:
    def __init__(self, out):
        self.out = pathlib.Path(out).resolve()
        self.logs = self.out / 'logs'; self.stamps = self.out / 'stamps'
        self.extract = self.out / 'extract'; self.exefs = self.extract / 'ExeFS'; self.romfs_img = self.extract / 'RomFS.img'
        self.patched = self.out / 'patched'; self.host = self.out / 'host' / 'dmhost'; self.font = self.out / 'font'
        self.profile_root = self.out / 'profile'; self.profile = self.profile_root / TITLE
        self.shader = self.out / 'shader'; self.corpus = self.shader / 'corpus'; self.ctl = self.shader / 'corpus-ctl'
        self.maxwell = self.shader / 'maxwell'; self.pkg = self.shader / 'pkg'; self.tables = self.shader / 'tables'
        self.cache = self.shader / 'cache'; self.ios = self.shader / 'ios'
        self.outputs = self.out / 'out'; self.rundir = self.out / 'run'; self.phone = self.out / 'phone'
        self.icon = self.out / 'icon.png'   # the apps' icon, made by nxs2.py icon from your own game; none = the blank default
        self.game = read_game_tsv(PROFILE_SRC / 'game.tsv')
        self.modules = self.game['modules'][0].split()
        self.build_ids = dict(kv.split('=') for kv in self.game['build'][0].split('\t')[2:] if '=' in kv)
        for d in (self.logs, self.stamps): d.mkdir(parents=True, exist_ok=True)

    # ---- stamps: a stage is skipped when its inputs are as at the last run
    def fresh(self, name, key):
        f = self.stamps / f'{name}.json'
        return f.is_file() and json.loads(f.read_text()).get('key') == key

    def stamp(self, name, key): (self.stamps / f'{name}.json').write_text(json.dumps({'key': key, 'time': time.strftime('%Y-%m-%d %H:%M:%S')}) + '\n')

    def env(self, **more):
        e = {'DM_GAMES_DIR': str(self.profile_root), 'DM_GAMES_FIRST': '1', 'DM_GAME': TITLE, 'GAME': TITLE, 'DM_ALLOW_OTHER_METAL': '1', 'NXNATIVE': str(NX),
             'AMPERE_LOWER': str(AMPERE), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
             'J': str(JOBS)}   # every worker pool of the build: the shader chain (dmsh_build.py), the lowering, the iPhone app compile on Windows
        if WIN: e.update(windows_env())
        else:
            d = dotnet_root()
            if d: e['DOTNET_ROOT'] = str(d); e['PATH'] = f'{d}:{os.environ.get("PATH", "")}'
        e.update(more); return e

    def corpus_dirs(self): return [d for d in (self.corpus / n for n in ('rdb', 'main-embedded', 'linkdata', 'dumps')) if d.is_dir()]

    def chain_version(self):
        r = subprocess.run([PY, NX / 'dm/gfx/tools/dmsh_chain.py'], capture_output=True, text=True, cwd=NX / 'dm/gfx/tools')
        if r.returncode or not r.stdout.strip(): raise Fail(f'dmsh_chain.py gave no chain version: {r.stderr.strip()[-300:]}')
        return r.stdout.strip().splitlines()[-1]

    def lib(self): return self.cache / f'b-{self.chain_version()}'


def read_game_tsv(path):
    g = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'): continue
        k, _, v = line.partition('\t'); g.setdefault(k, []).append(v)
    return g


def build_id(nso):
    with open(nso, 'rb') as f:
        f.seek(0x40); return f.read(16).hex()


def gentle_jobs():
    """How many worker processes the build runs at once by default. Shader compiles keep every worker at 100 %, for half an hour or more. On a Mac with Apple
    silicon that is fine at all cores. On a PC it is a long all-core load that some machines do not take: 13th and 14th generation Intel Core CPUs without
    Intel's 2024 microcode fix (BIOS update) crash under it. The default leaves most of a PC's cores alone; --full-speed or -j N uses more."""
    n = os.cpu_count() or 4
    return n if sys.platform == 'darwin' else max(2, min(8, n // 4))


def lower_priority():
    """The whole build at low priority (its child processes inherit it), so the machine stays responsive and the build takes only the time other work leaves."""
    try:
        if WIN:
            import ctypes
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p   # a pseudo-handle (-1): as a 32-bit int it would be truncated and the call would fail
            k.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS: the children inherit it
        else:
            os.nice(10)
    except Exception: pass


def windows_env():
    """What every tool started on Windows needs: the fork's shim on PYTHONPATH (it maps xcrun to Metal Developer Tools for Windows, the translator to
    `dotnet`, build_ipa.sh to win/build_app.py), the native SPIRV-Cross, the iPhoneOS SDK, and the shader workers the fork recommends."""
    e = {'PYTHONPATH': os.pathsep.join([str(NX / 'win/pyshim')] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p])}
    sc = spirv_cross()
    if sc: e['SPIRV_CROSS'] = str(sc)
    sdk = ios_sdk()
    if sdk: e['NXW_IOS_SDK'] = str(sdk)
    llvm = llvm_tool('clang')
    if llvm: e['PATH'] = f'{llvm.parent}{os.pathsep}{os.environ.get("PATH", "")}'
    return e


# ---------------------------------------------------------------- environment
def doctor(need_ios=True, quiet=False):
    """What this machine has of what the build needs. Returns the list of problems (empty = ready)."""
    bad = []
    def ok(name, cond, hint):
        if not quiet: print(f'  {"ok     " if cond else "MISSING"}  {name}' + ('' if cond else f'   -> {hint}'))
        if not cond: bad.append(name)
    if WIN: return doctor_windows(ok, bad)
    ok('macOS on Apple silicon', sys.platform == 'darwin' and platform.machine() == 'arm64', 'the host runs the game\'s arm64 code natively: an Apple silicon Mac is required')
    ok(f'Python 3.12+ (running {platform.python_version()})', sys.version_info >= (3, 12), 'brew install python@3.12, then run nxs2.py with python3.12')
    for mod, pip in (('capstone', 'capstone>=5,<6'), ('lz4', 'lz4'), ('fontTools', 'fonttools')):
        try: __import__(mod); have = True
        except ImportError: have = False
        ok(f'python module {mod}', have, f'{PY} -m pip install "{pip}"')
    try:
        import capstone; ok('capstone 5.x', capstone.__version__.startswith('5.'), f'{PY} -m pip install "capstone>=5,<6"')
    except ImportError: pass
    xc = subprocess.run(['xcode-select', '-p'], capture_output=True, text=True)
    ok('Xcode command line tools (xcode-select -p)', xc.returncode == 0 and xc.stdout.strip() != '', 'install Xcode from developer.apple.com, open it once, then: sudo xcode-select -s /Applications/Xcode.app')
    ok('clang', which('clang') is not None, 'comes with Xcode\'s command line tools')
    ok('make', which('make') is not None, 'comes with Xcode\'s command line tools')
    for sdk in ('macosx',) + (('iphoneos',) if need_ios else ()):
        r = subprocess.run(['xcrun', '-sdk', sdk, 'metal', '--version'], capture_output=True, text=True)
        ok(f'Metal compiler for {sdk}', r.returncode == 0, 'Xcode -> Settings -> Components: install the Metal toolchain' + (' and the iOS platform' if sdk == 'iphoneos' else ''))
    d = dotnet_root()
    have_rt = False
    if d:
        r = subprocess.run([str(d / 'dotnet'), '--list-runtimes'], capture_output=True, text=True)
        have_rt = any(l.startswith('Microsoft.NETCore.App 10.') for l in r.stdout.splitlines())
    ok('.NET 10 runtime (the shader translator)', have_rt, 'brew install --cask dotnet-sdk   (or the .NET 10 runtime from dotnet.microsoft.com)')
    if need_ios: ok('xcodegen (the iPhone build)', which('xcodegen', '~/.local/bin/xcodegen') is not None, 'brew install xcodegen')
    ok('git', which('git') is not None, 'comes with Xcode\'s command line tools')
    std = pathlib.Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    ok('a standard font on this Mac (Arial Unicode)', std.is_file(), 'STD_FONT=<a Unicode TTF> names another font for the game\'s text')
    return bad


def intel_microcode_warning():
    """13th and 14th generation Intel Core CPUs crash under long heavy loads until the BIOS carries Intel's 2024 microcode fix (0x12B or newer); the shader
    compile is such a load. A warning, not a requirement: the gentle default load keeps the risk low, a BIOS update removes it."""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0')
        name = winreg.QueryValueEx(k, 'ProcessorNameString')[0].strip()
        raw = winreg.QueryValueEx(k, 'Update Revision')[0]
    except Exception: return None
    if not re.search(r'\b1[34]th Gen Intel', name): return None
    rev = int.from_bytes(raw[4:8] if len(raw) >= 8 else raw[:4], 'little')
    if rev >= 0x12B: return None
    return (f'{name} runs microcode {rev:#x}: these CPUs need Intel\'s 0x12B fix or newer (a BIOS update from your motherboard maker), '
            'or long full loads can crash Windows. The build\'s default load is gentle; avoid --full-speed until the BIOS is updated')


def doctor_windows(ok, bad):
    """The Windows build (the iPhone IPA only; a Mac app needs a Mac): the toolchain nxnative's Windows port documents."""
    w = intel_microcode_warning()
    if w: print(f'  WARNING  {w}')
    ok('Windows on x64', platform.machine().lower() in ('amd64', 'x86_64'), 'the Windows tools are x64 builds')
    ok(f'Python 3.12+ (running {platform.python_version()})', sys.version_info >= (3, 12), 'https://www.python.org/downloads/ (tick "Add python.exe to PATH")')
    for mod, pip in (('capstone', 'capstone>=5,<6'), ('lz4', 'lz4'), ('fontTools', 'fonttools'), ('PIL', 'pillow')):
        try: __import__(mod); have = True
        except ImportError: have = False
        ok(f'python module {mod}', have, f'{PY} -m pip install "{pip}"')
    try:
        import capstone; ok('capstone 5.x', capstone.__version__.startswith('5.'), f'{PY} -m pip install "capstone>=5,<6"')
    except ImportError: pass
    try: import tkinter; ok('Tk (the window)', True, '')
    except ImportError: ok('Tk (the window)', False, 'reinstall Python from python.org with "tcl/tk and IDLE" ticked (the command line works without it)')
    dn = shutil.which('dotnet'); have_rt = False
    if dn:
        r = subprocess.run([dn, '--list-runtimes'], capture_output=True, text=True)
        have_rt = any(l.startswith('Microsoft.NETCore.App 10.') for l in r.stdout.splitlines())
    ok('.NET 10 runtime (the shader translator)', have_rt, 'https://dotnet.microsoft.com/download/dotnet/10.0 (the .NET Runtime is enough)')
    ok('LLVM for Windows: clang', llvm_tool('clang') is not None, 'https://github.com/llvm/llvm-project/releases (LLVM-<version>-win64.exe)')
    ok('LLVM for Windows: ld64.lld', llvm_tool('ld64.lld') is not None, 'comes with LLVM for Windows')
    mt = metal_tools()   # 4.x: ios/bin and macos/bin toolchains; 5.x and 6.x: one bin/ for every target
    ok('Metal Developer Tools for Windows (metal.exe)', mt is not None and any(mt.glob(p) for p in ('bin/metal.exe', 'ios/bin/metal.exe', 'ios/*/bin/metal.exe')),
       'https://developer.apple.com/download/all/?q=Metal%20Developer%20Tools (sign in with an Apple ID)')
    sdk = ios_sdk()
    ok(f'iPhoneOS SDK{f" ({sdk.name})" if sdk else ""}', sdk is not None, 'after setup: python nxnative\\win\\sdk\\extract_xip.py <Xcode.xip> (the SDK is taken from Xcode, downloaded from developer.apple.com), or NXW_IOS_SDK=<path>')
    ok('SPIRV-Cross (spirv-cross.exe)', spirv_cross() is not None, 'the Vulkan SDK from https://vulkan.lunarg.com (its Bin folder has it), or SPIRV_CROSS=<path>')
    ok('git', shutil.which('git') is not None, 'https://git-scm.com/download/win')
    fonts = pathlib.Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    ok('a standard font (Arial Unicode or Arial)', any((fonts / n).is_file() for n in ('ARIALUNI.TTF', 'arial.ttf')) or bool(os.environ.get('STD_FONT')), 'STD_FONT=<a Unicode TTF>')
    ok('a bold font for the button icons (Arial Bold)', any((fonts / n).is_file() for n in ('ARLRDBD.TTF', 'arialbd.ttf')) or bool(os.environ.get('LETTER_FONT')), 'LETTER_FONT=<a bold TTF>')
    return bad


def setup(args):
    """nxnative at the pinned commit with the Switch 2 patch and the rebuilt translator, and ampere-lower built."""
    bad = doctor(need_ios=not args.no_ios)
    if bad: raise Fail('install the missing tools above, then run setup again')
    if not (NX / '.git').is_dir():
        say(f'fetching nxnative ({UPSTREAM} at {UPSTREAM_COMMIT}) into {NX}')
        run(['git', 'clone', '--filter=blob:none', '--no-checkout', UPSTREAM, NX], HERE / 'setup-clone.log')
    run(['git', 'config', 'core.autocrlf', 'false'], HERE / 'setup-config.log', cwd=NX)   # the sources as on a Mac: the chain version hashes them, and the patches expect LF
    def head(): return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, cwd=NX).stdout.strip()
    marker = NX / 'dm/gfx/nvn/.switch2-patch'
    patch = HERE / 'host/nxnative-switch2.patch'
    want = fingerprint(patch)
    if not head().startswith(UPSTREAM_COMMIT[:7]):
        say(f'checking out nxnative {UPSTREAM_COMMIT} (was {head() or "nothing"})')
        run(['git', 'checkout', '-q', '--', '.'], HERE / 'setup-reset.log', cwd=NX, ok=(0, 1))   # drop a patch applied to another commit
        for m in (marker, NX / 'dm/gfx/nvn/.windows-extras'):
            if m.exists(): m.unlink()
        run(['git', 'fetch', 'origin', UPSTREAM_COMMIT], HERE / 'setup-fetch.log', cwd=NX, ok=(0, 128))
        run(['git', 'checkout', '-q', UPSTREAM_COMMIT], HERE / 'setup-checkout.log', cwd=NX)
    if not (marker.is_file() and marker.read_text().strip() == want):
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True, cwd=NX).stdout.strip()
        if dirty:
            say('resetting nxnative to the pinned commit before applying the patch')
            run(['git', 'checkout', '-q', '--', '.'], HERE / 'setup-reset.log', cwd=NX)
            if (NX / 'dm/gfx/nvn/.windows-extras').exists(): (NX / 'dm/gfx/nvn/.windows-extras').unlink()
        say('applying the Switch 2 host patch')
        run(['git', 'apply', '--whitespace=nowarn', patch], HERE / 'setup-patch.log', cwd=NX)
        marker.write_text(want + '\n')
    bin_dst = NX / 'dm/gfx/tools/dmsh_xlate_bin'
    for f in sorted((HERE / ('host/windows/dmsh_xlate_bin' if WIN else 'host/dmsh_xlate_bin')).iterdir()):
        dst = bin_dst / f.name
        if not dst.is_file() or dst.read_bytes() != f.read_bytes():
            shutil.copy2(f, dst); say(f'translator: {f.name} installed')
    if not WIN: (bin_dst / 'dmsh_xlate').chmod(0o755)
    if WIN: setup_windows(want)
    elif not AMPERE.is_file() or AMPERE.stat().st_mtime < (HERE / 'ampere-lower/main.cpp').stat().st_mtime:
        say('building ampere-lower (the Switch 2 shader lowering)')
        run(['sh', HERE / 'ampere-lower/build.sh'], HERE / 'setup-ampere.log')
    say(f'setup done: nxnative {head()} + Switch 2 patch at {NX}, ampere-lower at {AMPERE}')


def setup_windows(patch_key):
    """The Windows side on top of the patched nxnative: the fork's win/ folder (LLVM + Metal Developer Tools build of the iPhone app, the tool shim), its three
    Windows fixes to nxnative, this project's changes to its app builder, and the tool lock rewritten for the tools on this PC (the native SPIRV-Cross and the
    Windows Metal compiler), followed by the chain header that carries the lock."""
    src = HERE / 'nxnativewindows-src'
    if not (src / '.git').is_dir():
        say(f'fetching nxnative\'s Windows port ({FORK} at {FORK_COMMIT[:7]}, its win/ folder)')
        if src.exists(): shutil.rmtree(src)
        run(['git', 'clone', '--filter=blob:none', '--no-checkout', '--sparse', FORK, src], HERE / 'setup-fork-clone.log')
        run(['git', 'config', 'core.autocrlf', 'false'], HERE / 'setup-fork-config.log', cwd=src)
        run(['git', 'sparse-checkout', 'set', 'win'], HERE / 'setup-fork-sparse.log', cwd=src)
    cur = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, cwd=src).stdout.strip()
    if cur != FORK_COMMIT: run(['git', 'fetch', 'origin', FORK_COMMIT], HERE / 'setup-fork-fetch.log', cwd=src, ok=(0, 128))
    if cur != FORK_COMMIT or not (src / 'win/build_app.py').is_file():   # a --no-checkout clone has HEAD but no files yet
        run(['git', 'checkout', '-q', FORK_COMMIT], HERE / 'setup-fork-checkout.log', cwd=src)
    if not (src / 'win/build_app.py').is_file(): raise Fail(f'the Windows port\'s win/ folder did not arrive in {src}; see {HERE / "setup-fork-checkout.log"}')
    if (NX / 'win').is_dir(): shutil.rmtree(NX / 'win')
    shutil.copytree(src / 'win', NX / 'win', ignore=shutil.ignore_patterns('__pycache__', 'build', 'sdk'))
    (NX / 'win/sdk').mkdir(exist_ok=True); shutil.copy2(src / 'win/sdk/extract_xip.py', NX / 'win/sdk/extract_xip.py')   # the SDK extractor; the SDK itself stays where the player put it (win/sdk/out)
    say('Windows port: win/ installed')
    extras = HERE / 'host/windows/fork-extras.patch'; marker = NX / 'dm/gfx/nvn/.windows-extras'   # beside the Switch 2 patch's marker; setup's reset removes both
    key = fingerprint(extras, patch_key)
    if not (marker.is_file() and marker.read_text().strip() == key):
        run(['git', 'apply', '--whitespace=nowarn', extras], HERE / 'setup-extras.log', cwd=NX); marker.write_text(key + '\n'); say('Windows fixes applied (RomFS image reads, process pool size, IPA packing)')
    run(['git', 'apply', '--whitespace=nowarn', '--directory=win', HERE / 'host/windows/build_app.patch'], HERE / 'setup-build-app.log', cwd=NX)
    say('app builder: Switch 2 changes applied')
    env = dict(os.environ, **windows_env())
    if not spirv_cross(): raise Fail('no spirv-cross.exe: install the Vulkan SDK (vulkan.lunarg.com) or set SPIRV_CROSS=<path to spirv-cross.exe>')
    say('tool lock: the translator, SPIRV-Cross and Metal compiler on this PC')
    run([PY, NX / 'dm/gfx/tools/tool_lock.py', '--update-lock'], HERE / 'setup-lock.log', env=env, cwd=NX / 'dm/gfx/tools')
    run([PY, NX / 'dm/gfx/tools/dmsh_chain.py', '--header'], HERE / 'setup-header.log', env=env, cwd=NX / 'dm/gfx/tools')


# ---------------------------------------------------------------- the dump
def find_dump(args):
    """(exefs folder, romfs folder) of the update the recipe is for, under the folder given (the dump as a Switch 2 dumping tool writes it:
    <game>/updates/<version>/{exefs,romfs}, or a folder holding exefs/ and romfs/ directly), checked by the executable's build ID."""
    if args.exefs and args.romfs: cands = [(pathlib.Path(args.exefs), pathlib.Path(args.romfs))]
    else:
        d = pathlib.Path(args.dump).expanduser().resolve()
        roots = [d / 'updates' / str(VERSION), d]
        if (d / 'updates').is_dir(): roots += sorted((p for p in (d / 'updates').iterdir() if p.is_dir()), reverse=True)
        if (d / 'base').is_dir(): roots.append(d / 'base')
        cands = []
        for r in roots:
            for ex, ro in (('exefs', 'romfs'), ('ExeFS', 'RomFS'), ('exefs', 'RomFS'), ('ExeFS', 'romfs')):
                if (r / ex / 'main').is_file() and (r / ro).is_dir() and (r / ex, r / ro) not in cands: cands.append((r / ex, r / ro))
    if not cands: raise Fail(f'no dump under {args.dump}: expected <folder>/updates/{VERSION}/exefs/main and .../romfs/, or exefs/ and romfs/ in the folder itself')
    g = read_game_tsv(PROFILE_SRC / 'game.tsv')
    want = dict(kv.split('=') for kv in g['build'][0].split('\t')[2:] if '=' in kv)
    seen = []
    for ex, ro in cands:
        bid = build_id(ex / 'main'); seen.append(f'{ex}: main build {bid}')
        if bid == want['main']:
            for m, b in want.items():
                if (ex / m).is_file() and build_id(ex / m) != b: raise Fail(f'{ex / m} has build {build_id(ex / m)}, the recipe expects {b}')
                if not (ex / m).is_file(): raise Fail(f'{ex / m} is missing from the dump')
            if not any(ro.rglob('*')): raise Fail(f'{ro} is empty')
            return ex, ro
    raise Fail(f'this recipe is for {NAME} v{VERSION} (main build {want["main"]}); the dump holds:\n  ' + '\n  '.join(seen) +
               '\n(a base-game folder is not enough: the game\'s update is needed)')


# ---------------------------------------------------------------- stages
def st_profile(b):
    """The game profile the tools read: game.tsv, the entitlements and the empty shader tables (the package fills them later)."""
    b.profile.mkdir(parents=True, exist_ok=True)
    for name in ('game.tsv', 'pokopia.entitlements', 'runtime_keys.tsv', 'shader_sources.tsv', 'shader_facts.seed.tsv'):
        src = PROFILE_SRC / name
        if src.is_file(): shutil.copyfile(src, b.profile / name)
    for name, text in EMPTY_TABLES.items():
        if not (b.profile / name).is_file(): (b.profile / name).write_text(text)


def st_extract(b, exefs, romfs):
    """The game as nxnative's tools read it: the ExeFS copied, a control record with the game's name, the RomFS packed into one image."""
    b.exefs.mkdir(parents=True, exist_ok=True)
    for f in sorted(exefs.iterdir()):
        if f.is_file() and (not (b.exefs / f.name).is_file() or (b.exefs / f.name).stat().st_size != f.stat().st_size): shutil.copy2(f, b.exefs / f.name)
    nacp = b.extract / 'control.nacp'
    if not nacp.is_file(): nacp.write_bytes(make_nacp(NAME, int(TITLE, 16), '1.0.5'))
    say(f'RomFS image from {romfs} ({sum(1 for _ in romfs.rglob("*") if _.is_file())} files)')
    run([PY, NX / 'dm/tools/romfs_image.py', '--reuse', romfs, b.romfs_img], b.logs / 'romfs_image.log', env=b.env())
    if not b.romfs_img.is_file(): raise Fail('no RomFS image was written')


def make_nacp(name, title, version):   # a control record (NACP) with the game's name and version: Switch 2 dumps carry none, and the app build reads one
    import struct
    d = bytearray(0x4000)
    for lang in range(16):
        d[lang * 0x300:lang * 0x300 + len(name)] = name.encode(); d[lang * 0x300 + 0x200:lang * 0x300 + 0x207] = b'unknown'
    struct.pack_into('<Q', d, 0x3038, title); d[0x3060:0x3060 + len(version)] = version.encode()
    struct.pack_into('<Q', d, 0x3078, title); struct.pack_into('<Q', d, 0x30B0, title)
    return bytes(d)


def st_rewrite(b):
    """The game's code modules rewritten for Apple's kernel (nxnative's patch_nso.py), with their recipe tables."""
    key = fingerprint(*(b.exefs / m for m in b.modules), NX / 'dm/tools/patch_nso.py', PROFILE_SRC / 'game.tsv')
    if b.fresh('rewrite', key) and all((b.patched / f'{m}.patched.bin').is_file() for m in b.modules): return say('rewrite: up to date')
    b.patched.mkdir(parents=True, exist_ok=True)
    env = b.env(DM_REWRITE_J='1' if WIN else str(JOBS))   # the rewriter's worker pool forks (no fork on Windows): one worker runs the same code inline, about a minute
    for m in b.modules:
        say(f'rewriting {m}' + (' (one worker on Windows: a few minutes for main)' if WIN and m == 'main' else ''))
        run([PY, NX / 'dm/tools/patch_nso.py', b.exefs / m, b.patched, m], b.logs / f'rewrite_{m}.log', env=env, cwd=NX)
    b.stamp('rewrite', key)


def st_host(b, jobs):
    """The Mac host built for this game's profile (the profile is compiled in, so the binary is per game)."""
    srcs = [p for d in ('dm/host', 'dm/gfx', 'dm/gfx/nvn', 'dm/gfx/tex', 'dm/gfx/msl', 'dm/mac') for p in sorted((NX / d).iterdir())
            if p.is_file() and (p.suffix in ('.c', '.m', '.h', '.S', '.inc', '.metal') or p.name == 'Makefile')]   # the sources, not make's outputs
    key = fingerprint(*srcs, PROFILE_SRC / 'game.tsv')
    if b.fresh('host', key) and b.host.is_file(): return say('host: up to date')
    say('building the Mac host (dmhost)')
    run(['make', '-C', NX / 'dm/host', f'GAME={TITLE}', 'dmhost', f'-j{jobs}'], b.logs / 'host_make.log', env=b.env(), show=r'error:|\[RED\]')
    b.host.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(NX / 'dm/host/dmhost', b.host)
    b.stamp('host', key)


def st_font(b):
    """Stand-ins for the console's shared fonts, made from fonts on this Mac (nothing from the console): the standard font is a copy of the
    Mac's own Arial Unicode (STD_FONT=<ttf> names another), the button-icon font is generated by nxnative's make_ext_font.py."""
    b.font.mkdir(parents=True, exist_ok=True)
    std = pathlib.Path(os.environ.get('STD_FONT') or '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    if not std.is_file(): raise Fail(f'no standard font at {std}: set STD_FONT=<a Unicode TTF on this Mac>')
    if not (b.font / 'standard.ttf').is_file(): shutil.copyfile(std, b.font / 'standard.ttf')
    if not (b.font / 'nintendo_ext.ttf').is_file():
        run([PY, NX / 'dm/tools/make_ext_font.py', b.font / 'nintendo_ext.ttf'], b.logs / 'make_ext_font.log', env=b.env())


def st_corpus(b, romfs, dumps):
    """The game's shaders as the lowering reads them: every shader container in the game's files (shader_sources.tsv says where, read straight from
    the dump's RomFS folder, read-only), and any shader dumps of earlier runs (--dumps). Writes <shader>/corpus/<set>/ and <shader>/corpus-ctl/."""
    sources = [l.split('\t') for l in (PROFILE_SRC / 'shader_sources.tsv').read_text().splitlines() if l.strip() and not l.startswith('#')]
    key = fingerprint(b.exefs / 'main', b.romfs_img, *sorted(TOOLS.glob('*.py')), sources)
    if not (b.fresh('corpus', key) and (b.ctl / 'hostkeys.tsv').is_file()):
        for d in (b.corpus, b.ctl):
            if d.is_dir(): shutil.rmtree(d)
        nso = b.shader / 'nso'; nso.mkdir(parents=True, exist_ok=True)
        say('reading the executable\'s data (nso_tool.py)')
        run([PY, TOOLS / 'nso_tool.py', b.exefs / 'main', '--out', nso], b.logs / 'nso_tool.log', env=b.env())
        jobs = []   # (label, corpus set, mode, args)
        for kind, *paths in sources:
            if kind == 'blob': jobs.append(('main', 'main-embedded', 'blob', [nso / 'main.rodata', 'main.rodata']))
            elif kind == 'linkdata': jobs.append(('linkdata', 'linkdata', 'linkdata', [romfs / p for p in paths]))
            elif kind == 'rdb': jobs.append((pathlib.Path(paths[0]).name, 'rdb', 'rdb', [romfs / paths[0]]))
        for label, cset, mode, args in jobs:
            for p in args:
                if isinstance(p, pathlib.Path) and not p.is_file(): raise Fail(f'shader source {p} is missing from the dump')
        say(f'extracting the shaders of {len(jobs)} sources')
        def extract(j):
            run([PY, TOOLS / 'glslc2_extract.py', b.corpus / j[1], j[2], *j[3]], b.logs / f'corpus_{j[0]}.log', env=b.env(), heartbeat=120); return j[0]
        with cf.ThreadPoolExecutor(max(1, min(4, os.cpu_count() or 2))) as ex:   # the extractions in parallel; the control scans one at a time (they append to one hostkeys.tsv)
            for label in ex.map(extract, jobs): say(f'  extracted {label}')
        for label, cset, mode, args in jobs:
            run([PY, TOOLS / 's2_ctl_scan.py', b.ctl, mode, *args], b.logs / f'ctl_{label}.log', env=b.env(), heartbeat=120)
        b.stamp('corpus', key)
    else: say('corpus: up to date')
    add_dumps(b, dumps)


def add_dumps(b, dumps):
    """Shader dumps recorded while the game ran (DM_NVN_SHDUMP folders: <key>.ctl + <key>.code) become corpus entries and host keys."""
    dirs = [pathlib.Path(d).expanduser() for d in dumps if pathlib.Path(d).expanduser().is_dir() and any(pathlib.Path(d).expanduser().glob('*.ctl'))]
    if not dirs: return
    say(f'adding shader dumps from {len(dirs)} folder(s)')
    run([PY, TOOLS / 's2_dumps.py', *dirs, '--corpus', b.corpus / 'dumps', '--ctl', b.ctl], b.logs / 's2_dumps.log', env=b.env(), show=r'new|added|programs')


def st_lower(b, jobs):
    """Every corpus program lowered to Maxwell with ampere-lower (incremental: programs lowered before are kept)."""
    say('lowering the shaders (ampere-lower)')
    run([PY, TOOLS / 'lower_corpus.py', b.maxwell, *b.corpus_dirs()], b.logs / 'lower.log', env=b.env(J=str(jobs)), show=r'^programs|already done|^done')


def st_package(b):
    """The shader package nxnative's chain reads, and the profile's shader tables (shader_map.tsv, shader_facts.tsv with the hand-made answers on top)."""
    say('packaging the shaders (s2_pkg.py)')
    for d in (b.pkg, b.tables):
        if d.is_dir(): shutil.rmtree(d)
    run([PY, TOOLS / 's2_pkg.py', b.ctl, b.maxwell, b.pkg, b.tables, *b.corpus_dirs()], b.logs / 's2_pkg.log', env=b.env(), show=r'records|keys|written|pkg')
    shutil.copyfile(b.tables / 'shader_map.tsv', b.profile / 'shader_map.tsv')
    if (b.tables / 'shader_pairs.tsv').is_file(): shutil.copyfile(b.tables / 'shader_pairs.tsv', b.profile / 'shader_pairs.tsv')
    facts = {}
    order = []
    header = []
    for line in (b.tables / 'shader_facts.tsv').read_text(encoding='utf-8').splitlines():
        if line.startswith('#'): header.append(line); continue
        if not line.strip(): continue
        k = line.split('\t')[0]
        if k not in facts: order.append(k)
        facts[k] = line
    seed = PROFILE_SRC / 'shader_facts.seed.tsv'
    n = 0
    if seed.is_file():
        for line in seed.read_text(encoding='utf-8').splitlines():
            if line.startswith('#') or not line.strip(): continue
            k = line.split('\t')[0]
            if k not in facts: order.append(k)
            facts[k] = line; n += 1
    (b.profile / 'shader_facts.tsv').write_text('\n'.join(header + [facts[k] for k in order]) + '\n', encoding='utf-8')
    say(f'tables: {len(order)} facts rows ({n} hand-made), shader map {sum(1 for l in (b.profile / "shader_map.tsv").read_text().splitlines() if l and not l.startswith("#")) - 1} entries')


def st_chain(b, ios):
    """nxnative's shader chain: the lowered programs to Metal libraries for the Mac (and the iPhone), in <shader>/cache/b-<chain>/ and <shader>/ios/b-<chain>/.
    Incremental through the chain's own cache; the first run compiles everything (around half an hour on an M-series Mac, more with the iOS libraries)."""
    say('Metal libraries (nxnative chain: translate, cross-compile, metal)' + (' for the iPhone' if WIN else ' for the Mac and the iPhone' if ios else ' for the Mac'))
    env = b.env(DMSH_S2_CS='1', DMSH_S2_CENSUS='1')
    if ios: env['IOSOUT'] = str(b.ios)
    if WIN: env['DMSH_TARGETS'] = 'ios'   # no Mac host here: the macOS libraries are not needed (the tables are still made from the Mac-side reflection)
    b.cache.mkdir(parents=True, exist_ok=True)
    run([PY, NX / 'dm/gfx/tools/own_compilers.py', b.pkg, b.cache], b.logs / 'chain.log', env=env, cwd=NX / 'dm/gfx/tools', heartbeat=60,
        show=r'Metal cache|compiled|reused|\[RED\]|failed|success|lock')
    lib = b.lib()
    if not (lib / 'inputs.tsv').is_file(): raise Fail(f'the chain left no library stamp at {lib}/inputs.tsv; see {b.logs / "chain.log"}')


def st_mac_app(b):
    """A self-contained Mac app (nxnative's pack_app.py): launcher + host + the game files (APFS clones) + shader libraries + tables."""
    app = b.outputs / f'{NAME}.app'; b.outputs.mkdir(parents=True, exist_ok=True)
    envf = b.out / 'app_env.txt'; envf.write_text(APP_ENV)
    settings = b.out / 'launcher.json'
    run([PY, NX / 'dm/games/settings.py', TITLE, b.patched, b.extract / 'control.nacp', settings], b.logs / 'settings.log',
        env=b.env(GFXAPI='nvn', SETTINGS_PLATFORM='mac', DM_LANG='en-US'))
    cmd = [PY, NX / 'dm/mac/pack_app.py', app, '--host', b.host, '--patched', b.patched, '--romfs', b.romfs_img, '--msl', b.lib(), '--tables', b.profile,
           '--font', b.font, '--title-id', TITLE, '--name', NAME, '--bundle-id', f'org.nxnative.{TITLE.lower()}', '--lang', 'en-US', '--sign', '-',
           '--version', '1.0.5', '--category', b.game['app_category'][0], '--marker', b.game['romfs_marker'][0], '--env-file', envf, '--settings', settings]
    if b.icon.is_file(): cmd += ['--icon', b.icon]
    say('packing the Mac app')
    run(cmd, b.logs / 'pack_app.log', env=b.env(), show=r'^step:|^app:')
    if not (app / 'Contents/MacOS/dmhost').is_file(): raise Fail(f'no app at {app}')
    # the app's host records run-time shaders into shdump/ in its Application Support folder, which it does not create itself
    (pathlib.Path.home() / 'Library/Application Support/nxnative' / TITLE / 'shdump').mkdir(parents=True, exist_ok=True)
    say(f'Mac app: {app}')
    return app


def ipa_env(b):
    return b.env(W=str(b.out / 'ipa'), PATCHED=str(b.patched), ROMFS=str(b.romfs_img), MSL_IOS=str(b.ios), AOC='', DM_COPY_ROMFS='NO',
                 DM_ENTITLEMENTS=str(b.profile / 'pokopia.entitlements'), FONT_PY=PY, DM_SHOW_FPS='1', DM_PSO_GPUS='none', DM_ENV_FILE=str(b.out / 'phone_env.txt'),
                 DM_ICON=str(b.icon if b.icon.is_file() else b.out / 'no-icon'),   # build_ipa.sh otherwise looks for an icon.jpg beside the RomFS image
                 PATH=os.path.expanduser('~/.local/bin') + ':' + b.env().get('PATH', os.environ.get('PATH', '')))


def st_ipa(b):
    """An unsigned IPA (nxnative's build_ipa.sh): the app without the game files, which the phone takes from Documents/romfs.img; the executable
    carries the two kernel entitlements ad-hoc, so a re-signer that keeps entitlements can sign it for your own device."""
    (b.out / 'phone_env.txt').write_text(PHONE_ENV)
    b.outputs.mkdir(parents=True, exist_ok=True)
    out = b.outputs / f'{NAME.replace(" ", "")}-unsigned.ipa'
    say('building the IPA (unsigned)')
    run(['bash', NX / 'dm/ios/tools/build_ipa.sh', out], b.logs / 'build_ipa.log', env=ipa_env(b), show=r'^\[\d\d:\d\d:\d\d\]|FAIL|error:')
    if not out.is_file(): raise Fail(f'no IPA at {out}; see {b.logs / "build_ipa.log"}')
    say(f'IPA: {out} ({out.stat().st_size // (1 << 20)} MiB; romfs.img goes to the app\'s Documents on the phone, see docs/IPHONE.md)')
    return out


def st_ipa_windows(b):
    """The unsigned IPA on Windows: the fork's win/build_app.py (LLVM clang + ld64.lld with the iPhoneOS SDK, the host's shaders through Metal Developer Tools for
    Windows) with the same inputs as build_ipa.sh; the executable carries both kernel entitlements in an ad-hoc signature, the game files stay out (romfs.id instead)."""
    (b.out / 'phone_env.txt').write_text(PHONE_ENV)
    b.outputs.mkdir(parents=True, exist_ok=True)
    out = b.outputs / f'{NAME.replace(" ", "")}-unsigned.ipa'
    env = b.env(W=str(b.out / 'ipa'), PATCHED=str(b.patched), ROMFS=str(b.romfs_img), MSL_IOS=str(b.ios), AOC='', DM_ENV_FILE=str(b.out / 'phone_env.txt'), DM_SHOW_FPS='1',
                DM_PSO_GPUS='none', DM_LANG='en-US', DM_BUNDLE_ID=f'org.nxnative.{TITLE.lower()}', NXW_EXTENDED_VA='1', NXW_ROMFS_SEPARATE='1', NXW_ENTITLEMENTS='1')
    (b.out / 'ipa').mkdir(parents=True, exist_ok=True)
    say('building the IPA (unsigned; LLVM and Metal Developer Tools for Windows)')
    run([PY, NX / 'win/build_app.py', out], b.logs / 'build_ipa.log', env=env, show=r'^\[\d\d:\d\d:\d\d\]|FAIL|error')
    if not out.is_file(): raise Fail(f'no IPA at {out}; see {b.logs / "build_ipa.log"}')
    say(f'IPA: {out} ({out.stat().st_size // (1 << 20)} MiB; sign it with your own team, then romfs.img goes to the app\'s Documents: docs/IPHONE.md)')
    return out


def st_xcode(b, team):
    """An Xcode project for the game, to sign with your Apple developer team (nxs2.py sign, or Xcode itself)."""
    (b.out / 'phone_env.txt').write_text(PHONE_ENV)
    env = ipa_env(b); env['DM_TEAM_ID'] = team; env['DM_XCODE_PROJECT_DIR'] = str(b.out / 'xcode')
    say(f'preparing the Xcode project (team {team})')
    run(['bash', NX / 'dm/ios/tools/build_ipa.sh', '--prepare-xcode'], b.logs / 'prepare_xcode.log', env=env, show=r'^\[\d\d:\d\d:\d\d\]|FAIL|error:')
    proj = b.out / 'xcode' / 'DmIOS.xcodeproj'
    if not proj.is_dir(): raise Fail(f'no project at {proj}; see {b.logs / "prepare_xcode.log"}')
    say(f'Xcode project: {proj}')
    return proj


def sign(args):
    """Archive and export a development IPA from the prepared Xcode project with automatic signing (your Apple ID signed into Xcode, the
    iPhone's UDID on the team). The first run should happen in Terminal on the Mac itself: Xcode may ask for the keychain."""
    if WIN: raise Fail('signing with Xcode needs a Mac; on Windows, sign the unsigned IPA with your own signing tool and team (docs/IPHONE.md)')
    b = Build(args.build); team = args.team_id
    x = b.out / 'xcode'; d = b.out / 'build-ios'; ar = d / 'app.xcarchive'; ex = d / 'export'
    if not (x / 'DmIOS.xcodeproj').is_dir(): raise Fail(f'no Xcode project at {x}: run nxs2.py build ... --xcode --team-id {team} first')
    lst = subprocess.run(['xcodebuild', '-project', x / 'DmIOS.xcodeproj', '-list', '-json'], capture_output=True, text=True)
    schemes = json.loads(lst.stdout).get('project', {}).get('schemes', []) if lst.returncode == 0 else []
    scheme = next((s for s in schemes if TITLE in s), schemes[0] if schemes else None)
    if not scheme: raise Fail(f'no scheme in {x}: {lst.stderr.strip()[-300:]}')
    d.mkdir(parents=True, exist_ok=True)
    if ex.is_dir(): shutil.rmtree(ex)
    (d / 'export.plist').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>method</key><string>{args.method}</string>
<key>teamID</key><string>{team}</string>
<key>signingStyle</key><string>automatic</string>
<key>destination</key><string>export</string>
<key>compileBitcode</key><false/>
<key>thinning</key><string>&lt;none&gt;</string>
<key>stripSwiftSymbols</key><true/>
</dict></plist>
''')
    say(f'archiving {scheme}')
    run(['xcodebuild', '-project', x / 'DmIOS.xcodeproj', '-scheme', scheme, '-configuration', 'Release', '-destination', 'generic/platform=iOS',
         '-archivePath', ar, '-allowProvisioningUpdates', 'archive'], b.logs / 'xcode_archive.log', cwd=x, heartbeat=20, show=r'error:|\*\* ARCHIVE')
    say('exporting the IPA')
    run(['xcodebuild', '-exportArchive', '-archivePath', ar, '-exportOptionsPlist', d / 'export.plist', '-exportPath', ex, '-allowProvisioningUpdates'],
        b.logs / 'xcode_export.log', cwd=x, heartbeat=20, show=r'error:|EXPORT')
    ipas = sorted(ex.glob('*.ipa'))
    if not ipas: raise Fail(f'the export wrote no IPA; see {b.logs / "xcode_export.log"}')
    b.outputs.mkdir(parents=True, exist_ok=True)
    out = b.outputs / f'{NAME.replace(" ", "")}-signed.ipa'; shutil.copyfile(ipas[0], out)
    say(f'signed IPA: {out}  (install: pymobiledevice3 apps install "{out}"; then docs/IPHONE.md for romfs.img)')


# ---------------------------------------------------------------- report
def coverage(b):
    """(file shaders in the map, runtime shaders known, runtime shaders present)"""
    known = {l.split('\t')[0] for l in (PROFILE_SRC / 'runtime_keys.tsv').read_text().splitlines() if l.strip() and not l.startswith('#')}
    keys = set()
    smap = b.profile / 'shader_map.tsv'
    if smap.is_file():
        for l in smap.read_text().splitlines():
            if l.startswith('#') or l.startswith('package\t') or not l.strip(): continue
            f = l.split('\t')
            if len(f) > 3: keys.add(f[3])
    return len(keys - known), len(known), len(keys & known)


def report(b):
    files, known, present = coverage(b)
    say(f'shaders: {files} from the game files; {present} of the {known} known run-time shaders')
    if present < known:
        print(f'''
  {known - present} shaders the game builds while it runs (opening, character creator, first area) are not in this build yet;
  a draw that needs one is skipped until it is. They are recorded on your device as you play
  (the apps have DM_NVN_SHDUMP on); then:   nxs2.py refresh "{b.out}"   adds them and rebuilds the outputs.
  (iPhone: pull the app's Documents/shdump first: tools/phone_pull.py, see docs/IPHONE.md)''')


def status(args):
    b = Build(args.build)
    for name, p in (('ExeFS', b.exefs / 'main'), ('RomFS image', b.romfs_img), ('rewritten modules', b.patched / 'main.patched.bin'), ('Mac host', b.host),
                    ('fonts', b.font / 'nintendo_ext.ttf'), ('shader corpus', b.ctl / 'hostkeys.tsv'), ('lowered shaders', b.maxwell / 'results.tsv'),
                    ('shader package', b.pkg), ('shader map', b.profile / 'shader_map.tsv')):
        print(f'  {"ok     " if p.exists() else "missing"}  {name}')
    try:
        lib = b.lib(); print(f'  {"ok     " if (lib / "inputs.tsv").is_file() else "missing"}  Mac shader libraries ({lib.name})')
        print(f'  {"ok     " if (b.ios / lib.name / "inputs.tsv").is_file() else "missing"}  iPhone shader libraries')
    except Fail as e: print(f'  ?        shader libraries: {e}')
    for p in sorted(b.outputs.glob('*')) if b.outputs.is_dir() else []: print(f'  output   {p}')
    if (b.profile / 'shader_map.tsv').is_file(): report(b)


# ---------------------------------------------------------------- commands
def build(args):
    need_ios = True if WIN else not args.no_ios
    if WIN and (args.mac_app or args.xcode): raise Fail('the Mac app and the Xcode project need a Mac; on Windows the output is the iPhone IPA (--ipa)')
    bad = doctor(need_ios=need_ios, quiet=True)
    if bad: doctor(need_ios=need_ios); raise Fail('install the missing tools above (nxs2.py doctor), then build again')
    if not (NX / 'dm/host/Makefile').is_file() or not AMPERE.is_file() or (WIN and not (NX / 'win/build_app.py').is_file()): raise Fail(f'run nxs2.py setup first (nxnative at {NX}, ampere-lower at {AMPERE})')
    exefs, romfs = find_dump(args)
    if args.out: out = pathlib.Path(args.out).expanduser()
    elif args.dump: d = pathlib.Path(args.dump).expanduser().resolve(); out = d.parent / f'{d.name}_native'   # beside the dump, never inside it
    else: raise Fail('--exefs/--romfs need --out <build folder>')
    b = Build(out); jobs = JOBS
    say(f'{NAME} v{VERSION}: dump {exefs.parent}, build folder {b.out}')
    say(f'load: {jobs} worker(s) of {os.cpu_count()} CPU threads, low priority' + ('' if args.full_speed or args.jobs else ' (gentle default; --full-speed uses them all)'))
    st_profile(b)
    st_extract(b, exefs, romfs)
    st_rewrite(b)
    if not WIN: st_host(b, jobs); st_font(b)   # the Mac host and its fonts: the Windows build makes the iPhone app only (its fonts come from win/toolchain.py)
    st_corpus(b, romfs, args.dumps or [])
    st_lower(b, jobs)
    st_package(b)
    st_chain(b, need_ios)
    if args.mac_app: st_mac_app(b)
    if args.ipa: st_ipa_windows(b) if WIN else st_ipa(b)
    if args.xcode:
        if not args.team_id: raise Fail('--xcode needs --team-id <your Apple developer team ID>')
        st_xcode(b, args.team_id)
    if not (args.mac_app or args.ipa or args.xcode): say('no output asked for (--mac-app, --ipa, --xcode); the build folder is complete for nxs2.py run')
    report(b)
    say('done')


def default_dump_dirs(b):
    dirs = [b.rundir / 'shdump', pathlib.Path.home() / 'Library/Application Support/nxnative' / TITLE / 'shdump']
    if b.phone.is_dir(): dirs += sorted(p for p in b.phone.glob('*/shdump') if p.is_dir())
    return [d for d in dirs if d.is_dir()]


def refresh(args):
    b = Build(args.build)
    if not (b.ctl / 'hostkeys.tsv').is_file(): raise Fail(f'{b.out} holds no finished build')
    dirs = [pathlib.Path(d).expanduser() for d in args.dumps] or default_dump_dirs(b)
    if not dirs: raise Fail('no shader dumps found: give the folder(s) (DM_NVN_SHDUMP output: <key>.ctl + <key>.code files)')
    before = coverage(b)
    add_dumps(b, dirs)
    st_lower(b, JOBS); st_package(b)
    st_chain(b, WIN or (b.ios / b.lib().name / 'inputs.tsv').is_file())   # iPhone libraries again when the build had them
    if not WIN and (b.outputs / f'{NAME}.app').is_dir(): st_mac_app(b)
    if b.outputs.is_dir() and any(b.outputs.glob('*-unsigned.ipa')): st_ipa_windows(b) if WIN else st_ipa(b)
    if not WIN and (b.out / 'xcode' / 'DmIOS.xcodeproj').is_dir():
        team = args.team_id
        if not team and (b.out / 'xcode' / 'project.yml').is_file():
            m = re.search(r'DEVELOPMENT_TEAM\s*[:=]\s*"?([A-Z0-9]{10})', (b.out / 'xcode' / 'project.yml').read_text()); team = m.group(1) if m else None
        if team: st_xcode(b, team); say(f'the Xcode project is refreshed: nxs2.py sign "{b.out}" --team-id {team} makes the new signed IPA')
        else: say('the Xcode project is not refreshed (give --team-id)')
    after = coverage(b)
    say(f'run-time shaders: {before[2]} -> {after[2]} of {after[1]} known' + (f', plus {after[0] - before[0]} not in the known list' if after[0] != before[0] else ''))
    report(b); say('done')


def run_mac(args):
    """The game in a window on this Mac, straight from the build folder (the host, not the app): saves in <build>/run/save, dumps in <build>/run/shdump."""
    if WIN: raise Fail('playing from the build folder needs a Mac (the host runs the game\'s arm64 code natively); on Windows the output is the iPhone IPA')
    b = Build(args.build)
    for p in (b.host, b.romfs_img, b.patched / 'main.patched.bin'):
        if not p.exists(): raise Fail(f'{p} is missing: run nxs2.py build first')
    rd = b.rundir
    for d in ('save', 'temp', 'shdump'): (rd / d).mkdir(parents=True, exist_ok=True)   # the host writes dumps only into a folder that exists
    env = dict(os.environ, DM_ROOT=str(b.out), DM_ROMFS=str(b.romfs_img), DM_SAVE=str(rd / 'save'), DM_TEMP=str(rd / 'temp'), DM_MSL_DIR=str(b.cache),
               DM_FONT_DIR=str(b.font), DM_NVN_SHMAP=str(b.profile / 'shader_map.tsv'), DM_LANG=args.lang, DM_WINDOW='1', DM_AUDIO='1', DM_NVN_SHDUMP='shdump')
    for kv in args.env: k, _, v = kv.partition('='); env[k] = v
    say(f'starting {NAME} (window; Ctrl-C here or close the window to stop; log {rd / "host.log"})')
    with open(rd / 'host.log', 'w') as log:
        try: rc = subprocess.call([str(b.host), str(b.patched)], env=env, cwd=rd, stdout=log, stderr=subprocess.STDOUT)
        except KeyboardInterrupt: rc = 130
    n = len(list((rd / 'shdump').glob('*.ctl'))) if (rd / 'shdump').is_dir() else 0
    say(f'host exited ({rc}); {n} shaders recorded in {rd / "shdump"}' + (f'  ->  nxs2.py refresh "{b.out}"' if n else ''))


ICON_LOGO = (1205, 140, 1890, 610)   # the title screen's logo badge, in a 1920x1080 picture
ICON_AREA = (1196, 80, 1896, 780)    # the square around it: below the version number, above "Press A"


def pillow():
    try: from PIL import Image; return Image
    except ImportError: raise Fail('the icon needs Pillow: python3 -m pip install pillow')


def is_title(pic):
    """The title screen: its logo badge is lime green where every other screen (logos, language question, the opening) has none (measured: 21 % against 0)."""
    im = pillow().open(pic).convert('RGB').resize((1920, 1080)).crop(ICON_LOGO)
    return sum(1 for r, g, b in pixels(im) if g > 190 and 120 <= r <= 235 and b < 150 and g >= r) / (im.width * im.height) > 0.08


def pixels(im): return im.get_flattened_data() if hasattr(im, 'get_flattened_data') else im.getdata()   # Pillow 13 renamed getdata


def make_icon(pic, out):
    """The apps' icon from a picture of the title screen: the logo badge cut out along its white edge, on a soft blur of the screen around it, 1024x1024.
    A square picture (an icon your dumping tool saved, say) is taken as it is."""
    Image = pillow(); from PIL import ImageChops, ImageDraw, ImageEnhance, ImageFilter
    im = Image.open(pic).convert('RGB')
    if abs(im.width / im.height - 1) < 0.05: im.resize((1024, 1024), Image.LANCZOS).save(out); return
    if abs(im.width / im.height - 16 / 9) > 0.05: raise Fail(f'{pic} is {im.width}x{im.height}: give a 16:9 picture of the title screen, or a square icon')
    if not is_title(pic): raise Fail(f'{pic} does not show the title screen (no logo where the title screen has it)')
    im = im.resize((1920, 1080), Image.LANCZOS)
    crop = im.crop(ICON_LOGO); r, g, b = crop.split()
    edge = ImageChops.darker(ImageChops.darker(r, g), b).point(lambda v: 255 if v > 215 else 0)   # the badge's white outline
    for c in ((0, 0), (crop.width - 1, 0), (0, crop.height - 1), (crop.width - 1, crop.height - 1)):   # everything reachable from a corner without crossing it is outside
        if edge.getpixel(c) == 0: ImageDraw.floodfill(edge, c, 128, thresh=0)
    mask = edge.point(lambda v: 0 if v == 128 else 255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    if not 0.35 < sum(v > 128 for v in pixels(mask)) / (crop.width * crop.height) < 0.75: raise Fail(f'no logo found where the title screen has it in {pic}')
    bg = im.crop(ICON_AREA).filter(ImageFilter.GaussianBlur(50)).resize((1024, 1024), Image.LANCZOS)
    bg = ImageEnhance.Color(ImageEnhance.Brightness(bg).enhance(1.18)).enhance(1.3)
    w = 880; logo = crop.resize((w, round(crop.height * w / crop.width)), Image.LANCZOS); m = mask.resize(logo.size, Image.LANCZOS)
    x, y = (1024 - logo.width) // 2, (1024 - logo.height) // 2
    bg.paste((40, 60, 40), (x + 8, y + 14), m.filter(ImageFilter.GaussianBlur(14)).point(lambda v: v * 0.45))   # a soft shadow
    bg.paste(logo, (x, y), m); bg.save(out)


def played_save(b, given):
    """A save past the game's opening: a new one asks for the language, then goes straight into the opening and never shows the title screen.
    The given folder, else the build folder's (nxs2.py run), else the Mac app's; it is copied, never used itself."""
    cands = [pathlib.Path(given).expanduser()] if given else [b.rundir / 'save', pathlib.Path.home() / 'Library/Application Support/nxnative' / TITLE / 'save']
    for d in cands:
        if (d / 'G00').is_dir(): return d
    if given: raise Fail(f'{given} holds no game save (no G00 folder)')
    raise Fail('the title screen only shows once a game has been started: play past the opening once (nxs2.py run, or the Mac app), '
               'or give a picture of the title screen (nxs2.py icon <build> <picture>)')


def capture_title(b, png, save):
    """A picture of the title screen from the game itself (Mac): the host on a copy of a played save, until the screen shows the title's logo."""
    for p in (b.host, b.romfs_img, b.patched / 'main.patched.bin'):
        if not p.exists(): raise Fail(f'{p} is missing: run nxs2.py build first')
    rd = b.rundir / 'icon'; shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
    shutil.copytree(save, rd / 'save'); (rd / 'temp').mkdir()
    ctl = rd / 'ctl.txt'; ctl.write_text('')
    env = dict(os.environ, DM_ROOT=str(b.out), DM_ROMFS=str(b.romfs_img), DM_SAVE=str(rd / 'save'), DM_TEMP=str(rd / 'temp'), DM_MSL_DIR=str(b.cache),
               DM_FONT_DIR=str(b.font), DM_NVN_SHMAP=str(b.profile / 'shader_map.tsv'), DM_LANG='en-US', DM_AUDIO='0', DM_CTL=str(ctl))
    say('starting the game to take a picture of its title screen (up to 3 minutes)')
    with open(rd / 'host.log', 'w') as log:
        p = subprocess.Popen([str(b.host), str(b.patched)], env=env, cwd=rd, stdout=log, stderr=subprocess.STDOUT)
    try:
        t0, n, seen = time.time(), 0, None
        while time.time() - t0 < 180 and p.poll() is None:
            n += 1; shot = rd / f'shot{n}.png'
            with open(ctl, 'a') as f: f.write(f'shot {shot}\n')
            for _ in range(20):
                if shot.is_file() and shot.stat().st_size: break
                time.sleep(0.25)
            if shot.is_file() and shot.stat().st_size > 150_000 and is_title(shot):   # the boot's frames are blank and compress to a few dozen KB
                if seen: shutil.copy(shot, png); return
                seen = time.time()
            time.sleep(3 if seen else 2)   # once it shows, one more picture a moment later: the logo has finished coming in
        raise Fail(f'the title screen did not show within 3 minutes; see {rd / "host.log"} and the pictures beside it')
    finally:
        p.terminate()
        try: p.wait(10)
        except subprocess.TimeoutExpired: p.kill()


def icon(args):
    b = Build(args.build)
    if args.picture: pic = pathlib.Path(args.picture).expanduser()
    elif WIN: raise Fail('on Windows give a picture: a screenshot of the title screen taken on your Switch 2 (Capture button), or a square icon')
    else: pic = b.rundir / 'title.png'; capture_title(b, pic, played_save(b, args.save))
    make_icon(pic, b.icon)
    say(f'icon: {b.icon}; build again (same command) to put it into the apps')


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('-v', '--verbose', action='store_true', help='show every line the tools print (default: a heartbeat and the important lines; everything goes to <build>/logs)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('setup'); s.add_argument('--no-ios', action='store_true'); s.set_defaults(f=setup)
    s = sub.add_parser('doctor'); s.add_argument('--no-ios', action='store_true'); s.set_defaults(f=lambda a: sys.exit(1 if doctor(not a.no_ios) else 0))
    s = sub.add_parser('build')
    s.add_argument('dump', nargs='?', help='the dump folder (<game>/updates/<version>/{exefs,romfs} inside, or exefs/ and romfs/ directly)')
    s.add_argument('--exefs'); s.add_argument('--romfs'); s.add_argument('--out', help=f'the build folder (default: <dump>/../{TITLE}_native)')
    s.add_argument('--mac-app', action='store_true'); s.add_argument('--ipa', action='store_true'); s.add_argument('--xcode', action='store_true')
    s.add_argument('--team-id'); s.add_argument('--dumps', nargs='*', default=[]); s.add_argument('--no-ios', action='store_true')
    s.add_argument('-j', '--jobs', type=int, help='worker processes at once (default: all cores on a Mac, a quarter of them, at most 8, on a PC)')
    s.add_argument('--full-speed', action='store_true', help='every CPU thread (fastest; a long all-core load, see docs/WINDOWS.md)')
    s.set_defaults(f=build)
    s = sub.add_parser('refresh'); s.add_argument('build'); s.add_argument('dumps', nargs='*'); s.add_argument('--team-id')
    s.add_argument('-j', '--jobs', type=int); s.add_argument('--full-speed', action='store_true'); s.set_defaults(f=refresh)
    s = sub.add_parser('run'); s.add_argument('build'); s.add_argument('--lang', default='en-US'); s.add_argument('env', nargs='*', help='extra DM_* switches, KEY=VALUE'); s.set_defaults(f=run_mac)
    s = sub.add_parser('sign'); s.add_argument('build'); s.add_argument('--team-id', required=True); s.add_argument('--method', default='debugging', help='export method: debugging (default), ad-hoc, release-testing'); s.set_defaults(f=sign)
    s = sub.add_parser('status'); s.add_argument('build'); s.set_defaults(f=status)
    s = sub.add_parser('icon', help='the apps\' icon from the title screen: your own game on a Mac, or a picture of it')
    s.add_argument('build'); s.add_argument('picture', nargs='?', help='a 16:9 screenshot of the title screen (your Switch 2\'s Capture button), or a square icon')
    s.add_argument('--save', help='a save past the game\'s opening to boot from (copied; default: the build folder\'s, then the Mac app\'s)'); s.set_defaults(f=icon)
    a = ap.parse_args(); VERBOSE = a.verbose
    if a.cmd == 'build' and not (a.dump or (a.exefs and a.romfs)): ap.error('build needs the dump folder, or --exefs and --romfs')
    global JOBS
    JOBS = max(1, getattr(a, 'jobs', None) or ((os.cpu_count() or 4) if getattr(a, 'full_speed', False) else gentle_jobs()))
    if a.cmd in ('setup', 'build', 'refresh'): lower_priority()
    try: a.f(a)
    except Fail as e: print(f'\n[FAIL] {e}', file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt: print('\ninterrupted', file=sys.stderr); sys.exit(130)


VERBOSE = False
JOBS = 4   # set in main() from -j / --full-speed / gentle_jobs()
if __name__ == '__main__': main()
