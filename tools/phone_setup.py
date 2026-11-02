# phone_setup.py [--romfs <RomFS.img>] [--env <file>]: the game app's Documents folder on the iPhone over USB (pymobiledevice3 house_arrest,
# Documents only): writes env.txt (switches the app reads at boot; default: shader dumps on), creates the shdump folder, and with --romfs streams
# the game image (<build>/extract/RomFS.img) to Documents/romfs.img in 16 MB pieces with a progress line every 512 MB (about 3 minutes for 6 GB).
# Phone unlocked, trusted, Developer Mode on, the app installed (pymobiledevice3 apps install <ipa>).
# Run: python -W ignore tools/phone_setup.py [--romfs path/to/RomFS.img] [--env env.txt] [--bundle org.nxnative.<title id>]
import asyncio
import inspect
import os
import sys
import time

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.house_arrest import DOCUMENTS_ROOT, HouseArrestService

BUNDLE = "org.nxnative.0400a3a01ee4c000"
ENV = "# switches the app reads at boot (one KEY=VALUE per line); these override the build's own dm/env.txt\nDM_NVN_SHDUMP=shdump\n"
D = DOCUMENTS_ROOT.rstrip("/")


def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv): return sys.argv[i + 1]
        sys.exit(f"{flag} needs a value")
    return default


async def A(x):
    return await x if inspect.isawaitable(x) else x


async def main():
    bundle = arg("--bundle", BUNDLE); romfs = arg("--romfs"); envf = arg("--env")
    env = open(envf, encoding="utf-8").read() if envf else ENV
    ld = await A(create_using_usbmux())
    afc = await HouseArrestService.create(ld, bundle, documents_only=True)
    print("Documents before:", await A(afc.listdir(D)))   # VendDocuments denies anything outside /Documents
    await A(afc.set_file_contents(f"{D}/env.txt", env.encode()))
    if not await A(afc.exists(f"{D}/shdump")):
        await A(afc.makedirs(f"{D}/shdump"))
    if romfs:
        size = os.path.getsize(romfs)
        if await A(afc.exists(f"{D}/romfs.img")):
            print("romfs.img already on the phone:", (await A(afc.stat(f"{D}/romfs.img"))).get("st_size"), "bytes; rewriting")
        t0 = time.time(); done = 0; mark = 0
        h = await A(afc.fopen(f"{D}/romfs.img", "w"))
        with open(romfs, "rb") as f:
            while True:
                b = f.read(16 << 20)
                if not b:
                    break
                await A(afc.fwrite(h, b))
                done += len(b)
                if done - mark >= (512 << 20) or done == size:
                    mark = done; el = max(time.time() - t0, 1e-3)
                    print(f"[{time.strftime('%H:%M:%S')}] {done / 2**30:.2f} of {size / 2**30:.2f} GiB ({done / el / 2**20:.1f} MiB/s)", flush=True)
        await A(afc.fclose(h))
        got = (await A(afc.stat(f"{D}/romfs.img"))).get("st_size")
        print("romfs.img on the phone:", got, "bytes; local", size, "OK" if got == size else "[RED] SIZE MISMATCH")
    print("Documents after:", await A(afc.listdir(D)))
    for n in ("env.txt", "shdump", "romfs.img", "log.txt", "mem.tsv"):
        if await A(afc.exists(f"{D}/{n}")):
            print(f"  {n}: {(await A(afc.stat(f'{D}/{n}'))).get('st_size')} bytes")
    await A(afc.close())


asyncio.run(main())
