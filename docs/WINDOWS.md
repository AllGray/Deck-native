# Building on Windows

A Windows PC builds the **iPhone app** (the IPA). It cannot build the Mac app or play the game itself: the host runs the
game's arm64 code natively, which needs an Apple chip. Everything else of the build runs on Windows, through the tools
[nxnative's Windows port](https://github.com/zenvyzenshiro/NXNativeWindows) maps Apple's tools onto: the shaders go
through Apple's own Metal compiler for Windows, the app is compiled with LLVM against the iPhone SDK.

**Status (2026-11-03): not working yet.** On an iPhone the Windows-built app installs, starts and runs the game (sound,
input, 30 fps), but the picture stays black apart from some text. The Metal source of the shaders is byte-identical to the
Mac build's, so the difference is in the Windows compile (Apple's Metal compiler for Windows, or the LLVM-built app).

## What to install (once)

`Pokopia Native Builder.cmd` → **Check tools** (or `python nxs2.py doctor`) lists these and says what is missing:

| Tool | Where | Why |
|---|---|---|
| Python 3.12 or newer | python.org, tick "Add python.exe to PATH" (Tk comes with it) | the build script and the window |
| `python -m pip install "capstone>=5,<6" lz4 fonttools pillow` | | the code rewriter, the shader tools, the fonts, the app icon |
| .NET 10 runtime | dotnet.microsoft.com | the shader translator |
| LLVM for Windows | github.com/llvm/llvm-project/releases, `LLVM-<version>-win64.exe` | compiles and links the iPhone app (`clang`, `ld64.lld`) |
| Metal Developer Tools for Windows | developer.apple.com/download (Apple ID) | Apple's Metal compiler: the shaders and the host's own Metal code |
| Vulkan SDK | vulkan.lunarg.com | its `spirv-cross.exe` turns the translated shaders into Metal source |
| git | git-scm.com | fetches nxnative and its Windows port at the pinned commits |
| Xcode (`Xcode_27.x.xip`) | developer.apple.com/download (Apple ID; about 8 GB, downloaded once) | the iPhoneOS SDK the app is built against, extracted below |

The iPhoneOS SDK is taken out of the Xcode archive after **Setup** has run (Setup fetches the extractor):

```
python nxnative\win\sdk\extract_xip.py "C:\path\to\Xcode_27.1.xip"
```

A few minutes; the SDK lands in `nxnative\win\sdk\out\`, where the build finds it (`NXW_IOS_SDK=<folder>` names another).

Apple's licence terms for Xcode and its SDK are between you and Apple; the Windows port's README says to read them
before using the SDK off a Mac. The Windows build was made and tested here with LLVM 23, Metal Developer Tools 6.2 and
the iPhoneOS 27 SDK.

## Build

Double-click `Pokopia Native Builder.cmd`: **Setup** once, then choose your dump folder and press **Build** with
"iPhone IPA" ticked. From a terminal:

```
python nxs2.py setup
python nxs2.py build "C:\path\to\your dump folder" --ipa
```

The stages are the Mac's minus the Mac host: the game image, the rewritten code, the shader corpus, the lowering, the
package, the Metal compile (iOS libraries only), the app. The Metal compile is the long part here too.

**Load.** By default the build runs a quarter of your CPU threads (at most 8) at low priority, so the PC stays cool and
usable while it works; expect an hour or so. **Full speed** in the window (`--full-speed`, or `-j N`) uses every thread
and is much faster. Only use it on a machine you trust with a long all-core load: my own PC, an i9-13900KF on an old
BIOS, blue-screened a minute into a full-speed shader compile. 13th and 14th generation Intel Core CPUs need the BIOS
update with Intel's 2024 microcode fix (0x12B or newer) for that; **Check tools** warns when yours does not have it.

The output is `<build folder>\out\PokemonPokopia-unsigned.ipa`: the app without the game files, its executable carrying
the two kernel entitlements in an ad-hoc signature, plus `romfs.id`, the identity of your game image.

## Sign and install

The IPA has to be signed with a **paid Apple Developer Program** team (see the README: the game's memory needs the
Extended Virtual Addressing entitlement, which only that signing carries). On a Mac that is Xcode; on Windows it is a
signing tool that keeps the entitlements the executable asks for and signs with your team's certificate and a
profile that grants them. I signed my builds on a Mac and have not tested a Windows signing tool for this; if you make
one work, say which in the issues.

Then, as on the Mac ([IPHONE.md](IPHONE.md)): `pymobiledevice3 apps install <ipa>`, and `tools\phone_setup.py --romfs
<build folder>\extract\RomFS.img` for the game files. The shaders the game makes while running come back with
`tools\phone_pull.py` and go in with `python nxs2.py refresh "<build folder>"`, which rebuilds the IPA.
