# Photoshop Depth Mask Generator
**English** | [中文](README.zh-CN.md)

A Photoshop **UXP** plugin that generates **16-bit relative depth maps and fog masks** from a document, using five monocular depth-estimation models running locally.

The plugin is split in two parts:

* a **UXP panel** (plain JavaScript, no build step) that exports the active document, talks to a local HTTP service and turns the returned depth field into Photoshop channels;
* a **local inference service** (`depth_server`, Python + Flask) bound to `127.0.0.1:8766`, which runs each model in a throw-away subprocess so memory/VRAM is reclaimed after every request.

> **Source-only repository.** Model weights, the frozen Python runtime and the built `depth_server.exe` are **not** included. See [docs/BUILD.md](docs/BUILD.md) for how to obtain the weights and build the service yourself.

## Models

| key | model | resolution strategy | direction | origin |
|---|---|---|---|---|
| `bridge` | BRIDGE MDE | short side 518 (fp32) | inverted | Shanghai AI Lab / Fudan |
| `depthpro` | Depth Pro | fixed 1536×1536 | as-is | Apple |
| `distillanydepth` | DistillAnyDepth | short side 518 | inverted | Westlake AGI Lab / SJTU |
| `iris` | Iris | long side ≤1536 (fp16) | inverted | NUST MIL |
| `ppd` | Pixel-Perfect Depth | 2048×1536 area | as-is | MoGe2 semantic variant |

"Direction" is normalised to *far = bright*. `depthpro` is the default model. The panel's model dropdown is built from the weight folders actually found next to the service, so deleting a weight folder disables that model in the UI — the plugin is modular and can be slimmed down.

Two runtime variants share the same front-end and the same HTTP protocol:

* **Torch variant** (`com.zk21.depthpro`) — PyTorch / transformers / diffusers, CUDA or CPU.
* **Windows / ONNX variant** (`com.zk21.depthpro.windows`) — ONNX Runtime, DirectML (any DirectX 12 GPU) with a CPU fallback. Reads only `*.onnx` + external `*.onnx.data`, never `.pth` / `.safetensors`.

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

1. The panel renders the active document to a 16-bit PNG (long edge capped at `MAX_TRANSMIT_DIM = 4608`) and `POST`s it to `/process`.
2. The service picks the requested model, runs it in a one-shot subprocess, and applies the post-processing chain: outlier clipping → normalisation + histogram equalisation (`S1 / S5 / N1_KMeans / N1A / N2`, or `none` for an A/B control) → super-resolution (`bilinear / WGIF / JBU-NC / none`) → final normalisation → fog transform.
3. The depth field comes back as a 16-bit (or 8-bit) PNG. The panel imports it as a channel and can additionally derive three levels of `depth` / `antidepth` / `middepth` (`DERIVED_MAX_LEVEL = 3`), invert, and mix two channels (`MIX_MAX_DIM = 4096`).

### HTTP contract

| endpoint | method | response |
|---|---|---|
| `/ping` | GET | `{"service": "depth_server", "version": "7", ...}` plus the detected model list |
| `/process` | POST | JSON with the depth PNG (base64) and diagnostics |

The panel requires `version == "7"` (`SERVER_EXPECTED_VERSION` in `uxp/common.js:19` mirrors `SERVER_VERSION = "7"` in `server-torch/depth_server.py:33` and `server-onnx/depth_server.py:33`). **A version mismatch makes the panel refuse to talk to the service**, so change both sides together.

## Requirements

* Photoshop **23.0** or newer (PS 2023+ — `minVersion` in `uxp/manifest.json:12`). The panel is registered under the **Plugins (增效工具)** menu — *not* under *Window → Extensions*, which only lists legacy CEP extensions.
* Python 3.10+ and PyInstaller, if you build the service yourself: the Torch variant additionally needs PyTorch + transformers + diffusers, the ONNX variant `onnxruntime-directml` (DirectML + CPU execution providers).
* The Torch variant wants a CUDA GPU for reasonable speed; the ONNX variant targets machines without an NVIDIA GPU and falls back to CPU.
* A UXP plugin panel is only evaluated when Photoshop creates it: after changing any JS/CSS/manifest file you must **restart Photoshop**, not just reload the panel.

## Building and installing

Everything below is what a fresh clone has to supply itself; [docs/BUILD.md](docs/BUILD.md) is the same material in condensed form.

### Overview and layout

Both variants ship the same front-end and speak the same HTTP protocol; only the Python service and the manifest id/label differ. A working installation is one plugin folder that holds the front-end files **and** the service next to them:

