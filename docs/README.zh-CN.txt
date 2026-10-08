==========================================================================
 Depth Mask Generator 0.2.1（协议 7：模块化 5 模型）
==========================================================================

一、项目简介
----------------------------------------------------------------------------
本插件在 Photoshop 面板中生成 16-bit/8-bit 相对深度图与雾效蒙版。0.2.1 重构
彻底废除 dp/dav3/hybrid 三核心方案与 hybrid 融合算法，改为模块化多模型：

  模型（权重随插件分发，按需使用）：
    BRIDGE MDE        短边 518（fp32），边缘结构好
    Depth Pro         1536×1536，边缘/细节精度高（默认）
    DistillAnyDepth   短边 518，天空等纯色区域鲁棒
    Iris              长边 ≤1536（fp16），扩散模型，最慢
    Pixel-Perfect     面积 2048×1536，连续深度，最慢

权重不直接放在插件根目录，而是放在各自的"权重名称文件夹"下
（BRIDGE/ DepthPro/ DistillAnyDepth/ Iris/ PPD/，命名与
papers/深度实验/模型 一致）。服务启动时（以及每次 /ping）检视插件目录，
把检出的模型反馈给面板"模型"下拉；删除某个权重文件夹即可在 UI 中禁用对应
模型（模块化可拆装）。

对外为单一服务 EXE（depth_server.exe，PyInstaller onedir），
每个模型在一次性子进程中按需推理，完成后整进程回收内存/显存。

二、目录结构
----------------------------------------------------------------------------
  com.zk21.depthpro\   最终安装文件夹（深度主插件：EXE、依赖、5 套模型权重）
  com.zk21.spectrum\   最终安装文件夹（频谱分析独立插件，纯前端 JS，无权重）
  测试样图\            不可再生输入样图
  测试输出\            （历史 v6 测试产物，可清理）
  源码\                后端与算法源码、模型适配器、前端源码、构建脚本、测试
  README.txt           本文件

com.zk21.depthpro 内部结构：
  manifest.json / index.html / main.js / icon.png / 启动深度服务.jsx    UXP 前端
  depth_server.exe / _internal\                                        服务与依赖
  BRIDGE\ DepthPro\ DistillAnyDepth\ Iris\ PPD\                        5 套模型权重（含推理所需源码）
  THIRD_PARTY_NOTICE.txt / THIRD_PARTY_LICENSES\                       第三方许可与 vendor 补丁说明
  build_manifest_v7.json                                               构建清单（哈希）

三、安装方法
----------------------------------------------------------------------------
方式 A（推荐）：
  1. 打开 Photoshop 2023+（manifest 声明 minVersion 23.0）。
  2. 安装 UXP Developer Tool（Adobe 官方工具），登录 Adobe。
  3. UXP Developer Tool → Add Plugin → 选择 com.zk21.depthpro\manifest.json → Load。
  4. Photoshop 菜单 → 插件 → Depth Mask 打开面板。

方式 B（直接复制）：
  1. 关闭 Photoshop，将整个 com.zk21.depthpro\ 复制到
     %APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<版本号>\Internal\ 下。
  2. 重新打开 Photoshop，在 插件 菜单中启用 Depth Mask。

首次生成时会通过 启动深度服务.jsx 自动启动 depth_server.exe（临时 bat 后台启动，
监听 127.0.0.1:8766，版本感知 /ping：发现旧版残留服务自动 taskkill 后重启）。

