# Upstream sources

These trees are vendored, unmodified copies of upstream repositories, with `.git/`,
`__pycache__/` and `.cache/` stripped so the code can live inside this repository. They are kept
here so the runtime is reproducible without chasing moving branches.

| path | upstream | commit | license |
|---|---|---|---|
| `BRIDGE/` | https://github.com/lnbxldn/BRIDGE | `1eb1a2b2a49e1c3e03b83dfa6538cd7ff68e4f36` | Apache-2.0 (`THIRD_PARTY_LICENSES/BRIDGE-LICENSE.txt`) |
| `Iris/` | https://github.com/NUST-Machine-Intelligence-Laboratory/Iris | `bcf43d47a2e5117feada94233a27438ed2997a09` | Apache-2.0 (`THIRD_PARTY_LICENSES/Iris-LICENSE.txt`) |
| `PPD/` | https://github.com/gangweix/pixel-perfect-depth | `427a86f5882aa3f7233c1b94fbdccce87f1c8313` | Apache-2.0 (`THIRD_PARTY_LICENSES/PPD-LICENSE.txt`) |

To restore a pristine checkout of any of them:

```bash
git clone <upstream-url> /tmp/<name>
git -C /tmp/<name> checkout <commit>
```

## Where each model is loaded from

The service resolves models through `MODEL_SPECS` in `server-torch/models.py` and
`server-onnx/models.py`:

* `bridge`, `iris`, `ppd` — import the model package from the vendored `源码/` (Torch variant) or
  `source_dirs: []` (ONNX variant, which loads the exported graphs directly). Only the Torch
  variant actually puts these trees on `sys.path`, via
  `models.add_source_dirs(key, model_root)` (`server-torch/models.py:114-120`).
* `depthpro`, `distillanydepth` — loaded straight through `transformers`; **no local source
  copy** is used. `DistillAnyDepth` deliberately avoids the `pipeline()` wrapper to dodge its
  256-level greyscale quantisation (see `THIRD_PARTY_NOTICE.txt`).

## Local modifications

Applied by this project, not by upstream:

1. `Iris/pipeline.py` — the top-level `import tensorboard` was removed (not needed on the
   inference path; avoids bundling tensorboard).
2. `Iris/utils/image_utils.py` — `import matplotlib` was moved inside `colorize_depth_map`
   (visualisation only; avoids bundling matplotlib).

Everything else in these trees is upstream content at the commit above. The full notice, including
the patches applied to the vendored `diffusers` build, is in
[`../THIRD_PARTY_NOTICE.txt`](../THIRD_PARTY_NOTICE.txt).
