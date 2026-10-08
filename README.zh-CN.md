# Photoshop 深度蒙版生成器
[English](README.md) | **中文**

一个 Photoshop **UXP** 插件：用本地运行的五种单目深度估计模型，从当前文档生成 **16 位相对深度图与雾蒙版**。

插件分为两部分：

* 一个 **UXP 面板**（纯 JavaScript，无构建步骤）负责导出当前文档、与本地 HTTP 服务通信，并把返回的深度场写进 Photoshop 通道；
* 一个**本地推理服务**（`depth_server`，Python + Flask）绑定在 `127.0.0.1:8766`，每个模型都在一次性的子进程里运行，因此每次请求结束后内存 / 显存都会被回收。

> **纯源码仓库。** 模型权重、冻结的 Python 运行时以及编译好的 `depth_server.exe` 都**不在**仓库中。如何自行获取权重并编译服务，见 [docs/BUILD.md](docs/BUILD.md)。

## 模型

| key | model | 分辨率策略 | 方向 | 来源 |
|---|---|---|---|---|
| `bridge` | BRIDGE MDE | 短边 518（fp32） | 反转 | 上海 AI Lab / 复旦大学 |
| `depthpro` | Depth Pro | 固定 1536×1536 | 原样 | Apple |
| `distillanydepth` | DistillAnyDepth | 短边 518 | 反转 | 西湖 AGI Lab / 上海交通大学 |
| `iris` | Iris | 长边 ≤1536（fp16） | 反转 | 南京理工大学 MIL |
| `ppd` | Pixel-Perfect Depth | 面积 2048×1536 | 原样 | MoGe2 语义变体 |

「方向」统一规范为 *远 = 亮*。`depthpro` 是默认模型。面板的模型下拉框是根据服务旁边**实际发现**的权重文件夹生成的，所以删掉某个权重文件夹就会在 UI 中禁用该模型 —— 插件是模块化的，可以按需裁剪。

两种运行时变体共用同一套前端和同一套 HTTP 协议：

* **Torch 变体**（`com.zk21.depthpro`）—— PyTorch / transformers / diffusers，CUDA 或 CPU。
* **Windows / ONNX 变体**（`com.zk21.depthpro.windows`）—— ONNX Runtime，DirectML（任意 DirectX 12 GPU）并带 CPU 回退。只读 `*.onnx` 及其外部 `*.onnx.data`，从不读 `.pth` / `.safetensors`。

## 仓库结构

```
uxp/                 UXP 面板：manifest、index.html、main.js、common.js、PNG 编解码、测试
  manifest.json        变体 id com.zk21.depthpro        （Torch）
  manifest.onnx.json   变体 id com.zk21.depthpro.windows （ONNX）
server-torch/        Torch 变体的 Python 服务
server-onnx/         Windows / ONNX 变体的 Python 服务
third_party/         上游模型源码树（见 third_party/UPSTREAM.md）
docs/                构建说明与原始中文手册
THIRD_PARTY_NOTICE.txt, THIRD_PARTY_LICENSES/
```

## 处理流程

1. 面板把当前文档渲染成 16 位 PNG（长边限制为 `MAX_TRANSMIT_DIM = 4608`），然后 `POST` 到 `/process`。
2. 服务按请求选择模型，在一次性子进程里跑完，然后依次执行后处理链：异常值裁剪 → 归一化 + 直方图均衡（`S1 / S5 / N1_KMeans / N1A / N2`，或用 `none` 作为 A/B 对照组）→ 超分（`bilinear / WGIF / JBU-NC / none`）→ 最终归一化 → 雾变换。
3. 深度场以 16 位（或 8 位）PNG 返回。面板把它作为通道导入，还可以再派生三级 `depth` / `antidepth` / `middepth`（`DERIVED_MAX_LEVEL = 3`）、反相，以及混合两个通道（`MIX_MAX_DIM = 4096`）。

### HTTP 协议

| 端点 | 方法 | 响应 |
|---|---|---|
| `/ping` | GET | `{"service": "depth_server", "version": "7", ...}` 以及检测到的模型列表 |
| `/process` | POST | 含深度 PNG（base64）与诊断信息的 JSON |