四、面板使用说明
----------------------------------------------------------------------------
  模型       bridge | depthpro | distillanydepth | iris | ppd（默认 depthpro；
            选项由服务端检视插件目录决定，未检出权重的模型自动禁用）
  输出       Depth | Fog（both 已废除；Depth=深度蒙版，Fog=雾效蒙版并载入选区）
  后处理     S1 全局CDF（默认）/ 无 / S5 / N1_KMeans / N1A / N2（直方图均衡化）
  超分       Bilinear（默认）/ WGIF 加权引导 / JBU-NC 非凸联合双边 / 不超分
            （留给 PS 处理；0.2.1 超分选型依据 papers/超分实验 36 组全指标评选）
            "半径 r"滑块（1-32，默认 2，仅 WGIF）为基准柔化系数，实际半径按
            上采样倍率 k（目标长边/深度原生长边）动态放大：抗锯齿平滑遍数
            n=round(k²/2.92)（clamp 1-8），WGIF 实际半径 r_eff=r·k/2、
            引导低通 r_lp=round(k)（clamp 后同前）；k≤1 不做额外平滑
            JBU-NC 无调参项（σr 固定 0.05）：2026-09-19 实测 σr 0.02-0.10 全程
            差异低于 8-bit 可见阈值，滑块取消（详见 20260919 第四轮后 jbu-nc 修正节）
  派生通道   Depth / AntiDepth / MidDepth 各一键（与标签合并为紧凑按钮，
            左列竖排），生成 1-3 层（depth1-3、antidepth1-3、middepth1-3）；
            键为"全部生成/全部删除"开关。原 1-5 数字键已废除。
  AlphaMixer 派生区右侧子面板：通道A ▾ / 通道B ▾（Lum Lock / depth / L / R /
            G / B / 自定义——选自定义出现文本框，下拉保留可退出）、运算 ▾
            （8 种）、比例滑块（0-100%，默认 50%）、[mix] 按钮。
            mix 完全在前端 JS 完成：提取两通道灰度（复制文档→索引删除其余
            通道→转灰度→PNG）→ 8 种加权运算 → 生成 mixerN 通道（顺位编号
            mixer1/mixer2/…，同名覆盖）。
  AlphaMixer 运算（比例 w=A 的权重；w=0/1 为纯端点）：
            加权平均 +  w·A+(1-w)·B
            乘法 ×      A^w·B^(1-w)
            平方平均 RMS sqrt(w·A²+(1-w)·B²)
            滤色 Screen  1-(1-A)^w·(1-B)^(1-w)
            差值        w·|A-B|+(1-w)·A
            最大/最小    w·max/min(A,B)+(1-w)·A
            叠加 Overlay w·overlay(A,B)+(1-w)·A
  频谱分析   独立插件 com.zk21.spectrum（增效工具菜单→频谱分析，单面板）。
            方法：2D DFT、DCT、Gabor、DWT、简化 Shearlet；窗口可选 Hann
            （边缘优化）。**频谱预览实时更新**：以文档指纹（文档 id + 历史
            记录数 + 尺寸 + 位深）轮询检测，编辑/切换文档或调整参数即自动
            重算；预览为一维频谱分布（类直方图，CSS 柱状图）——DFT/DCT 为
            径向平均功率谱、Gabor/Shearlet 为方向能量曲线、DWT 为尺度能量柱。
            **导出采用直存（saveAs 副本，不创建临时文档 → 不弹新文件）**。
            点击"生成"以 2048 上限计算并生成/更新 2D 频率功率空间分布通道
            （fft_power / dct_coeff / gabor_mag / dwt_sub / shearlet_mag，
            同名覆盖；DWT 为细节能量叠加图，无四块拼接）。面板为
            左图右控两栏布局：分布图占左栏全高（y 轴显著加高），
            方法/窗口/参数/生成键移至右侧竖排（340×210 不变）。
            右栏底部新增"自动预览"复选框：取消勾选即停止全部后台重算
            （面板折叠/切走时也会自动暂停，见下方修复记录）。
  频谱分析修复 0.2.1 修复记录（2026-09-11）：
            ① downsample() 两个分支返回形状不一致（不需要缩放时返回
              {data,width,height}，需要缩放时直接返回裸 Float32Array），
              导致 Gabor/Shearlet（cap 768）与方向谱（cap 160）在实机尺寸
              下 ds.width/ds.height/ds.data 全为 undefined，逐层退化成 NaN、
              最终 normalize01 填 0.5 → 预览画成整块纯色、生成产出 0×0 PNG
              后 app.open 报错。两侧统一返回 {data,width,height}。
              原 Node 单测只用 ≤256 的小图，从未进入缩放分支，故漏检；
              已在 源码/test_frontend_core.js 增补 960×720 / 2048×1536 回归块。
            ② 方法/参数取值不再读 select.value：UXP 下 option 属性反射不可靠
              （depthpro/main.js 已记录同类问题），改为以 selectedIndex 为准 +
              JS 白名单（METHOD/WINDOW/LEVELS_ORDER），范围滑块同样带 HTML
              默认值兜底；option 数与白名单不符时 console 告警一次。
            ③ 关闭面板后不再后台运算：轮询改为可启停，检测到面板不可见
              （document.visibilityState + visibilitychange，带能力检测）即
              清掉全部定时器，恢复可见时重算一次；指纹需稳定 600ms 才动手
              （避免连续涂抹把 duplicate+saveAs+FFT 打成串）；生成通道与
              预览分析共用 busy 互斥。无 visibilityState 的 UXP 版本退化为
              常驻轮询 + "自动预览"开关手动停。
  频谱分析修复 0.2.1 第二轮（2026-09-11 晚）：
            ④ 每次重算都新建临时文档：exportDocPng 在文档长边超过 maxDim 时
              会 doc.duplicate()，预览 maxDim=512 → 任何正常照片每次重算都在
              PS 里闪出一个临时文档。改为新增 readDocLuminance()：
              imaging.getPixels({documentID, sourceBounds, targetSize,
              componentSize, applyAlpha}) 让 PS 直接返回按上限缩好的合成像素，
              不建文档、不写临时 PNG、不做全分辨率解码；顺带按 Rec.709 计算
              亮度（旧路径 png_codec.grayFloat 注释写"取亮度"实际只取了 R
              通道）。失败自动回退 exportDocPng 旧路径，行为不劣化。
              踩坑记录（2026-09-11 实机）：getPixels 返回对象上的 width/height
              不可信（实测取到 NaN）。拿它算尺寸会让 dftMag 的 nextPow2(NaN)=1，
              径向谱塌成 2 个 bin → 分布图变成整块纯色柱状图、横坐标出现
              "0 0 0.5 0.5 0.5"。现在几何一律以请求的 targetSize 为准，通道数由
              imageData.length 反推，并加两道校验（长度与几何自洽 + 灰度不能退化
              成常量），任一不满足即回退旧路径。无头冒烟增加了「返回对象不含
              width/height」「数据半截」「imaging 不可用」「无 visibilityState」
              四种模式的用例。
            ⑤ "自动预览"复选框与参数滑块重合，且「方向数」「层数」两个参数
              根本出不来。两个原因：
              a) index.html 给 #orientRow / #levelsRow 行本身写了 inline
                 style="display:none"，而 syncParams() 只切换行内子元素——
                 inline 优先级最高，两行永远打不开（Shearlet 的方向数、DWT
                 的层数此前一直无法设置）。已删掉这两个 inline 样式。
              b) 参数行原来靠"子元素全隐藏后行高自动塌缩"来占 0 高，UXP 下
                 塌缩不可靠，行内滑块会溢出叠到下一行。改为显隐以「整行」为
                 单位（syncParams 直接 setRow(freqRow/thetaRow/orientRow/
                 levelsRow, show)），行隐藏即 display:none，结构上不可能重叠。
              配套：#specParams{flex-shrink:0}、参数行 min-height:18px、
              manifest minimumSize.height 150→185（保证最矮时全部行放得下、
              "生成"键不被裁）。
            后三种模式与前两种输出差异（本轮只记录，未改代码）：实测同一张
              2000×1339 真实图，输出网格 dft/dwt 2000×1339、dct 1024×686、
              gabor/shearlet 768×514；归一化 dft/dct 用 percentileClip(0.002)
              （=1.0 占 0.20%）、gabor/dwt/shearlet 用 normalize01（极值恰为
              0/1，=1.0 占 0%）。均值 dft 0.474 / dct 0.307 / gabor 0.275 /
              shearlet 0.274 / dwt 0.065——dwt 细节能量图极度右偏，64.7% 像素
              落在最暗一档，故看起来几乎全黑；gabor 与 shearlet 的直方图几乎
              完全相同（shearlet 即多方向 Gabor 响应之和）。
  频谱分析修复 0.2.1 第三轮（2026-09-11 深夜）：
            ⑥ 多次生成/刷新后残留临时文档：两条创建路径的关闭都不保险——
              exportDocPng 的 finally 里 close() 失败被 catch 吞掉；
              createChannelFromPngBytes 的 app.open 之后完全没有兜底，中途抛错
              就永久留下临时文档。新增 closeTempDoc()（常规关闭→模态内重试
              一次→仍失败则登记 doc id）与 sweepTempDocs()（每次生成前、以及
              回退取像素前，兜底关闭登记过的 id 和名字明确是插件的临时文档
              exp_<数字>.png / dep_<通道名>.png，只认自己的命名、不碰用户
              文档，每关一个打日志）；createChannelFromPngBytes 整段套
              try/finally。无头冒烟新增 6 条用例（含"首次关闭失败可重试"
              "两次都失败则登记并在下轮清掉""用户文档不被触碰"）。
            ⑦ 面板加宽：preferredDockedSize 340×210 → 380×210、
              minimumSize 250×185 → 320×200（右栏宽度见 ⑩ 的 118→136）。
            ⑧ 高频信息（免费部分）：FFT/卷积缓冲由 Float64Array 改 Float32Array
              （内存减半，8 位输入下精度损失不可见——dft/dwt 输出统计与改前
              逐位一致）；拆掉没道理的小封顶：gabor/shearlet 768→1000、
              dct 1024→2048（⑨ 进一步改为跟随 UI 的「分析尺寸」）。实测同一张
              2000×1339 真实图（生成路径）：
              dct 输出 1024×686 → 2000×1339（204→743ms）、gabor 768×514 →
              1000×670（175→192ms）、shearlet 768×514 → 1000×670
              （982→1028ms）——gabor/shearlet 几乎免费，因为 768 与 1000 的
              零填充后同为 1024² FFT。
            原分辨率可行性：实机实测数据（PS 26.0.0 / uxp-8.0.1）
              · imaging.getPixels 正确用法：外层返回 {imageData, sourceBounds,
                level}，像素要 await imageData.getData()；两层都在
                executeAsModal 内；必须显式 colorSpace:'RGB'（否则 CMYK/Lab
                文档返回对应分量）。此前直接读外层 → 每次都解析失败回落旧
                路径 → 旧路径每次 duplicate → 这正是"多次刷新后残留"的来源。
              · 内存：Float32/Float64 数组实提交到 1536MB 都成功（只 new 不写
                等于只预留地址空间，1GB 也 0ms 返回，必须写页才算数）。
              · dft 实测 2048² 1089ms、3072² 4138ms、4096² 5090ms 全成功。
              · 结论：4K 级的 DFT/DCT/DWT 在纯 JS 里就能做，**不需要**分块
                Welch、也不需要外接 Python 服务。GPU/WASM 路线仍排除：
                UXP 面板不支持 WebGL，WASM 在 PS 2025 上会崩。
            ⑨ 新增「分析尺寸」下拉（1024 / 2048 / 4096，默认 4096），位置在
              「自动预览」下面。它决定**生成通道**时的分析上限：dft/dwt 跟随
              该上限，dct 的 cap 直接用该值，gabor/shearlet 取 min(2000, 该值)
              （它们单次要 4 份 pw² 缓冲，越过 2000 会跳到 8192²，约 1.07GB
              且 12 方向极慢，core 里保留了 2000 硬上限）。实时预览固定 512
              长边保持秒回，不受该选项影响；切换该选项不触发重算，只在状态栏
              提示「分析尺寸 N（点生成生效）」。生成完成后状态栏会带上实际
              输出网格，例如「已生成通道 fft_power（2000×1339）」。
              生成耗时随尺寸上升（4096 档 dft/dct/dwt 约 5s，shearlet 约
              10～20s），故生成中提示改为「生成中（上限 4096，约 5～20 秒）」。
            ⑩ UI 对齐修正：右栏原来用 flex:0 0 auto 的自然宽度做标签，2 字
              （方法/窗口）与 4 字（自动预览/分析尺寸）宽度不同，导致每行控件
              左边缘各自错位；参数行还比其它行矮 2px。现改为**定宽标签列**
              （.label 与 .param-label 同为 50px、同为 line-height 20px），
              所有行统一 20px 高 → 下拉/滑块/勾选框左边缘共线、右边缘与
              .range-value 的右对齐线也共线。右栏由 118px 放宽到 136px，
              使定宽标签后控件仍有 82px（否则「Shearlet」会显示不全）。
              另修 y 轴与图顶的 2px 错位：.y-axis 补上 margin-top:2px，
              与 #chart 的 margin-top 一致，最上面的「1.0」刻度才与图顶齐平。
              实机截图确认：方法/窗口/层数/自动预览/分析尺寸五行共线。
            方框容器，控件行高 19-20px、行距 1px，模块间用发丝分隔线
            （后处理+超分合并为一行，选项显示简称、value 不变）；
            多参数后处理同类参数合并同行（S5/N1A/N2 最多 2 行）；
            AlphaMixer 无标题行（A/B 行首 Mixer 小标签），比例滑块与
            mix 键同行/运算行，极矮面板下日志自动收缩兜底；
            超分半径滑块样式已与后处理调参滑块统一
            （.param-row 结构）。

