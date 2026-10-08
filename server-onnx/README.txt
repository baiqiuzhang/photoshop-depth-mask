================================================================
Depth Mask Generator (ONNX 分支) — com.zk21.depthpro.windows
协议 7 / 版本 0.2.1
================================================================

1. 这是什么
----------------------------------------------------------------
本插件是 com.zk21.depthpro（torch 版）的 ONNX 分支，面向"非 NVIDIA 独显"
的 Windows 电脑：推理全部走 ONNX Runtime，优先使用 DirectX（DirectML）
加速（NVIDIA/AMD 独显均可），否则回退 CPU。不再打包 torch / transformers /
diffusers 等运行时，也不读取任何 torch 权重（.pth/.safetensors/.pt），
只读取 ONNX 权重（*.onnx + 外置 *.onnx.data）。

前端界面、协议（127.0.0.1:8766，GET /ping、POST /process）、
后处理流水线（离群值裁切 → 归一化+直方图均衡 S1/S5/N1_KMeans/N1A/N2
【原生分辨率、超分之前执行】→ 超分 bilinear/WGIF/JBU-NC/none → 归一化收尾
→ 雾气变换 → 16bit/8bit 输出）与 torch 版完全一致（2026-09-19 同步）。

超分四模式（与 torch 版同源实现）：
- Bilinear：PIL 双线性 + 精确高斯抗锯齿 σ≈k/2 + gated_smooth 边界拉直
- WGIF：加权引导滤波（Li TIP 2015），"半径 r"滑块 1-32，实际半径随 k 放大
- JBU-NC：LR 网格制非凸联合双边（upscale_sr.jbu_nc_lr，2026-09-19 实机修正
  版，与 torch 分支逐位同源）；σr 固定 0.05 无调参项
- 不超分：原生分辨率返回
- 旧 GF 模式退役（同 torch 分支）

2. 模型与加速
----------------------------------------------------------------
本分支内置 5 个模型的 ONNX 导出（全 FP32）：

  模型                分辨率策略                    方向(远亮)   权重大小
  BRIDGE MDE          518 级：784×518 / 518×784     反转         约 5.5 GB
  Depth Pro           固定 1536×1536（fp32）        不反转       约 3.8 GB
  DistillAnyDepth     518 级：518²/518×784/784×518  反转         约 1.4 GB
  Iris                固定 768×1024（两阶段扩散）    反转         约 5.3 GB
  Pixel-Perfect Depth 固定 1024×768（5 步 Euler）    不反转       约 3.2 GB

Depth Pro 权重来源：onnx-community/DepthPro-ONNX 的 fp32 导出（opset 14 纯标准
算子，图内已烘焙 fov 换算 + 1/clamp 后处理，输出即度量深度）。许可为 Apple ML
Research License（非商用，见 THIRD_PARTY_NOTICE.txt）。

加速策略：
- 默认按优先级尝试 DirectML（DmlExecutionProvider，`performance_preference=
  high_performance` + `device_filter=gpu` 锁定独显），失败自动回退
  CPUExecutionProvider（onnxruntime-directml 一个包同时提供两个 EP）。
- 模型 ONNX 图已做 DML 兼容编辑（结果逐位不变）：Reshape allowzero 置 0、
  onnxscript 局部函数内联、rank-0 Expand 改写为 Add+ConstantOfShape。
- 某模型确认无法在 DirectML 上运行时，该模型的后续请求直接走 CPU
  （黑名单缓存，避免重复无效尝试）。
- 输入比例与模型固定形状不一致时，统一"等比缩放居中 + 灰边填充
  (letterbox) → 推理 → 裁剪"，保证不拉伸变形。

3. 重要：Intel 显卡（无独显）机器的 DirectML 禁用
----------------------------------------------------------------
实测 Intel 核显的 DirectML 驱动存在不兼容问题（可能报错/崩溃）。
本服务器启动时会枚举显卡（Win32_VideoController）：
  * 检测到非 Intel 适配器（NVIDIA/AMD 独显等）→ 允许 DirectML；
  * 只检测到 Intel 核显 / Microsoft Basic Display 等 → 强制禁用 DirectML，
    使用 CPU 推理（日志 depth_server.log 会记录原因与所用 provider）。
如果显卡枚举失败（权限问题等）默认允许 DirectML 并靠运行失败自动回退 CPU。

手动禁用 DirectML（枚举失败或想强制 CPU 时）：
  a) 打开"系统属性 → 环境变量 → 用户变量"，新建：
       变量名  DEPTH_DML
       变量值  0
     然后重启 Photoshop（等效命令：setx DEPTH_DML 0）。
  b) 或设置 DEPTH_FORCE_CPU=1 达到同样效果。
  c) 或在插件目录放一个 start_depth_server.bat，用
     "set DEPTH_DML=0" 后再启动 depth_server.exe（同样会生效，
     服务器读取的是环境变量）。

4. 安装
----------------------------------------------------------------
1) 把整个 com.zk21.depthpro.windows 文件夹复制到 Photoshop 插件目录
   （%APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<版本>\Internal\），
   或用 UXP Developer Tool 加载 index.html。
2) Photoshop 中：增效工具 → Depth Mask (ONNX) 面板。
3) 首次点击"生成"会通过 启动深度服务.jsx 自动拉起 depth_server.exe
   （onedir 布局，启动约 1-3 秒）。

注意：
- 端口固定 127.0.0.1:8766（与 torch 版相同）。若同时安装了
  com.zk21.depthpro（torch 版），不要同时运行两个深度服务，否则端口冲突；
  正常使用只需安装其中一个。
