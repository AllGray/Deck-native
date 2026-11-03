# Building on Windows

A Windows PC builds the **iPhone app** (the IPA). It cannot build the Mac app or play the game itself: the host runs the
game's arm64 code natively, which needs an Apple chip. Everything else of the build runs on Windows, through the tools
[nxnative's Windows port](https://github.com/zenvyzenshiro/NXNativeWindows) maps Apple's tools onto: the shaders go
through Apple's own Metal compiler for Windows, the app is compiled with LLVM against the iPhone SDK.

**Status (2026-11-03): works.** Built and signed on a PC, the app plays on my iPhone like the Mac-built one. One thing is
different from a Mac: the PC cannot run the game, so it cannot record the shaders the game makes while it runs (see the
README's shader section). Until your phone has recorded them once, the title screen is black apart from its text; the
steps for that are at the end of this page.

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

For the home-screen icon, take a screenshot of the game's title screen on your Switch 2 (Capture button), copy it to the
PC and run `python nxs2.py icon "<build folder>" title.jpg` before the build (or build again afterwards).

## Sign and install

The IPA has to be signed with a **paid Apple Developer Program** team (see the README: the game's memory needs the
Extended Virtual Addressing entitlement, which only that signing carries). On Windows the build tool signs it itself,
with [rcodesign](https://github.com/indygreg/apple-platform-rs) (fetched by Setup and checked against its published
checksum). It needs two files from your developer account:

- **Your Apple Development certificate with its private key, as a `.p12`.** If you have a Mac with Xcode, export it from
  Keychain Access (My Certificates, right-click "Apple Development: ...", Export). Without a Mac: make a certificate
  signing request with `tools-bin\rcodesign.exe generate-certificate-signing-request`, upload it at developer.apple.com →
  Certificates (+, Apple Development), download the certificate and combine it with the key into a `.p12`.
- **A development provisioning profile (`.mobileprovision`)** for the bundle ID `org.nxnative.0400a3a01ee4c000`, with your
  iPhone on it. At developer.apple.com: Identifiers (+, App IDs, that bundle ID, and tick **Increased Memory Limit** and
  **Extended Virtual Addressing** under Capabilities), Devices (your iPhone's UDID), Profiles (+, iOS App Development,
  that App ID, your certificate, your iPhone). If Xcode already signed this app for you, that profile works too.

Then **Sign IPA** in the window (it asks for both files and the certificate's password), or:

```
python nxs2.py sign "<build folder>" --p12 my-cert.p12 --profile Pokopia.mobileprovision
```

The result is `<build folder>\out\PokemonPokopia-signed.ipa`. The tool checks that the profile matches the app and
grants both memory capabilities, and after signing it reads the signature back and stops if an entitlement is missing.

Then, as on the Mac ([IPHONE.md](IPHONE.md)): `pymobiledevice3 apps install <ipa>`, and `tools\phone_setup.py --romfs
<build folder>\extract\RomFS.img` for the game files.

**The first run (once).** Start the game on the phone and let it reach the title screen: it is black apart from the
version number and "Settings", the music plays. Press A and play into the game for a few minutes (the opening, the first
area), then close it. The phone has now recorded the shaders the game made. Bring them over and build them in:

```
python tools\phone_pull.py "<build folder>\phone\first-run"
python nxs2.py refresh "<build folder>"
```

(`refresh` finds what is under `<build folder>\phone\` by itself.)

Sign the new IPA again and install it over the old one (your save stays). From then on the title screen and everything
you played through are drawn. Anything new the game makes later is recorded the same way; refresh again whenever you like.