```
com.zk21.depthpro/            # Torch variant (uxp/manifest.json)
com.zk21.depthpro.windows/    # ONNX variant  (uxp/manifest.onnx.json)
  index.html main.js common.js mix_core.js mix_extract.jsx png_codec.js vendor/ icon.png
  启动深度服务.jsx             # launcher: finds, probes and starts depth_server.exe
  manifest.json               # manifest.onnx.json for the ONNX variant
  depth_server.exe _internal/ # your PyInstaller onedir build
  BRIDGE/ DepthPro/ DistillAnyDepth/ Iris/ PPD/                 # Torch weights
  BRIDGE-onnx/ DepthPro-onnx/ DistillAnyDepth-onnx/ Iris-onnx/ PPD-onnx/   # ONNX weights
```

`uxp/启动深度服务.jsx` looks for `depth_server.exe` in the plugin folder, in its parent and in one level of sub-folders, so an unpacked `dist/depth_server/` tree can also live in a sub-folder.

### Prerequisites

* Photoshop 23.0+ (PS 2023+), 64-bit.
* Python 3.12 for the service — both reference builds used 3.12 — plus `pyinstaller` if you package it (the build drivers call `python -m PyInstaller`).
* Adobe **UXP Developer Tool** — optional, but the easiest way to load and debug the panel.
* Runtime dependencies: install from the file that matches your variant — [`server-torch/requirements.txt`](server-torch/requirements.txt) or [`server-onnx/requirements.txt`](server-onnx/requirements.txt). They list what the two services actually import, together with the versions recorded for the reference build environments.
* ⚠️ Do **not** `pip install third_party/*/requirements.txt` verbatim. Those are the upstream *research* environments and they pin an incompatible stack: Iris asks for `torch==2.3.1+cu121`, `transformers==4.40.1` and `numpy==1.26.4`, whereas the shipped service runs `torch 2.9.1+cu128`, `transformers 5.13.1` and `diffusers 0.28.0` (Python 3.12); the ONNX reference environment is Python 3.12.4 + `onnxruntime-directml 1.24.4` + PyInstaller 6.22. Take the upstream **source trees**, not their dependency pins.
* Torch variant: a CUDA GPU is wanted for reasonable speed; ONNX variant: DirectML (any DirectX 12 GPU) with a CPU fallback, for machines without an NVIDIA GPU.
* A UXP plugin panel is only evaluated when Photoshop creates it: after changing any JS/CSS/manifest file you must **restart Photoshop**, not just reload the panel.

### 1. Preparing the weights

Weights are **not** shipped: a fresh clone starts with no model available. The service discovers models by probing the folders next to the plugin directory (`MODEL_SPECS` in `server-torch/models.py:14` and `server-onnx/models.py:14`); each model lives in its own folder, and the folder must contain **every** marker file listed below, otherwise the model simply does not show up in the panel.

#### Torch variant

| folder | required marker files | notes |
|---|---|---|
| `BRIDGE/` | `权重/bridge.pth` | inference package + `源码/` on `sys.path`; must run in **fp32** (fp16 overflows to NaN on ViT-g) |
| `DepthPro/` | `model.safetensors`, `config.json` | transformers Hub layout — no local source copy |
| `DistillAnyDepth/` | `权重/model.safetensors`, `权重/config.json` | transformers Hub layout |
| `Iris/` | `权重/unet/diffusion_pytorch_model.safetensors`, `权重/text_encoder/model.safetensors` | diffusers layout (`unet` / `text_encoder` / `vae` / `tokenizer` / `scheduler`) |
| `PPD/` | `ppd_moge.pth`, `moge2.pt` | plus the vendored `源码/` |

* The folder names are fixed (`BRIDGE`, `DepthPro`, `DistillAnyDepth`, `Iris`, `PPD`), and `BRIDGE` / `Iris` / `PPD` also need their upstream `源码/` tree, which the adapters put on `sys.path`.
* Miss one marker — a single `config.json` — and that model is reported as not found and disabled in the UI.

#### Windows / ONNX variant

The ONNX exports live in `*-onnx/` folders with **external data** files, which is why both the `.onnx` graph and its `.onnx.data` sibling are required:

| folder | required marker files |
|---|---|
| `BRIDGE-onnx/` | `bridge_fp32_784x518.onnx`, `bridge_fp32_518x784.onnx`, `bridge_fp32.onnx_data` |
| `DepthPro-onnx/` | `depthpro_fp32.onnx`, `depthpro_fp32.onnx.data` |
| `DistillAnyDepth-onnx/` | `distillanydepth_fp32_518x518.onnx`, `..._518x784.onnx`, `..._784x518.onnx`, `distillanydepth_fp32.onnx_data` |
| `Iris-onnx/` | `iris_unet.onnx`, `iris_text_encoder.onnx`, `iris_vae_encoder.onnx`, `iris_vae_decoder.onnx` |
| `PPD-onnx/` | `ppd_moge_sem.onnx`, `ppd_dit_step.onnx` |

* The ONNX variant reads **only** `*.onnx` and their external data files; `.pth` / `.safetensors` folders are ignored.
* Deleting a whole `*-onnx/` folder is the supported way to slim the plugin down.

You can also point the service at an out-of-tree model directory with the `DEPTH_MODEL_ROOT` environment variable (`server-torch/models.py:64`, `server-onnx/models.py:72`): it is prepended to the candidate chain (env → service directory → nested plugin folder → parent plugin folder).

Where to get them:

* **BRIDGE MDE** — https://github.com/lnbxldn/BRIDGE (Apache-2.0).
* **Depth Pro** — `apple/DepthPro-hf` on Hugging Face. ⚠️ The weights are released under the **Apple ML Research License (non-commercial)**. The ONNX graphs used by the Windows variant are exported from `onnx-community/DepthPro-ONNX` (fp32, opset 14; the graph already bakes in the FOV conversion and the `1/clamp`, so its `predicted_depth` output *is* metric depth).
* **DistillAnyDepth** — Westlake AGI Lab / SJTU (MIT).
* **Iris** — https://github.com/NUST-Machine-Intelligence-Laboratory/Iris (Apache-2.0).
* **Pixel-Perfect Depth** — https://github.com/gangweix/pixel-perfect-depth (Apache-2.0).

Full attribution and the list of local vendor patches are in [`THIRD_PARTY_NOTICE.txt`](THIRD_PARTY_NOTICE.txt).

#### Where the vendored upstream sources go (Torch variant)

The Torch adapters put `<weight folder>/源码` on `sys.path` (`add_source_dirs()` in `server-torch/models.py:114`) and then import the upstream inference packages (`from bridge.dpt import Bridge`, `from pipeline import IrisPipeline`, `import ppd`), so the trees under `third_party/` are **not** optional — copy them into place:

| this repository | copy to | provides |
|---|---|---|
| `third_party/BRIDGE/*` | `<model root>/BRIDGE/源码/` | the `bridge/` package (`bridge/dpt.py`) |
| `third_party/Iris/*` | `<model root>/Iris/源码/` | `pipeline.py` (+ `utils/`) |
| `third_party/PPD/*` | `<model root>/PPD/源码/` | the `ppd/` package |

`DepthPro/` and `DistillAnyDepth/` need **no** local source copy (their adapters load straight through `transformers`), and the Windows / ONNX variant needs **none** of `third_party/` at all — its adapters use ONNX Runtime plus `onnx_common.py` from `server-onnx/`.

### 2. Installing the panel

