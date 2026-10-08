# Photoshop Depth Mask Generator

A Photoshop **UXP** plugin that generates **16-bit relative depth maps and fog masks** from a
document, using five monocular depth-estimation models running locally.

The plugin is split in two parts:

* a **UXP panel** (plain JavaScript, no build step) that exports the active document, talks to a
  local HTTP service and turns the returned depth field into Photoshop channels;
* a **local inference service** (`depth_server`, Python + Flask) bound to
  `127.0.0.1:8766`, which runs each model in a throw-away subprocess so memory/VRAM is reclaimed
  after every request.

> **Source-only repository.** Model weights, the frozen Python runtime and the built
> `depth_server.exe` are **not** included. See [docs/BUILD.md](docs/BUILD.md) for how to obtain
> the weights and build the service yourself.

## Models

| key | model | resolution strategy | direction | origin |
|---|---|---|---|---|
| `bridge` | BRIDGE MDE | short side 518 (fp32) | inverted | Shanghai AI Lab / Fudan |
| `depthpro` | Depth Pro | fixed 1536×1536 | as-is | Apple |
| `distillanydepth` | DistillAnyDepth | short side 518 | inverted | Westlake AGI Lab / SJTU |
| `iris` | Iris | long side ≤1536 (fp16) | inverted | NUST MIL |
| `ppd` | Pixel-Perfect Depth | 2048×1536 area | as-is | MoGe2 semantic variant |

"Direction" is normalised to *far = bright*. `depthpro` is the default model. The panel's model
dropdown is built from the weight folders actually found next to the service, so deleting a weight
folder disables that model in the UI — the plugin is modular and can be slimmed down.

Two runtime variants share the same front-end and the same HTTP protocol:

* **Torch variant** (`com.zk21.depthpro`) — PyTorch / transformers / diffusers, CUDA or CPU.
* **Windows / ONNX variant** (`com.zk21.depthpro.windows`) — ONNX Runtime, DirectML (any
  DirectX 12 GPU) with a CPU fallback. Reads only `*.onnx` + external `*.onnx.data`, never
  `.pth` / `.safetensors`.

## Repository layout

```
uxp/                 UXP panel: manifest, index.html, main.js, common.js, PNG codec, tests
  manifest.json        variant id com.zk21.depthpro       (Torch)
  manifest.onnx.json   variant id com.zk21.depthpro.windows (ONNX)
server-torch/        Python service of the Torch variant
server-onnx/         Python service of the Windows / ONNX variant
third_party/         Upstream model source trees (see third_party/UPSTREAM.md)
docs/                Build notes and the original Chinese manual
THIRD_PARTY_NOTICE.txt, THIRD_PARTY_LICENSES/
```

## Pipeline

1. The panel renders the active document to a 16-bit PNG (long edge capped at
   `MAX_TRANSMIT_DIM = 4608`) and `POST`s it to `/process`.
2. The service picks the requested model, runs it in a one-shot subprocess, and applies the
   post-processing chain: outlier clipping → normalisation + histogram equalisation
   (`S1 / S5 / N1_KMeans / N1A / N2`, or `none` for an A/B control) → super-resolution
   (`bilinear / WGIF / JBU-NC / none`) → final normalisation → fog transform.
3. The depth field comes back as a 16-bit (or 8-bit) PNG. The panel imports it as a channel and
   can additionally derive three levels of `depth` / `antidepth` / `middepth`
   (`DERIVED_MAX_LEVEL = 3`), invert, and mix two channels
   (`MIX_MAX_DIM = 4096`).

### HTTP contract

| endpoint | method | response |
|---|---|---|
| `/ping` | GET | `{"service": "depth_server", "version": "7", ...}` plus the detected model list |
| `/process` | POST | JSON with the depth PNG (base64) and diagnostics |

The panel requires `version == "7"` (`SERVER_EXPECTED_VERSION` in `uxp/common.js:19` mirrors
`SERVER_VERSION = "7"` in `server-torch/depth_server.py:33` and
`server-onnx/depth_server.py:33`). **A version mismatch makes the panel refuse to talk to the
service**, so change both sides together.

## Requirements

* Photoshop **23.0** or newer (PS 2023+). The panel is registered under the **Plugins
  (增效工具)** menu — *not* under *Window → Extensions*, which only lists legacy CEP extensions.
* The Torch variant wants a CUDA GPU for reasonable speed; the ONNX variant targets machines
  without an NVIDIA GPU and falls back to CPU.
* A UXP plugin panel is only evaluated when Photoshop creates it: after changing any JS/CSS/
  manifest file you must **restart Photoshop**, not just reload the panel.

## Building and installing

See [docs/BUILD.md](docs/BUILD.md).

## Known issues

* The panel's launcher keeps its original Chinese filename `启动深度服务.jsx`; `uxp/common.js`
  resolves it by name, so it must not be renamed.
* `docs/README.zh-CN.txt` is the original Chinese manual. It predates this repository and still
  mentions fixture folders (`测试样图\`, `测试输出\`) that are not part of the source tree.
* The shipped **0.2.1 ONNX package on disk** was built before the bit-depth fix landed in the
  Torch variant: its `common.js` / `main.js` treated `Document.bitsPerChannel` as a number
  (it is the string `'bitDepth16'`/`'bitDepth8'` in UXP), so 16-bit documents were uploaded as
  8-bit, and large 16-bit documents could upload a *blank* image through the
  `copyMerged` → clipboard route. **This repository ships the fixed front-end**; the prebuilt
  `.exe` packages were not rebuilt.
* Source-only: no weights are bundled, so a fresh clone starts with no model available.
* Runtime logs (`*.log`), pre-patch ONNX fixtures and `*.bak-*` snapshots from the development
  tree are intentionally not published; use Git history instead.

## Third-party components

Depth Mask Generator redistributes source code from several upstream research projects. See
[THIRD_PARTY_NOTICE.txt](THIRD_PARTY_NOTICE.txt),
[THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES) and
[third_party/UPSTREAM.md](third_party/UPSTREAM.md) for the exact commits and the local
modifications applied to them.

**Note that model *weights* carry their own terms** — in particular the Depth Pro weights are
released under the Apple ML Research License, which is **non-commercial**. The notices shipped
with this project describe the full plugin distribution (weights + runtime); this repository
contains source only.

## License

The original code in this repository is MIT-licensed — see [LICENSE](LICENSE).
Third-party code keeps its own license (Apache-2.0 / MIT), preserved in
`THIRD_PARTY_LICENSES/` and in the upstream trees under `third_party/`.