五、算法处理流程（服务端，按序）
----------------------------------------------------------------------------
  1. 单目深度估计神经网络（所选模型原生分辨率推理，不预先放大）
  2. 无效像素中值替换（raw 0 为合法远区值，保留为白）+ 有效值全量程
     归一化、方向统一为"远亮"（与深度实验 python 基准输出一致，亮度链路
     不再被分位裁切拉伸）
  3. 直方图均衡化（按"后处理"选项，默认 S1）——**在原生分辨率、超分之前
     执行**（2026-09-19 第四轮修订：5 个均衡算法均为纯值 LUT，换序后由超分
     插值在重映射值之间重建过渡坡，原理性消除"天地边界平行灰带"——旧序
     会把超分宽过渡带的稀疏中间调经 CDF 压成平架+陡台阶，_DSC9662 实测
     平架斜率 0.083→0.022/k；附带使 S5 GMM 等重算法从最大 64MP 降到原生
     ~0.4MP，成本不随输出尺寸增长）
  4. 超分辨率（bilinear / WGIF 加权引导滤波 / JBU-NC 非凸联合双边 / 不超分）
  5. min-max 重归一化（仿射，保持剖面形状）+ 雾气深度变换（robust 相对线性
     雾映射，作用于均衡化后的深度）
  6. 采样到 16bit/8bit 位深度返回 PS
     —— 输出位深由输入图像位深决定：PS 16-bit 文档 → 16-bit（I;16 PNG）；
        8-bit 文档 → 8-bit（L PNG）。面板无位深选择器。

