#!/usr/bin/env python3
# Builds meshxlate on the Deck: Eglantine's recompiler and sirit, no CMake. Paths are the Deck's.
import os,re,subprocess,sys,glob,concurrent.futures as cf
R="/run/media/deck/SDCard/mkw-native"; T=R+"/tc"; X=T+"/mx"; E=R+"/egl/src"; ME=R+"/src/windows/src/meshxlate"
CXX=T+"/llvm-23.1.4/bin/clang++"
BASE=["--target=x86_64-linux-gnu","--sysroot="+T+"/sysroot","-std=c++23","-O1","-g0","-w","-DFMT_HEADER_ONLY","-DSIRIT_USE_SYSTEM_SPIRV_HEADERS","-fexceptions","-fPIC",
 "-I"+ME+"/shim","-I"+E,"-I"+X+"/boost","-I"+X+"/fmt/include","-I"+X+"/sirit/include","-I"+X+"/sirit/src","-I"+X+"/spirv-headers/include","-I"+X+"/spirv-headers/include/spirv/unified1"]
src=[f for f in glob.glob(E+"/shader_recompiler/**/*.cpp",recursive=True) if "/glsl/" not in f and "/glasm/" not in f]
src+=glob.glob(X+"/sirit/src/**/*.cpp",recursive=True)+[ME+"/main.cpp"]
extra=[E+"/common/"+n for n in ("assert.cpp",) if False]
os.makedirs(X+"/obj",exist_ok=True)
def one(f):
    o=X+"/obj/"+re.sub(r"[^A-Za-z0-9]+","_",f[-70:])+".o"
    if os.path.exists(o) and os.path.getmtime(o)>os.path.getmtime(f) and "main.cpp" not in f: return o,None
    r=subprocess.run(["nice","-n","12",CXX]+BASE+["-c",f,"-o",o],capture_output=True,text=True)
    return o,(f+"\n"+r.stderr[:1500]) if r.returncode else None
errs=[];objs=[]
with cf.ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv)>1 else 5) as ex:
    for o,e in ex.map(one,src):
        objs.append(o)
        if e: errs.append(e); print(e[:900]); sys.stdout.flush()
        if len(errs)>6: break
print(len(src),"sources",len(errs),"errors")
if not errs:
    r=subprocess.run([CXX,"--target=x86_64-linux-gnu","--sysroot="+T+"/sysroot","-fuse-ld=lld","-o",X+"/meshxlate"]+objs,capture_output=True,text=True)
    print(r.stderr[:2500] or "linked "+X+"/meshxlate")
