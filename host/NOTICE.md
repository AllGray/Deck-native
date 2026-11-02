# Third-party notices for `host/`

`nxnative-switch2.patch` is a set of changes to [nxnative](https://github.com/seedds/nxnative) and so contains
modified nxnative source. `dmsh_xlate_bin/` is a build of nxnative's shader translator wrapper and of three
[Ryujinx](https://github.com/Ryujinx/Ryujinx) libraries (`Ryujinx.Graphics.Shader`, `Ryujinx.Common`,
`Spv.Generator`, version 1.3.2 as carried in nxnative's `dm/third_party/ryujinx`, with the two source changes in the
patch). Both projects are MIT-licensed; their notices follow and apply to those files. nxnative itself started from
[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) (MIT) and lists its other third-party components, with their
licences, in its `dm/third_party/README.md`.

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

## The changes themselves

The Switch 2 changes in the patch and the `dmsh_xlate` wrapper build are released under this repository's MIT licence
(`../LICENSE`). Nothing in `host/` comes from the game, from the console's firmware, or from Nintendo's SDK: the NVN2
entry points and structures were worked out by observing what the game calls, and `names.txt` lists API function names
only.