六、超分辨率说明（实装自 papers/超分实验，36 组像素级评选胜出算法）
----------------------------------------------------------------------------
  - Bilinear：PIL BILINEAR 放大 + 精确高斯抗锯齿低通 σ≈k/2（0.2.1 边缘质量
    修订：旧版为 3×3 级联且遍数 clamp 8，大倍率欠平滑产生断层样台阶）
  - WGIF：加权引导滤波（Li et al., IEEE TIP 2015）。先 bilinear 放大，再以
    传输图像明度（低通 r_lp）为引导，有效 eps 按引导方差加权（边缘区更保真、
    抑制 halo）；"半径 r"控制边缘柔化程度；失败自动回退 bilinear
  - JBU-NC：非凸联合双边上采样（Lu et al., MTAP 2018），LR 网格制实装
    （2026-09-19 实机修正）。Cauchy 型范围核按 RGB 引导在 LR 样本间选边，
    空间窗口 r=4、sigma_s=2.0（LR 像素单位，任意 k 下恒覆盖 ≈2 个 LR 采样
    间距）；数据项为 LR 深度双线性展开（无块纹），范围核不做截断（全帧实测
    截断在纹理引导上产生逐像素权重闪烁→斑点噪声，关闭后干净且指标不变）；
    σr 固定 0.05。旧 HR 网格制（窗口 9 目标像素固定）在大倍率（k>4）下窗口
    覆盖不足一个 LR 采样间距，引导失效退化为≈bilinear——实验（k 1.35-3.95）
    恰在其有效区，故实验结论不能外推到大倍率。失败自动回退 bilinear
  - 边界拉直后处理（三模式统一，2026-09-19 边缘质量修订）：LR 深度的边界
    台阶是宽带位置锯齿，σ≈k/2 低通只能压约 3×；天空-地面边界处 RGB 引导无
    对比，细化算法无信息去重排边缘，台阶全部保留（实测与目视双证）。故在
    超分后统一做 gated_smooth：引导无真实边处加重各向同性平滑拉直边界，
    引导有边处原样保留（门控场羽化防接缝）。
    第三轮修订（法向灰带 + 过度圆滑）：法向剖面实测（papers/超分实验/
    scripts/profile_audit.py）证实用户所见"与天地边缘平行的灰带"是宽过渡带
    被 S1 直方图均衡压成平带（S1 对像素稀疏的中间调区间压缩值域）——与过度
    圆滑同源。故 σ 改为按倍率 k 动态缩放并按模式定稿基准：bilinear 2.0k /
    wgif 1.2k / jbu-nc 1.6k，封顶 8px。过渡带收窄后 S1 反而将中间调当作稠密
    区间重新拉伸，灰带随宽度收敛而消退（_DSC1655 平台度 0.57→1.17）。
    代价：引导无边处的深度边缘过渡带仍比无拉直略宽（远场边界可接受）
  - 不超分：返回模型原生分辨率深度，由 PS 端放大（缩放临时文档后复制通道）
  - 旧 GF 引导滤波模式已退役（实现缺陷见 papers/超分实验报告.md §2.2）；
    dav3-EG（原"边缘引导细化"）已随 DAV3 一并废除

