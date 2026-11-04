# Third-party notices for `host/`

`nxnative-switch2.patch` is a set of changes to [nxnative](https://github.com/seedds/nxnative) and so contains
modified nxnative source. `dmsh_xlate_bin/` is a build of nxnative's shader translator wrapper and of three
[Ryujinx](https://github.com/Ryujinx/Ryujinx) libraries (`Ryujinx.Graphics.Shader`, `Ryujinx.Common`,
`Spv.Generator`, version 1.3.2 as carried in nxnative's `dm/third_party/ryujinx`, with the two source changes in the
patch). Both projects are MIT-licensed; their notices follow and apply to those files. nxnative itself started from
[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) (MIT) and lists its other third-party components, with their
licences, in its `dm/third_party/README.md`.

## Eden-NS2 (GPL-3.0-or-later)

The Switch 2 texture layout in `nxnative-switch2.patch` (the in-GOB bit order in `dm/gfx/tex/nvn_deswizzle.c` and
`dm/gfx/msl/deswz.metal`) follows code from the Eden-NS2 emulator, which is GPL-3.0-or-later (the full text is in
`ampere-lower/LICENSE`). While that part is in the patch, the patch as distributed is GPL-3.0-or-later. It is being
replaced with a clean MIT implementation.

## nxnative

```
MIT License

Copyright (c) 2026 seedds

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Ryujinx

```
MIT License

Copyright (c) Ryujinx Team and Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## nxnative's Windows port (`windows/`)

On Windows, `nxs2.py setup` also fetches the `win/` folder of [NXNativeWindows](https://github.com/zenvyzenshiro/NXNativeWindows)
(MIT, the same licence text as nxnative's, whose fork it is) at a pinned commit: the tool shim, the LLVM build of the iPhone
app, the SDK extractor. `windows/fork-extras.patch` carries three of that port's fixes to nxnative files (RomFS image reads,
the process-pool size, IPA packing on Windows), `windows/build_app.patch` this project's changes to its app builder, and
`windows/dmsh_xlate_bin/` the translator above built for Windows (AnyCPU) from the same patched sources.

## The changes themselves

The Switch 2 changes in the patch and the `dmsh_xlate` wrapper build are released under this repository's MIT licence
(`../LICENSE`). Nothing in `host/` comes from the game, from the console's firmware, or from Nintendo's SDK: the NVN2
entry points and structures were worked out by observing what the game calls, and `names.txt` lists API function names
only.