- 本分支只读 ONNX 权重；删掉某个 *-onnx 文件夹即可在面板中隐藏对应模型。

5. 磁盘占用（实测构成）
----------------------------------------------------------------
- _internal/（onnxruntime + numpy/PIL/scipy/sklearn/flask 等）≈ 0.6 GB
- 5 套 ONNX 权重合计 ≈ 19.3 GB
- 前端 + 文档 < 1 MB
总计约 20 GB。相比 torch 版（约 20 GB，含 torch/cuda 运行时与 torch 权重），
省去 torch/cuda 运行时（约 4 GB），并可在不需要的模型时删除对应
*-onnx 文件夹进一步缩小。

6. 系统要求
----------------------------------------------------------------
- Windows 10/11 64 位，Photoshop 2023 及以上（UXP 面板）。
- 内存建议 16 GB 以上（Depth Pro 1536² / Iris / PPD 峰值约 3-8 GB 内存）。
- 加速：任意支持 DirectX 12 的独显（NVIDIA/AMD）可用 DirectML；无独显时
  CPU 推理（Iris/PPD 在纯 CPU 上较慢，请耐心等待）。

7. 测试状态（2026-08-31）
----------------------------------------------------------------
- 已实测：RTX 4060 Laptop GPU 混合显卡机器（NVIDIA + Intel 核显 + 虚拟显示器）。
  数值门：5 模型 ONNX 宿主流水线 vs torch 参考，逐阶段 cos ≥ 0.99（图编辑后不变）。
  DirectML 实测（allowzero/内联/Expand 修复 + Depth Pro 换用社区 fp32 导出后）：
    bridge / distillanydepth / depthpro 走 DML（depthpro 66s CPU → 28s DML；
    各模型 DML 与 CPU 输出逐位一致）；
    iris / ppd 因 8GB 显存不足保持 CPU——iris_unet/ppd_dit 的 12288-token 注意力
    中间张量峰值超 8GB（0x8007000E E_OUTOFMEMORY），显存硬限制，无法在不改变
    结果的前提下解决。
- 已实测：DEPTH_DML=0 强制 CPU 路径；Intel 核显机器的自动禁用逻辑
  （本机同时存在 NVIDIA 独显，Intel-only 场景以枚举逻辑 + 手动开关覆盖）。
- 未实测：纯 Intel 核显机器实机；AMD 独显实机（预期可用 DirectML）；更大显存
  （≥16GB）独显上 iris/ppd 的 DML（预期可跑通，图编辑已就绪）。

9. torch 分支第五轮同步（2026-09-19）
----------------------------------------------------------------
torch 分支的五轮改动已全量同步到本分支（代码级，exe 已重打包并验证）：
- 超分四模式 bilinear/wgif/jbu-nc/none（gf 退役）；新增 upscale_sr.py
  （wgif / jbu-nc LR 网格制 jbu_nc_lr / gated_smooth / 精确高斯 AA）
- 直方图均衡换序到超分之前（原生分辨率执行，histogram.stage=
  pre_upscale_native）+ 超分后 normalize_minmax 收尾
- diagnostics 新增 stage_seconds 分段计时；/ping capabilities 改
  wgif_radius/jbu_nc_sigma_r；服务端校验同步；%TEMP% 残留清扫
- img_io 16-bit PNG 解码改 cv2/libpng（修手工解码器 Adam7 隐患，
  新解码器对源数据逐位还原）
- 前端 index.html/main.js/common.js 与 torch 分支同源（四模式下拉 +
  WGIF 半径滑块；JBU-NC σr 固定 0.05 无调参项；fetch 超时兜底；临时文档
  try/finally 清理）
- ONNX 特有逻辑（DML 黑名单/设备回退/图编辑 gate）全部保留
验证（onnx_win_env venv）：test_upscale_sr.py 34/34 全过；源码服务器与
新 exe 各跑 bridge/jbu-nc/S1/16bit /process 全过（model_device=dml，
输出 I;16 全量程 unique=65536，两版输出统计逐位一致）。exe sha256
74311d32d9260beb。按用户指示未安装到 PS Plug-ins（本分支保持工作区内）。

8. 构建（开发者）
----------------------------------------------------------------
打包环境（作者本机历史记录，路径仅作参照）：D:\depth_pro_photoshop_jsx\0.2.1\onnx_win_env
（venv，Python 3.12.4，onnxruntime-directml 1.24.4、flask、opencv-python、
pillow、numpy、pyinstaller 6.22；2026-09-19 创建——旧环境
D:\event_camera\depth_builder 已不存在，anaconda base 仅有纯 CPU onnxruntime 无 DML）。
命令（在自己创建的虚拟环境里执行；依赖清单见同目录 requirements.txt）：
  <venv>\Scripts\python.exe build_windows_onnx.py --dry-run
  <venv>\Scripts\python.exe build_windows_onnx.py
  （--python 默认取当前解释器；--skip-copy-to-plugin 不回写插件目录）
输出：com.zk21.depthpro.windows\（onedir 即插件目录）。
数值门（对照参考夹具 _onnx_ref 下的 *.npz）：本仓库不附带参考夹具与 ONNX 权重，
需先设 DEPTH_MODELS_SRC（ONNX 权重目录）与 DEPTH_ONNX_REF_DIR（参考夹具目录），再运行
  <venv>\Scripts\python.exe gate_onnx_models.py [--models depthpro bridge ...]

9. 版权与许可
----------------------------------------------------------------
模型权重与源码版权归各自作者；本分支仅做 ONNX 格式转换与插件封装，
详见 THIRD_PARTY_NOTICE.txt。