面板要求 `version == "7"`（`uxp/common.js:19` 的 `SERVER_EXPECTED_VERSION` 与 `server-torch/depth_server.py:33`、`server-onnx/depth_server.py:33` 的 `SERVER_VERSION = "7"` 对应）。**版本不匹配时面板会拒绝与服务通信**，所以两侧必须一起改。

## 环境要求

* Photoshop **23.0** 或更高（PS 2023+，见 `uxp/manifest.json:12` 的 `minVersion`）。面板注册在 **增效工具（Plugins）** 菜单下 —— **不是** *窗口 → 扩展*，后者只列出旧的 CEP 扩展。
* 自行编译服务时：Python 3.10+ 与 PyInstaller；Torch 变体另外需要 PyTorch + transformers + diffusers，ONNX 变体需要 `onnxruntime-directml`（DirectML + CPU 两个执行提供者）。
* Torch 变体要有 CUDA 显卡才有可接受的速度；ONNX 变体面向没有 NVIDIA 显卡的机器，并可回退到 CPU。
* UXP 插件面板只在 Photoshop 创建它时被求值：改动任何 JS/CSS/manifest 文件后必须**重启 Photoshop**，仅仅重载面板是不够的。

## 编译与安装

下面这些都是一份新克隆必须自己补齐的内容；[docs/BUILD.md](docs/BUILD.md) 是同一份内容的精简版。

### 总览与目录

两个变体共用同一套前端、讲同一套 HTTP 协议，差别只在 Python 服务与 manifest 的 id/label。一个可用的安装就是一个插件文件夹，里面同时放着前端文件**和**服务：

```
com.zk21.depthpro/            # Torch 变体（uxp/manifest.json）
com.zk21.depthpro.windows/    # ONNX 变体 （uxp/manifest.onnx.json）
  index.html main.js common.js mix_core.js mix_extract.jsx png_codec.js vendor/ icon.png
  启动深度服务.jsx             # 启动器：查找、探测并启动 depth_server.exe
  manifest.json               # ONNX 变体用 manifest.onnx.json
  depth_server.exe _internal/ # 你自己构建的 PyInstaller onedir 产物
  BRIDGE/ DepthPro/ DistillAnyDepth/ Iris/ PPD/                 # Torch 权重
  BRIDGE-onnx/ DepthPro-onnx/ DistillAnyDepth-onnx/ Iris-onnx/ PPD-onnx/   # ONNX 权重
```

`uxp/启动深度服务.jsx` 会在插件文件夹、它的父文件夹以及一层子文件夹里查找 `depth_server.exe`，所以解包后的 `dist/depth_server/` 目录也可以放在子文件夹里。

### 前置条件

* Photoshop 23.0+（PS 2023+），64 位。
* 服务侧使用 Python 3.12 —— 两个参考构建都用 3.12 —— 打包时需要 `pyinstaller`（构建驱动调用 `python -m PyInstaller`）。
* Adobe **UXP Developer Tool** —— 可选，但它是加载和调试面板最省事的方式。
* 运行时依赖：按所用变体从 [`server-torch/requirements.txt`](server-torch/requirements.txt) 或 [`server-onnx/requirements.txt`](server-onnx/requirements.txt) 安装。这两个文件列出了两个服务实际 import 的包，以及参考构建环境记录的版本。
* ⚠️ **不要**直接 `pip install third_party/*/requirements.txt`。那是上游的*研究*环境，钉死的版本与随包服务互斥：Iris 要求 `torch==2.3.1+cu121`、`transformers==4.40.1`、`numpy==1.26.4`，而随包服务实际运行 `torch 2.9.1+cu128`、`transformers 5.13.1`、`diffusers 0.28.0`（Python 3.12）；ONNX 侧参考环境是 Python 3.12.4 + `onnxruntime-directml 1.24.4` + PyInstaller 6.22。只取上游的**源码树**，不要用它们钉的依赖版本。
* Torch 变体：要有 CUDA 显卡才有可接受的速度；ONNX 变体：DirectML（任意 DirectX 12 GPU）并带 CPU 回退，面向没有 NVIDIA 显卡的机器。
* UXP 插件面板只在 Photoshop 创建它时被求值：改动任何 JS/CSS/manifest 文件后必须**重启 Photoshop**，仅仅重载面板是不够的。

### 1. 准备权重