七、磁盘占用（约 20.5G，实测组成）
----------------------------------------------------------------------------
  5 套权重合计 ~15.3G（BRIDGE 4.8G / Iris 4.1G / PPD 3.2G / DepthPro 1.8G /
  DistillAnyDepth 1.4G——占总量 ~74%，模型本身不可压缩）
  _internal ~5.0G（torch+CUDA 库、transformers、diffusers、timm 等）
  depth_server.exe ~0.12G；前端/文档/许可 <0.05G
  提示：模块化设计允许按需删除不需要的权重文件夹以节省空间（对应模型会在
  面板中自动禁用）。

八、系统要求与注意事项
----------------------------------------------------------------------------
  - NVIDIA CUDA 显卡（8G 显存可运行全部模型，单次仅加载一个模型）；无 CUDA
    时回退 CPU（速度大幅下降；Iris/PPD 会非常慢）。
  - 运行时 torch 2.9.1+cu128：支持到 RTX 50（sm_120）并兼容旧架构；RTX 50
    为重新打包验证项（见"九、测试状态"），旧显卡已回归验证。
  - 显存需求峰值：Iris ~7.7G（长边 1536，超限自动降到 1024/768）、
    BRIDGE ~6.2G、PPD ~5.5G、DepthPro ~3.8G、DistillAnyDepth ~1.8G。
  - BRIDGE 必须 fp32（fp16 溢出为 NaN）；Iris 用 fp16。
  - 已废除：hybrid 融合、DAV3/DP 固定双模型、both 输出、tiling 分块推理、
    白块/硬边/融合/质量/边缘/增强选项、depth/antidepth/middepth 1-5 数字键。