1. **Stage the plugin folder** — create `com.zk21.depthpro/` (Torch) or `com.zk21.depthpro.windows/` (ONNX) and copy the contents of `uxp/` into it.
2. **Pick the manifest** — keep `manifest.json` for the Torch variant; for the ONNX variant copy `manifest.onnx.json` over `manifest.json`. The two differ only in `id` and in the panel `label` (`uxp/manifest.json:3`, `uxp/manifest.onnx.json:3`).
3. **Load it** — either point the *UXP Developer Tool* at `manifest.json` and press *Load*, or copy the whole plugin folder into the directory Photoshop scans (`%APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<version>\Internal\`, or the Photoshop `Plug-ins\` folder).
4. **Open the panel** — Photoshop → **增效工具 / Plugins** → *Depth Mask*; it is **not** under *Window → Extensions*.
5. **Restart Photoshop after every JS/CSS/manifest change** — a UXP panel is evaluated only when Photoshop creates it, so reloading the panel is not enough.

### 3. Building the service

`depth_server.exe` is **not** in this repository; build the PyInstaller **onedir** bundle yourself. Install the runtime dependencies first, from [`server-torch/requirements.txt`](server-torch/requirements.txt) or [`server-onnx/requirements.txt`](server-onnx/requirements.txt). The `.spec` files carry the hidden imports and the collected binaries, so prefer them over a hand-written PyInstaller command:

```
server-torch/depth_server_v5.spec
server-onnx/depth_server_windows.spec
```

#### Torch variant

```bash
cd server-torch
python -m PyInstaller --noconfirm --clean depth_server_v5.spec
# -> dist/depth_server/depth_server.exe + dist/depth_server/_internal/
```

`build_depth_server_v5.py` is the developer's staging driver around the same spec: it runs `python -m PyInstaller --noconfirm --clean --distpath dist_v7_candidate --workpath build_v7_candidate depth_server_v5.spec`, stages the weight folders (only `权重/` for DistillAnyDepth; `权重/` + `源码/` for BRIDGE and Iris; the whole folder for DepthPro and PPD), checks every marker file, patches the Iris source (drops the top-level `tensorboard` / `matplotlib` imports) and writes `build_manifest_v7.json` with SHA-256 hashes. Switches: `--python`, `--skip-pyinstaller`, `--source-dist-dir`, `--clean`, `--no-verify-exe`; there is **no** `--dry-run`. It is written for the upstream build workspace (`../papers/深度实验/模型` for the weights, `../com.zk21.depthpro/` and `../README.txt` for the front-end), so from a plain clone either recreate those paths or use the raw PyInstaller command above and copy `uxp/*` next to `depth_server.exe` yourself.

#### Windows / ONNX variant

```bash
cd server-onnx
python build_windows_onnx.py --dry-run   # print the copy plan and sizes, build nothing
python build_windows_onnx.py             # PyInstaller + staging + /ping protocol check
```

The ONNX driver wraps `depth_server_windows.spec` the same way (`--noconfirm --clean --distpath dist_win_candidate --workpath build_win_candidate`), stages the `*-onnx` folders and the front-end files, and then **launches the new executable and asserts that `/ping` reports `version == "7"`** before writing `build_manifest_windows.json` with the artifact hashes. Additional switches: `--python`, `--skip-pyinstaller`, `--skip-copy-to-plugin`, `--clean`, `--no-verify-exe`. Its weight source is `../papers/深度实验/模型` with a fallback to `com.zk21.depthpro.windows/` (`_resolve_models_src`), and it also expects a `com.zk21.depthpro/` front-end folder.

`gate_onnx_models.py` is a separate numerical gate: it compares every model stage against reference `.npz` tensors produced on the CPU execution provider (cosine ≥ 0.99 per stage and on the final output) and writes `gate_results.json`. Set `DEPTH_MODELS_SRC` to point it at a different model directory.

### 4. Running and self-check

Pressing **生成** in the panel calls `启动深度服务.jsx`, which locates `depth_server.exe`, probes `/ping` and starts it in the background — killing a stale service with the wrong protocol version first — then long-polls for up to 90 s. You can also start `depth_server.exe` by hand for testing.

```bash
curl http://127.0.0.1:8766/ping
```

must report `"version": "7"`, matching `SERVER_EXPECTED_VERSION` in `uxp/common.js:19`; **a mismatch makes the panel refuse to talk to the service**.

* `server-torch/run_ps_channel.ps1`, `run_ps_diag.ps1`, `run_ps_e2e.ps1` and `run_ps_extract.ps1` evaluate a `.jsx` inside a running Photoshop through COM: `pwsh -File run_ps_e2e.ps1 -Path 'C:\path\to\your.jsx'`. Their default `-Path` points at scratch files that are not in this repository, so always pass your own.
* `server-onnx/smoke_client.py` posts a fixed 8-bit or 16-bit PNG and checks the output: `python smoke_client.py --server http://127.0.0.1:8766 --model depthpro --algo none --bits 8 [--image path/to/img.png]`, writing `smoke_out/<model>_<algo>_<bits>.png` and printing shape / dtype / bit depth / min / max / mean / std / unique-value / clipping stats. `server-onnx/test_client.py` is the wider client test.
* Depth output must be verified as **`uint16` / 16-bit PNG for real** — never trust the file name or the UI label.

### 5. Front-end self-check

The panel's pure-JS logic runs without Photoshop, from the repository root:

```bash
node uxp/tests/test_bitdepth.js        # 28 assertions
node uxp/tests/test_frontend_core.js   # 25 assertions on a fresh clone
```

`test_bitdepth.js` guards the 16-bit path (`Document.bitsPerChannel` is the string `'bitDepth16'` / `'bitDepth8'`, `docPngBits` / `setDocBits`, static guards against the legacy `Number(doc.bitsPerChannel)` and `ChangeMode.SIXTEEN` spellings, and the PNG IHDR bit-depth byte). `test_frontend_core.js` covers `png_codec` and `mix_core`; when it cannot find `com.zk21.spectrum/spectrum_core.js` it prints a notice and skips its sections 3–6, which is why a fresh clone reports **25** assertions instead of the full suite. Both scripts accept `DEPTHMASK_PLUGIN=<plugin folder>` to test a different plugin directory, and `SPECTRUM_PLUGIN=<folder>` to point `test_frontend_core.js` at a sibling Spectrum plugin.

### 6. Using the panel

Open a document, choose a model and press **生成**: the panel exports the (resized) image, `POST`s it to `/process`, the service runs the model and the post-processing chain, and the panel imports the returned 16-bit (or 8-bit) PNG as a channel named `depth` or `fog`.

* **Model** — `bridge | depthpro | distillanydepth | iris | ppd`, default `depthpro`; the dropdown only lists models whose weights the service actually found.
* **Output** — `Depth` (channel `depth`) or `Fog` (channel `fog`, also loaded as a selection); `both` was removed in 0.2.1.
* **Post-processing** — histogram equalisation `S1` (default, global CDF) / `S5` / `N1_KMeans` / `N1A` / `N2`, or `none` for an A/B control, after outlier clipping and normalisation.
* **Super-resolution** — `bilinear` (default) / `WGIF` / `JBU-NC` / `none` (leave it to Photoshop), followed by a final normalisation and the fog transform; the WGIF radius slider (1–32, default 2) is scaled by the upscale factor k.
* **Derived channels** — one button each for `Depth` / `AntiDepth` / `MidDepth`, 1–3 levels (`DERIVED_MAX_LEVEL = 3`; channels `depth1-3`, `antidepth1-3`, `middepth1-3`), plus an all-generate / all-delete toggle.
* **AlphaMixer** — pick channel A and channel B (`Lum Lock` / `depth` / `L` / `R` / `G` / `B` / custom), one of 8 operations (weighted average / multiply / RMS / screen / difference / max / min / overlay), a ratio slider (0–100 %, default 50 %) and press `mix`; all of it runs in the front-end JS and writes a `mixerN` channel. Channel extraction is capped at `MIX_MAX_DIM = 4096` and Photoshop scales the result back to the document size.
* **Direction** — everything is normalised on the wire to **far = bright** (`invert_direction` is baked in per model in `models.py`), so a lower PNG value means "nearer".

Constants to keep in mind: `MAX_TRANSMIT_DIM = 4608` (`uxp/common.js:21`), `DERIVED_MAX_LEVEL = 3` (`uxp/main.js:13`), `MIX_MAX_DIM = 4096` (`uxp/main.js:14`), port `8766`, protocol version `"7"`.

## Known issues

* The panel's launcher keeps its original Chinese filename `启动深度服务.jsx`; `uxp/common.js` resolves it by name, so it must not be renamed.
* `docs/README.zh-CN.txt` is the original Chinese manual. It predates this repository and still mentions fixture folders (`测试样图\`, `测试输出\`) that are not part of the source tree.
* The shipped **0.2.1 ONNX package on disk** was built before the bit-depth fix landed in the Torch variant: its `common.js` / `main.js` treated `Document.bitsPerChannel` as a number (it is the string `'bitDepth16'`/`'bitDepth8'` in UXP), so 16-bit documents were uploaded as 8-bit, and large 16-bit documents could upload a *blank* image through the `copyMerged` → clipboard route. **This repository ships the fixed front-end**; the prebuilt `.exe` packages were not rebuilt.
* Source-only: no weights are bundled, so a fresh clone starts with no model available.
* Runtime logs (`*.log`), pre-patch ONNX fixtures and `*.bak-*` snapshots from the development tree are intentionally not published; use Git history instead.

## Third-party components

Depth Mask Generator redistributes source code from several upstream research projects. See [THIRD_PARTY_NOTICE.txt](THIRD_PARTY_NOTICE.txt), [THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES) and [third_party/UPSTREAM.md](third_party/UPSTREAM.md) for the exact commits and the local modifications applied to them.

**Note that model *weights* carry their own terms** — in particular the Depth Pro weights are released under the Apple ML Research License, which is **non-commercial**. The notices shipped with this project describe the full plugin distribution (weights + runtime); this repository contains source only.

## License

The original code in this repository is MIT-licensed — see [LICENSE](LICENSE). Third-party code keeps its own license (Apache-2.0 / MIT), preserved in `THIRD_PARTY_LICENSES/` and in the upstream trees under `third_party/`.