权重**不在**仓库里：一份新克隆一开始没有任何可用模型。服务通过探测插件目录旁边的文件夹来发现模型（`server-torch/models.py:14` 与 `server-onnx/models.py:14` 的 `MODEL_SPECS`）；每个模型独占一个文件夹，且该文件夹必须包含下表列出的**全部**标记文件，否则该模型不会出现在面板里。

#### Torch 变体

| 文件夹 | 必需标记文件 | 说明 |
|---|---|---|
| `BRIDGE/` | `权重/bridge.pth` | 推理包 + `源码/` 加入 `sys.path`；必须以 **fp32** 运行（fp16 在 ViT-g 上会溢出为 NaN） |
| `DepthPro/` | `model.safetensors`, `config.json` | transformers Hub 布局 —— 不需要本地源码副本 |
| `DistillAnyDepth/` | `权重/model.safetensors`, `权重/config.json` | transformers Hub 布局 |
| `Iris/` | `权重/unet/diffusion_pytorch_model.safetensors`, `权重/text_encoder/model.safetensors` | diffusers 布局（`unet` / `text_encoder` / `vae` / `tokenizer` / `scheduler`） |
| `PPD/` | `ppd_moge.pth`, `moge2.pt` | 另外还需要随仓库分发的 `源码/` |

* 文件夹名是固定的（`BRIDGE`、`DepthPro`、`DistillAnyDepth`、`Iris`、`PPD`）；其中 `BRIDGE` / `Iris` / `PPD` 还需要上游的 `源码/` 树，适配器会把它加入 `sys.path`。
* 少一个标记文件 —— 哪怕只是一个 `config.json` —— 该模型就会被报告为未找到，并在 UI 中禁用。

#### Windows / ONNX 变体

ONNX 导出放在带**外部数据**文件的 `*-onnx/` 文件夹里，这也是为什么 `.onnx` 图文件和它的 `.onnx.data` 兄弟文件都是必需的：

| 文件夹 | 必需标记文件 |
|---|---|
| `BRIDGE-onnx/` | `bridge_fp32_784x518.onnx`, `bridge_fp32_518x784.onnx`, `bridge_fp32.onnx_data` |
| `DepthPro-onnx/` | `depthpro_fp32.onnx`, `depthpro_fp32.onnx.data` |
| `DistillAnyDepth-onnx/` | `distillanydepth_fp32_518x518.onnx`, `..._518x784.onnx`, `..._784x518.onnx`, `distillanydepth_fp32.onnx_data` |
| `Iris-onnx/` | `iris_unet.onnx`, `iris_text_encoder.onnx`, `iris_vae_encoder.onnx`, `iris_vae_decoder.onnx` |
| `PPD-onnx/` | `ppd_moge_sem.onnx`, `ppd_dit_step.onnx` |

* ONNX 变体**只**读 `*.onnx` 及其外部数据文件；`.pth` / `.safetensors` 文件夹会被忽略。
* 删掉整个 `*-onnx/` 文件夹是官方支持的插件瘦身方式。

也可以用 `DEPTH_MODEL_ROOT` 环境变量把服务指向仓库外的模型目录（`server-torch/models.py:64`、`server-onnx/models.py:72`）：它会被插到候选链的最前面（环境变量 → 服务所在目录 → 嵌套的插件文件夹 → 父级插件文件夹）。

权重获取来源：

* **BRIDGE MDE** —— https://github.com/lnbxldn/BRIDGE（Apache-2.0）。
* **Depth Pro** —— Hugging Face 上的 `apple/DepthPro-hf`。⚠️ 权重以 **Apple ML Research License（非商用）** 发布。Windows 变体使用的 ONNX 图导出自 `onnx-community/DepthPro-ONNX`（fp32，opset 14；图中已经烘进了 FOV 换算与 `1/clamp`，所以它的 `predicted_depth` 输出*就是*米制深度）。
* **DistillAnyDepth** —— 西湖 AGI Lab / 上海交通大学（MIT）。
* **Iris** —— https://github.com/NUST-Machine-Intelligence-Laboratory/Iris（Apache-2.0）。
* **Pixel-Perfect Depth** —— https://github.com/gangweix/pixel-perfect-depth（Apache-2.0）。

完整的第三方声明与本地 vendor 补丁清单见 [`THIRD_PARTY_NOTICE.txt`](THIRD_PARTY_NOTICE.txt)。