九、测试状态
----------------------------------------------------------------------------
  已实测（本机 RTX 4060 8G + PS 2025）：
    - 5 模型适配器推理（native 分辨率/位深/方向/无 NaN）
    - /process 端到端（5 模型 × 超分 × 直方图 × 16/8bit，输出 I;16/L 校验）
    - 16-bit 输入 → 16-bit 输出；8-bit 输入 → 8-bit 输出
    - 服务 /ping version=7 与 5 模型检视
    - 前端纯 JS 模块 Node 单元测试（png_codec 往返与滤波器解码 / mix_core
      8 运算×比例端点与公式 / spectrum_core FFT·DCT·Gabor·DWT 解析向量，
      34/34 通过）
    - PS 2025：深度主插件面板显示自身 UI（模型/后处理/超分/AlphaMixer 控件，
      AX 树实测）；通道提取链路实测（复制文档→索引删除其余通道→转灰度→PNG，
      16-bit 值精确保真 0.2510）
  超分重构实测（2026-09-19，torch 2.9.1+cu128 重打包后）：
    - 源码层自检 28 项全过（源码/test_upscale_sr.py：常数图保恒、guide 缺失/
      尺寸不符回退、sigma_r 越界钳制、算法异常回退、gf 退役、服务端校验拒绝）
    - WGIF/JBU-NC 部署输出与实验实现逐位一致（max|Δ|=0）
    - bilinear 路径与旧版逐位一致（回归）
    - exe 实测（depthpro，device=cuda）：bilinear/wgif/jbu-nc/none × depth，
      wgif/jbu-nc × fog，jbu-nc σr=0.02 8bit，wgif×S5 直方图回归——
      输出全部 I;16/L 全量程，upscale 诊断无回退
    - 683×1024 与 3072×4608 两档耗时正常（83s 内含模型推理）
    - 安装目录同步后 /ping 复验（4 模式 capabilities + 5 模型检出）
  边缘质量修订与性能/鲁棒性加固实测（2026-09-19 第二轮）：
    - 伪影根因（数据+目视双证，papers/超分实验/scripts/audit_edge*.py、
      _final_*.png）：LR 深度边界台阶为宽带位置锯齿，σ≈k/2 低通仅能压约 3×；
      天空-地面边界处 RGB 引导无对比，细化算法无信息去重排边缘 → 台阶全保留
    - 修复：bilinear 的 3×3 级联（clamp 8）→ 精确高斯 σ≈k/2；三模式统一增加
      gated_smooth 边界拉直后处理（引导无边处 σ≈2.5k 加重平滑、有边处保留，
      门控场羽化防接缝）。36 对全指标回归：边界锯齿 jag_boundary
      bilinear 0.168→0.148 / wgif 0.152→0.143 / jbu-nc 0.144→0.134，
      平坦区高频 flat_std 全线下降（wgif 0.0018→0.0012），目视 _DSC8497
      地平线台阶基本消除。备选的"结构张量定向拉直"方案因梳状伪影被目视否决
    - 性能：16-bit PNG 解码改 cv2（逐位一致校验，4608 档 17s→0.6s，30×）；
      jbu-nc 内层缓冲复用（逐位一致，4608 档 SR 18.8s→9.8s）；diagnostics
      新增 stage_seconds 分段计时（decode/infer/sr/hist/fog/encode）。
      评估后未改：双重 b64 解码（C 速，非瓶颈）、_resize_rgb 合并（行为中性优先）、
      flask 单线程（8G 显存下并发推理会 OOM，串行是保护）
    - 鲁棒性：jbu-nc wsum 防护、NaN 引导回退用例、前端临时 PNG try/finally
      兜底清理、fetch 超时（2.2h，AbortController 能力检测）、服务启动清扫
      %TEMP%/depth_v7_* 崩溃残留（>24h）、自检 29 项全过（test_upscale_sr.py）
    - exe 复验：/ping 4 模式 + 5 模型检出；3 模式 × depth/fog 16bit 全过；
      安装位哈希运行前后一致（2cd5e102dc6abb5e）
  第三轮（法向灰带修复 + 动态 σ，2026-09-19）：
    - 法向剖面审计（profile_audit.py，S1 开/关同组件对比）：_DSC1655 noS1
      平台度 1.15–1.76（近线性），开 S1 后宽度 5.5k→7.5k、平台度 0.22–0.59
      ——灰带由 S1 压平宽过渡带产生；拉直过强（σ=2.5k 固定）是同源放大器
    - 修复：σ 按模式动态（bilinear 2.0k / wgif 1.2k / jbu-nc 1.6k，封顶 8px）。
      剖面复测：_DSC1655 wgif 平台度 0.57→1.17、宽度 7.3k→6.4k；_DSC8497
      wgif 宽度 7.4k→4.7k。36 对回归：resid（台阶振幅）bilinear 1.49→1.22、
      jbu-nc 1.45→1.28，wgif 0.75→1.02（亚像素级，换取灰带消退），锯齿度与
      平坦度基本不变；目视 _dyn_*.png 确认边缘更紧致、灰带变窄
    - exe 复验：3 模式 smoke 全过（straighten 诊断 σ 随 k 缩放），安装位哈希
      a40fa43a973f3bf7 运行前后一致
  第四轮（灰带原理性消除——后处理换序，2026-09-19）：
    - 根因（_DSC9662 只读数值分析 + 目视）：天地边界处 LR 原生是 0.7k 锐阶，
      超分链加宽为平滑坡（noS1 无伪影），S1 把坡上稀疏中间调经 CDF 压成
      "平架+陡台阶"——即与天地边缘平行的灰带，三模式同机制
    - 修复：直方图均衡移到超分之前（原生分辨率执行；5 算法均为纯值 LUT，
      换序语义干净）。9662 三模式目视（_band_old/new.png、_band_zoom.png）：
      平行灰带/石柱光晕基本消除；36 对回归全指标改善（jag_all 0.13–0.17→
      0.11–0.13、resid 全降、texture_copy 全降）
    - 附带：S5 GMM 成本恒定在原生分辨率（不再随输出尺寸增长，实测 S1 于
      4608 档 normalize_hist 0.36s）；diagnostics.histogram 新增
      stage=pre_upscale_native 标记
    - 已否决候选：LR 预锐化（无效）、S1 后法向锐化（掩盖不消除）、过渡带
      抖动（有害）
    - exe 复验：6 用例 smoke 全过（S1/S5/fog/8bit/4608），安装位哈希
      7659bf023f728e5a 运行前后一致
  第五轮（jbu-nc 实机退化修正 + σr 调参项取消，2026-09-19）：
    - 用户实测反馈两问题：σr 滑块调节无可感知差异；jbu-nc 输出像"RGB 边缘
      与深度锯齿边各 50% 混合"，与实验中"强 RGB 边缘引导"不符
    - 诊断（papers/超分实验/scripts/diag_jbunc_deploy.py，真实模型 LR × 2 图 ×
      k∈{2,4,8}）：σr 部署档 0.02→0.10 全程仅 0.1-2.6% 像素变化超 8-bit LSB
      （k 越大越无感）；输出梯度与双线性基线相关 0.88-0.98、与 RGB 引导相关
      ≈0，RGB 边特异性 ≈1（改动不集中在引导边）——旧实装在大 k 下引导失效
    - 根因：旧 jbu-nc 空间窗口 9 目标像素固定，k>4 时不足一个 LR 采样间距，
      范围核看不到深度过渡带两侧样本；实验（TARGET_LONG=2048，k 1.35-3.95）
      恰在有效区，结论不可外推
    - 修正：jbu-nc 改 LR 网格制（jbu_nc_lr）——空间核 r=4/σs=2.0 以 LR 像素
      为单位（任意 k 恒覆盖 ≈2 个 LR 间距）；数据项 = LR 深度双线性展开；
      范围核比较 HR 明度与 LR 样本引导（面积降采样限带，抑制纹理拷贝）；
      Cauchy 截断取消（全帧实测截断掩码在纹理引导上逐像素闪烁→黑斑点
      0.45% 像素，关闭后干净且指标不变）。自检 34 项全过（新增边缘吸附
      行为锚：主跳变 253→271，RGB 目标 272）
    - 全帧验证（_DSC9662，bridge，真实 upscale 路径）：bf1 0.31→0.37、
      过渡带宽 2.33→1.85px（k=2.6）｜w 8.5→5.4px（k=8 crop）；纹理区干净
      无斑点无纹理拷贝失控（tex 0.32→0.56，仍低于实验版 1.03）；4096 档
      jbu 单级 10.6s（4608 档预计 ~15s）
    - UI：JBU-NC"容差 σr"滑块取消（参数固定服务端默认 0.05，服务端仍兼容
      接收）；WGIF"半径 r"滑块保留
    - exe 复验：dist smoke /ping 5 模型 + jbu-nc depth 16bit 全过
      （straighten 诊断正常、无回退），新 exe sha256 6f9164e575bae24f，
      安装目录与 dist 逐位一致
  尚未验证（交付后需在 PS 中手动点验）：
    - jbu-nc LR 网格制新实装的实机观感（边缘贴合度、纹理区干净度、大倍率
      档 4608 耗时）；RTX 50 / sm_120 真机
    - AlphaMixer 面板按钮流（mix 按钮 → mixerN 通道生成）；通道提取的 UXP
      端实机行为（ExtendScript 侧已验证配方，UXP 运行时行为未自动化验证）
    - 频谱插件面板按钮流（实时预览渲染、生成 → fft_power 等通道）
    - 8K 大图与多图连续使用、MacOS 平台
  频谱插件 0.2.1 修复后的实测记录（2026-09-11，PS 2025 26.x）：
    - 已实测：插件由 Plug-ins\com.zk21.spectrum 加载（面板出现新增的
      "自动预览"复选框即证明加载的是修复版）；面板渲染正常；
      2D DFT 预览出图、状态栏"实时 dft 频谱分布"；方法切到 DWT 后
      状态栏与图表同步变为尺度能量柱、层数参数行出现；切到 Shearlet
      后方向数参数行出现、图表为多柱方向能量曲线（修复前该路径必错）；
      两侧文件 md5 一致。
    - 尚未实测：document.visibilityState / visibilitychange 在 UXP 中是否
      真的派发（代码已做能力检测，缺失时退化为常驻轮询 + 手动开关；
      两条分支都在无头冒烟里通过）；面板最小尺寸（250×150）下的逐行几何
      未程序化测量（340×210 常用尺寸下余量充足，且 生成 按钮完整可见）；
      "生成"按钮实际写入通道未在用户打开的文档上执行（仅验证到生成的
      16-bit PNG 几何与可解码性）；DCT 在非 2 的幂尺寸下的数值精度问题
      本轮未修（nextPow2(2n) ≠ 2n 时为近似，不报错）。
    - 面板残留：UXP 打包安装目录 PluginsStorage\PHSP\26\Internal\
      com.zk21.spectrum 里另有一份 8 月 27 日的旧副本（未被本次加载，
      当前生效的是 Plug-ins 副本）；若日后用 CCX 重新安装，需注意会覆盖。
  频谱插件第二轮修复后待实机确认（改的是运行时行为，UXP 只在面板创建时
  执行 index.html，必须关掉"频谱分析"面板再打开一次或重启 PS 才生效）：
    - 编辑图像时不再闪出临时文档标签（改用 imaging.getPixels 后，正常路径
      不再调用 exportDocPng；无头冒烟实测 getPixels=有/exportDocPng=0，
      并把「imaging 不可用时回退旧路径」也做成了用例）
    - 参数滑块与"自动预览"勾选框不再重合（参数行改为整行显隐）；面板高度
      下限 185
    - 「方向数」（Shearlet）与「层数」（DWT）两行此前因 inline
      display:none 从未显示过，本轮一并修好，需要在面板里确认真的出现
    - imaging.getPixels 在 PS 2025 26.x 上的实测结果（是否需模态、返回
      components/componentSize 实际取值）——若失败会自动回退，届时状态栏
      与 console 会提示，需要在面板里确认一次
  订正上一轮的一处错误结论：上一轮我据低分辨率截图判断"切到 DWT 后层数
  参数行出现、切到 Shearlet 后方向数参数行出现"，实际那两行当时根本没显示
  （inline display:none 所致），该结论作废，已按上面的 ⑤a 修复。

