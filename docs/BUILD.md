# Building, installing and running from source

This repository contains **source only**: no model weights, no frozen Python runtime and no
prebuilt `depth_server.exe`. This document covers what you have to supply yourself.

## 1. Panel only (no service)

`uxp/` is plain JavaScript with no build step. Point the Adobe **UXP Developer Tool** at
`uxp/manifest.json` and press *Load*, or copy `uxp/` into a plugin folder that Photoshop scans.
The panel appears under the **Plugins (增效工具)** menu, not under *Window → Extensions*.

The panel is useless without a running service, but at least the UI can be inspected this way.

## 2. Weights

The service discovers models by probing the folder next to the plugin directory (see
`MODEL_SPECS` in `server-torch/models.py:14` and `server-onnx/models.py:14`). Weights are **not**
at the plugin root — each model lives in its own folder, and the folder must contain the marker
files listed below or the model simply does not show up in the panel.

### Torch variant

| folder | required marker files | notes |
|---|---|---|
| `BRIDGE/` | `权重/bridge.pth` | inference package + `源码/` on `sys.path`; must run in **fp32** (fp16 overflows to NaN on ViT-g) |
| `DepthPro/` | `model.safetensors`, `config.json` | transformers Hub layout — no local source copy |
| `DistillAnyDepth/` | `权重/model.safetensors`, `权重/config.json` | transformers Hub layout |
| `Iris/` | `权重/unet/diffusion_pytorch_model.safetensors`, `权重/text_encoder/model.safetensors` | diffusers layout (`unet` / `text_encoder` / `vae` / `tokenizer` / `scheduler`) |
| `PPD/` | `ppd_moge.pth`, `moge2.pt` | plus the vendored `源码/` |

### Windows / ONNX variant

The ONNX exports live in `*-onnx/` folders with **external data** files, which is why both the
`.onnx` graph and its `.onnx.data` sibling are required:

| folder | required marker files |
|---|---|
| `BRIDGE-onnx/` | `bridge_fp32_784x518.onnx`, `bridge_fp32_518x784.onnx`, `bridge_fp32.onnx_data` |
| `DepthPro-onnx/` | `depthpro_fp32.onnx`, `depthpro_fp32.onnx.data` |
| `DistillAnyDepth-onnx/` | `distillanydepth_fp32_518x518.onnx`, `..._518x784.onnx`, `..._784x518.onnx`, `distillanydepth_fp32.onnx_data` |
| `Iris-onnx/` | `iris_unet.onnx`, `iris_text_encoder.onnx`, `iris_vae_encoder.onnx`, `iris_vae_decoder.onnx` |
| `PPD-onnx/` | `ppd_moge_sem.onnx`, `ppd_dit_step.onnx` |

You can also point the service at an out-of-tree model directory with the `DEPTH_MODEL_ROOT`
environment variable (`server-onnx/models.py:72`).

Where to get them:

* **BRIDGE MDE** — https://github.com/lnbxldn/BRIDGE (Apache-2.0).
* **Depth Pro** — `apple/DepthPro-hf` on Hugging Face. ⚠️ The weights are released under the
  **Apple ML Research License (non-commercial)**. The ONNX graphs used by the Windows variant are
  exported from `onnx-community/DepthPro-ONNX` (fp32, opset 14; the graph already bakes in the
  FOV conversion and the `1/clamp`, so its `predicted_depth` output *is* metric depth).
* **DistillAnyDepth** — Westlake AGI Lab / SJTU (MIT).
* **Iris** — https://github.com/NUST-Machine-Intelligence-Laboratory/Iris (Apache-2.0).
* **Pixel-Perfect Depth** — https://github.com/gangweix/pixel-perfect-depth (Apache-2.0).

Full attribution and the list of local vendor patches are in
[`../THIRD_PARTY_NOTICE.txt`](../THIRD_PARTY_NOTICE.txt).

## 3. Building the service

### Torch variant

```bash
cd server-torch
pyinstaller depth_server_v5.spec          # onedir build
```

`build_depth_server_v5.py` wraps the same spec with explicit `hiddenimports`
(`models`, `model_bridge`, `model_*.py`, `postprocess`, `img_io`, `histogram_eq`) and
`collect_all` for the heavy environment packages — prefer it over a raw `pyinstaller` call.

### Windows / ONNX variant

```bash
cd server-onnx
python build_windows_onnx.py --dry-run    # print the plan first
python build_windows_onnx.py              # onedir build + /ping protocol check
```

The build is a PyInstaller **onedir** build, and it validates the result by launching the new
executable and asserting `/ping` reports `version == "7"`. Passing `--dry-run` prints the plan
without building. The build writes `build_manifest_windows.json` with artifact hashes.

`gate_onnx_models.py` is a separate numerical gate: it compares every model stage against
reference `.npz` tensors produced on the CPU execution provider (cosine ≥ 0.99 per stage and on
the final output) and writes `gate_results.json`. Set `DEPTH_MODELS_SRC` to point it at a
different model directory.

### Packaging the panel

The two variants share the same front-end files and differ only in `manifest.json`:

```
com.zk21.depthpro/          ->  copy uxp/* and keep  uxp/manifest.json
com.zk21.depthpro.windows/  ->  copy uxp/* and use   uxp/manifest.onnx.json as manifest.json
```

The bundled launcher `uxp/启动深度服务.jsx` locates `depth_server.exe`, starts it in the
background and restarts it when a stale service with the wrong protocol version answers
`/ping`. Keep the file name — `uxp/common.js:130` resolves it by name.

## 4. Verifying

* Protocol: `curl http://127.0.0.1:8766/ping` must report `"version": "7"`.
* End to end: `server-onnx/smoke_client.py` posts a fixed image and writes PNGs into
  `smoke_out/`; `server-onnx/test_client.py` is the wider client test.
* Front-end logic without Photoshop: `node uxp/tests/test_frontend_core.js` and
  `node uxp/tests/test_bitdepth.js` (28 + 87 assertions).
* Depth output must be checked as **`uint16` / 16-bit PNG for real** — do not trust the file name
  or the UI label. Record shape, dtype, bit depth, min/max/mean/std, unique value count,
  clipping ratio and seam metrics when comparing variants, and always keep an
  `algorithm: "none"` control run.

## 5. Notes for Contributors

* A UXP panel is evaluated when Photoshop creates it, so **restart Photoshop** after touching any
  JS/CSS/manifest file. Reloading the panel is not enough.
* `Document.bitsPerChannel` is the **string** `'bitDepth16'` / `'bitDepth8'`, not a number; use
  `docPngBits()` in `uxp/common.js`. `Documents.add()` silently ignores `bitsPerChannel` — change
  depth with the setter (`setDocBits()`).
* Change a constant on one side of the wire and you must change the other:
  `SERVER_VERSION` (`server-*/depth_server.py:33`) ↔ `SERVER_EXPECTED_VERSION`
  (`uxp/common.js:19`), and `MAX_TRANSMIT_DIM` (`uxp/common.js:21`).