#### 随仓库分发的上游源码该放在哪里（Torch 变体）

Torch 侧的适配器会把 `<权重文件夹>/源码` 加入 `sys.path`（`server-torch/models.py:114` 的 `add_source_dirs()`），然后 import 上游推理包（`from bridge.dpt import Bridge`、`from pipeline import IrisPipeline`、`import ppd`），所以 `third_party/` 下的源码树**不是**可选项 —— 要把它们复制到位：

| 本仓库 | 复制到 | 提供 |
|---|---|---|
| `third_party/BRIDGE/*` | `<模型根目录>/BRIDGE/源码/` | `bridge/` 包（`bridge/dpt.py`） |
| `third_party/Iris/*` | `<模型根目录>/Iris/源码/` | `pipeline.py`（+ `utils/`） |
| `third_party/PPD/*` | `<模型根目录>/PPD/源码/` | `ppd/` 包 |

`DepthPro/` 与 `DistillAnyDepth/` **不需要**本地源码副本（这两个适配器直接通过 `transformers` 加载）；Windows / ONNX 变体**完全不需要** `third_party/` —— 它的适配器只用 ONNX Runtime 与 `server-onnx/` 里的 `onnx_common.py`。

### 2. 安装面板

1. **准备插件文件夹** —— 创建 `com.zk21.depthpro/`（Torch）或 `com.zk21.depthpro.windows/`（ONNX），把 `uxp/` 的内容复制进去。
2. **选择 manifest** —— Torch 变体保留 `manifest.json`；ONNX 变体把 `manifest.onnx.json` 覆盖成 `manifest.json`。两者只差 `id` 和面板 `label`（`uxp/manifest.json:3`、`uxp/manifest.onnx.json:3`）。
3. **加载** —— 用 *UXP Developer Tool* 指向 `manifest.json` 并按 *Load*，或者把整个插件文件夹复制到 Photoshop 扫描的目录（`%APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<版本号>\Internal\`，或 Photoshop 的 `Plug-ins\` 文件夹）。
4. **打开面板** —— Photoshop → **增效工具 / Plugins** → *Depth Mask*；它**不在** *窗口 → 扩展* 下。
5. **每次改动 JS/CSS/manifest 后都要重启 Photoshop** —— UXP 面板只在 Photoshop 创建它时被求值，仅仅重载面板是不够的。

### 3. 编译服务端

`depth_server.exe` **不在**本仓库里；请自行构建 PyInstaller **onedir** 产物。先按所用变体安装运行时依赖：[`server-torch/requirements.txt`](server-torch/requirements.txt) 或 [`server-onnx/requirements.txt`](server-onnx/requirements.txt)。两个 `.spec` 文件里已经写好 hidden imports 与需要收集的二进制，优先用它们，而不是手写 PyInstaller 命令：

```
server-torch/depth_server_v5.spec
server-onnx/depth_server_windows.spec
```

#### Torch 变体

```bash
cd server-torch
python -m PyInstaller --noconfirm --clean depth_server_v5.spec
# -> dist/depth_server/depth_server.exe + dist/depth_server/_internal/
```

`build_depth_server_v5.py` 是围绕同一个 spec 的开发者打包驱动：它执行 `python -m PyInstaller --noconfirm --clean --distpath dist_v7_candidate --workpath build_v7_candidate depth_server_v5.spec`，把权重文件夹就位（DistillAnyDepth 只放 `权重/`；BRIDGE 与 Iris 放 `权重/` + `源码/`；DepthPro 与 PPD 放整个文件夹），逐一校验每个标记文件，给 Iris 源码打补丁（去掉顶层的 `tensorboard` / `matplotlib` import），并写出带 SHA-256 哈希的 `build_manifest_v7.json`。开关：`--python`、`--skip-pyinstaller`、`--source-dist-dir`、`--clean`、`--no-verify-exe`；**没有** `--dry-run`。它是为上游构建工作区写的（权重来自 `../papers/深度实验/模型`，前端来自 `../com.zk21.depthpro/` 与 `../README.txt`），所以在普通克隆里要么自己重建这些路径，要么直接用上面的原生 PyInstaller 命令，再自行把 `uxp/*` 复制到 `depth_server.exe` 旁边。

#### Windows / ONNX 变体

```bash
cd server-onnx
python build_windows_onnx.py --dry-run   # 只打印复制计划与体积，不做任何构建
python build_windows_onnx.py             # PyInstaller + 就位 + /ping 协议自检
```

ONNX 驱动以同样方式包装 `depth_server_windows.spec`（`--noconfirm --clean --distpath dist_win_candidate --workpath build_win_candidate`），把 `*-onnx` 文件夹与前端文件就位，然后**启动新生成的 exe，并断言 `/ping` 报告 `version == "7"`**，最后写出带产物哈希的 `build_manifest_windows.json`。附加开关：`--python`、`--skip-pyinstaller`、`--skip-copy-to-plugin`、`--clean`、`--no-verify-exe`。它的权重来源是 `../papers/深度实验/模型`，回退到 `com.zk21.depthpro.windows/`（`_resolve_models_src`），并且同样需要一个 `com.zk21.depthpro/` 前端文件夹。

`gate_onnx_models.py` 是独立的数值门禁：它把每个模型阶段与 CPU 执行提供者产出的参考 `.npz` 张量逐阶段对比（每阶段与最终输出的 cosine ≥ 0.99），并写出 `gate_results.json`。用 `DEPTH_MODELS_SRC` 可以让它指向别的模型目录。

### 4. 运行与自检

在面板里按 **生成** 会调用 `启动深度服务.jsx`，它定位 `depth_server.exe`、探测 `/ping` 并在后台启动它 —— 如果已有协议版本不对的陈旧服务，会先杀掉它 —— 然后最多长轮询 90 s。测试时也可以手动启动 `depth_server.exe`。

```bash
curl http://127.0.0.1:8766/ping
```

必须报告 `"version": "7"`，与 `uxp/common.js:19` 的 `SERVER_EXPECTED_VERSION` 一致；**不匹配时面板会拒绝与服务通信**。

* `server-torch/run_ps_channel.ps1`、`run_ps_diag.ps1`、`run_ps_e2e.ps1` 与 `run_ps_extract.ps1` 通过 COM 在运行中的 Photoshop 里执行 `.jsx`：`pwsh -File run_ps_e2e.ps1 -Path 'C:\path\to\your.jsx'`。它们的默认 `-Path` 指向本仓库里没有的临时脚本，所以请始终传入自己的路径。
* `server-onnx/smoke_client.py` 会 POST 一张固定的 8 位或 16 位 PNG 并检查输出：`python smoke_client.py --server http://127.0.0.1:8766 --model depthpro --algo none --bits 8 [--image path/to/img.png]`，写出 `smoke_out/<model>_<algo>_<bits>.png`，并打印形状 / dtype / 位深 / min / max / mean / std / 唯一值数量 / 裁剪统计。`server-onnx/test_client.py` 是更全面的客户端测试。
* 深度输出必须验证为**真正的 `uint16` / 16 位 PNG** —— 不要相信文件名或 UI 标签。

### 5. 前端自检

面板中纯 JS 的逻辑不需要 Photoshop 就能运行，在仓库根目录执行：

```bash
node uxp/tests/test_bitdepth.js        # 28 项断言
node uxp/tests/test_frontend_core.js   # 新克隆上是 25 项断言
```

`test_bitdepth.js` 守护 16 位通路（`Document.bitsPerChannel` 是字符串 `'bitDepth16'` / `'bitDepth8'`，`docPngBits` / `setDocBits`，对历史写法 `Number(doc.bitsPerChannel)` 与 `ChangeMode.SIXTEEN` 的静态检查，以及 PNG IHDR 里的位深字节）。`test_frontend_core.js` 覆盖 `png_codec` 与 `mix_core`；当它找不到 `com.zk21.spectrum/spectrum_core.js` 时会打印提示并跳过第 3~6 节，这也是新克隆只报告 **25** 项断言而不是完整套件的原因。两个脚本都支持用 `DEPTHMASK_PLUGIN=<插件目录>` 测试别的插件目录，用 `SPECTRUM_PLUGIN=<目录>` 让 `test_frontend_core.js` 指向同级的 Spectrum 插件。

### 6. 使用面板

打开一个文档，选择模型并按 **生成**：面板导出（已缩放的）图像，`POST` 到 `/process`，服务跑完模型与后处理链，面板把返回的 16 位（或 8 位）PNG 作为名为 `depth` 或 `fog` 的通道导入。

* **模型** —— `bridge | depthpro | distillanydepth | iris | ppd`，默认 `depthpro`；下拉框只列出服务真正找到权重的模型。
* **输出** —— `Depth`（通道 `depth`）或 `Fog`（通道 `fog`，同时也会载入为选区）；`both` 在 0.2.1 中已移除。
* **后处理** —— 直方图均衡 `S1`（默认，全局 CDF）/ `S5` / `N1_KMeans` / `N1A` / `N2`，或用 `none` 作为 A/B 对照组；均在异常值裁剪与归一化之后进行。
* **超分** —— `bilinear`（默认）/ `WGIF` / `JBU-NC` / `none`（交给 Photoshop），之后是最终归一化与雾变换；WGIF 半径滑块（1–32，默认 2）会按超分倍率 k 缩放。
* **派生通道** —— `Depth` / `AntiDepth` / `MidDepth` 各一个按钮，1–3 级（`DERIVED_MAX_LEVEL = 3`；通道名 `depth1-3`、`antidepth1-3`、`middepth1-3`），另有全部生成 / 全部删除开关。
* **AlphaMixer** —— 选通道 A 与通道 B（`Lum Lock` / `depth` / `L` / `R` / `G` / `B` / 自定义），从 8 种运算中选一种（加权平均 / 相乘 / RMS / 滤色 / 差值 / 取最大 / 取最小 / 叠加），拖比例滑块（0–100 %，默认 50 %）后按 `mix`；全部在前端 JS 里完成，写出 `mixerN` 通道。通道提取上限为 `MIX_MAX_DIM = 4096`，Photoshop 会把结果缩回文档尺寸。
* **方向** —— 线上传输统一规范为 **远 = 亮**（每个模型的 `invert_direction` 在 `models.py` 里已内置），所以 PNG 值越低表示「越近」。

需要记住的常量：`MAX_TRANSMIT_DIM = 4608`（`uxp/common.js:21`）、`DERIVED_MAX_LEVEL = 3`（`uxp/main.js:13`）、`MIX_MAX_DIM = 4096`（`uxp/main.js:14`）、端口 `8766`、协议版本 `"7"`。

## 已知问题

* 面板的启动器保留原始中文文件名 `启动深度服务.jsx`；`uxp/common.js` 按文件名解析它，所以不能改名。
* `docs/README.zh-CN.txt` 是最早的中文手册，早于本仓库，里面还提到并不在源码树里的夹具目录（`测试样图\`、`测试输出\`）。
* 磁盘上已打包的 **0.2.1 ONNX 包**是在位深修复落地到 Torch 变体之前构建的：它的 `common.js` / `main.js` 把 `Document.bitsPerChannel` 当数字处理（在 UXP 里它是字符串 `'bitDepth16'`/`'bitDepth8'`），于是 16 位文档被当作 8 位上传，而较大的 16 位文档还可能通过 `copyMerged` → 剪贴板这条路线上传一张*空白*图。**本仓库里的前端已经是修好的版本**；预编译的 `.exe` 包没有重新构建。
* 纯源码：不随包提供权重，所以新克隆一开始没有任何可用模型。
* 开发树里的运行日志（`*.log`）、打补丁前的 ONNX 夹具与 `*.bak-*` 快照有意不发布；请改用 Git 历史。

## 第三方组件

Depth Mask Generator 重新分发了若干上游研究项目的源码。确切的 commit 与本地修改见 [THIRD_PARTY_NOTICE.txt](THIRD_PARTY_NOTICE.txt)、[THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES) 与 [third_party/UPSTREAM.md](third_party/UPSTREAM.md)。

**注意模型*权重*有各自的条款** —— 尤其是 Depth Pro 权重以 Apple ML Research License 发布，**非商用**。本项目随附的声明描述的是完整插件发行版（权重 + 运行时）；本仓库只含源码。

## 许可证

本仓库自有代码采用 MIT 许可 —— 见 [LICENSE](LICENSE)。第三方代码保留各自的许可（Apache-2.0 / MIT），保存在 `THIRD_PARTY_LICENSES/` 与 `third_party/` 下的上游源码树中。