十、构建与源码
----------------------------------------------------------------------------
  源码位于 源码\：depth_server.py（协议 7 服务）、models.py（模型注册表与
  寻址检测）、model_*.py（5 个模型适配器）、postprocess.py（深度归一化/超分/
  雾气变换/量化）、upscale_sr.py（超分细化算法 WGIF/JBU-NC）、histogram_eq.py
  （直方图均衡化）、img_io.py（16bit PNG 解码）、depth_server_v5.spec +
  build_depth_server_v5.py（onedir 构建与 staging）、test_client.py（协议 7
  测试客户端）、test_upscale_sr.py（超分自检）、run_v7_e2e.sh（端到端矩阵）、
  test_frontend_core.js（前端纯 JS 模块单元测试，Node 运行：34 项断言）。

  前端文件位于 com.zk21.depthpro\：index.html（主面板）、main.js（主面板逻辑
  + AlphaMixer）、common.js（共享 UXP 助手：服务器引导/图像导出/通道导入/
  通道提取）、png_codec.js（JS PNG 编解码）、mix_core.js（8 种加权融合运算）、
  vendor/pako.min.js（PNG 压缩，MIT）。
  频谱插件 com.zk21.spectrum\：manifest.json（单面板，340×210）、
  index.html（左图右控两栏布局）、spectrum.js（实时预览 + 点击生成通道）、
  spectrum_core.js（FFT/DCT/Gabor/DWT/简化Shearlet）、png_codec.js、
  common.js、vendor/pako.min.js。两个插件均为纯前端 JS，服务端零改动
  （协议保持 7，exe 无需重建）。

  构建环境：D:\depth_pro_photoshop_jsx\0.2.1\depth_v8_builder
  （torch 2.9.1+cu128——支持 RTX 50 / sm_120、transformers 5.13.1 +
  diffusers 0.28.0 + peft/timm/utils3d/cv2/omegaconf/PyInstaller 6.20）。
  diffusers 与 transformers 5.x 的兼容性通过 4 处 vendor 补丁解决
  （详见 THIRD_PARTY_NOTICE.txt"本地修改"）。

  构建命令（在 源码\ 下，仅重建 exe+_internal）：
    ..\depth_v8_builder\python.exe -m PyInstaller --noconfirm --clean
      --distpath ..\dist_v7_candidate --workpath ..\build_v7_candidate
      depth_server_v5.spec
  完整 staging（含 5 套权重复制，需 papers/深度实验/模型 就位）：
    build_depth_server_v5.py（见该脚本 --help）
