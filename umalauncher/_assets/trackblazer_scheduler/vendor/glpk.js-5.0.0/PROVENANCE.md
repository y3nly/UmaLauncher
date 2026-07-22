# glpk.js 5.0.0

- Package: `glpk.js` version `5.0.0`
- Upstream repository: https://github.com/jvail/glpk.js
- Source archive: https://registry.npmjs.org/glpk.js/-/glpk.js-5.0.0.tgz
- Source archive SHA-256: `8B0D01E5CB4B54520A7D5DF717440508454B78D4FE9A7F1CE5BB932A48F4E909`
- Retrieved: 2026-07-15
- License: GPL-3.0; the upstream license text is included as `LICENSE`.

The files below were copied without modification from the npm source archive:

| File | Upstream path | SHA-256 |
| --- | --- | --- |
| `index.js` | `package/dist/index.js` | `04FF37A216B2A8FA9375FEE1C56B4522405B6D23A1AF5FCFE230A59CA419A2A9` |
| `glpk.wasm` | `package/dist/glpk.wasm` | `ACF09A204BBED381FD00FE57A3D4F8337986F28245DABA5E977831BA1FC090CA` |
| `LICENSE` | `package/LICENSE` | `FE3EEA6C599E23A00C08C5F5CB2320C30ADC8F8687DB5FCEC9B79A662C53FF6B` |

The browser scheduler imports `index.js` locally. That browser bundle embeds its worker and compressed
WebAssembly runtime; `glpk.wasm` is retained alongside it as the corresponding upstream distribution
artifact and for package provenance. No scheduler runtime file is loaded from a CDN.
