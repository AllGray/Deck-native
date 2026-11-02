# phone_pull.py <out folder> [--bundle org.nxnative.<title id>]: copy the app's run files from the iPhone's Documents (log.txt, log.prev.txt,
# mem.tsv, shdump/*, pso_*, save folders; everything but romfs.img) to <out folder>/ over USB (pymobiledevice3 house_arrest). Recursive; an entry
# that cannot be read is reported and skipped. The shdump folder it brings back is what `nxs2.py refresh <build> <out folder>/shdump` adds to the build;
# <build>/phone/<name> is where refresh looks by itself.
# Run: python -W ignore tools/phone_pull.py <build>/phone/run-1
import asyncio
import inspect
import os
import sys

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.house_arrest import DOCUMENTS_ROOT, HouseArrestService

BUNDLE = "org.nxnative.0400a3a01ee4c000"
D = DOCUMENTS_ROOT.rstrip("/")


async def A(x):
    return await x if inspect.isawaitable(x) else x


async def pull_dir(afc, remote, local, depth=0):
    os.makedirs(local, exist_ok=True)
    names = await A(afc.listdir(remote))
    count = 0
    for n in names:
        if depth == 0 and n == "romfs.img":
            continue
        p = f"{remote}/{n}"
        l = os.path.join(local, n)
        try:
            if await A(afc.isdir(p)):
                sub = await pull_dir(afc, p, l, depth + 1)
                count += sub
                if depth == 0:
                    print(f"  {n}/: {sub} files")
            else:
                data = await A(afc.get_file_contents(p))
                with open(l, "wb") as f:
                    f.write(data)
                count += 1
                if depth == 0:
                    print(f"  {n}: {len(data)} bytes")
        except Exception as e:
            print(f"  [skip] {p}: {e}")
    return count


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    bundle = sys.argv[sys.argv.index("--bundle") + 1] if "--bundle" in sys.argv else BUNDLE
    if not args:
        sys.exit("usage: phone_pull.py <out folder> [--bundle id]")
    out = args[0]
    ld = await A(create_using_usbmux())
    afc = await HouseArrestService.create(ld, bundle, documents_only=True)
    print("Documents:", await A(afc.listdir(D)))
    n = await pull_dir(afc, D, out)
    await A(afc.close())
    print(f"-> {out} ({n} files)")


asyncio.run(main())
