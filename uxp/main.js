// =======================================================
//  Depth Mask Generator - UXP Plugin 0.2.1 / Protocol 7
//  模块化多模型 + AlphaMixer 通道混合（前端 JS）
//  共享助手见 common.js；纯 JS 数学见 png_codec/mix_core
//  频谱分析已拆分为独立插件 com.zk21.spectrum
// =======================================================
// 注：ps/app/core/batchPlay/fs/storage/localFileSystem、SERVER_URL/PING_URL、
//     SERVER_EXPECTED_VERSION、MAX_TRANSMIT_DIM、uint8ToBase64、sleep、
//     isServerRunning/launchServer/ensureServer、getResizedImageData、
//     importChannelStable/createChannelFromPngBytes/extractChannelPng
//     均由 common.js 定义（先于本文件加载），此处不重复声明。

const DERIVED_MAX_LEVEL = 3;           // depth/antidepth/middepth 各保留 3 层
const MIX_MAX_DIM = 4096;              // 通道提取上限（快速提取路径实测 4096 下耗时数秒；混合后由 PS 缩回文档尺寸）

// 模型固定元数据（前端展示用；可用性以服务端检视结果为准）
const MODEL_OPTIONS = [
    { key: 'bridge',          display: 'BRIDGE MDE' },
    { key: 'depthpro',        display: 'Depth Pro' },
    { key: 'distillanydepth', display: 'DistillAnyDepth' },
    { key: 'iris',            display: 'Iris' },
    { key: 'ppd',             display: 'Pixel-Perfect Depth' },
];

// UI元素（主面板）
const generateBtn  = document.getElementById('generateBtn');
const statusDiv    = document.getElementById('status');
const logDiv       = document.getElementById('log');
const radioModes   = document.querySelectorAll('input[name="mode"]');
const modelSelect  = document.getElementById('modelSelect');
const algoSelect   = document.getElementById('algoSelect');
const paramsContainer = document.getElementById('paramsContainer');
const upscaleSelect = document.getElementById('upscaleSelect');
const wgifRadiusRow  = document.getElementById('wgifRadiusRow');
const wgifRadius     = document.getElementById('wgifRadius');
const wgifRadiusValue = document.getElementById('wgifRadiusValue');

// UI元素（AlphaMixer）
const mixASelect = document.getElementById('mixA');
const mixBSelect = document.getElementById('mixB');
const mixCustomRow = document.getElementById('mixCustomRow');
const mixACustom = document.getElementById('mixACustom');
const mixBCustom = document.getElementById('mixBCustom');
const mixOpSelect = document.getElementById('mixOp');
const mixRatio = document.getElementById('mixRatio');
const mixRatioValue = document.getElementById('mixRatioValue');
const mixBtn = document.getElementById('mixBtn');

// ---------- 参数元数据（直方图后处理；数组套数组 = 多参数合并一行） ----------
const algoParams = {
    S1: [
        { name: 'strength', label: '强度', type: 'range', min: 0, max: 1, step: 0.01, default: 1.0 }
    ],
    S5: [
        [
            { name: 'K_GMM', label: '分量数', type: 'number', min: 2, max: 20, step: 1, default: 5 },
            { name: 'strength', label: '强度', type: 'range', min: 0, max: 1, step: 0.01, default: 1.0 }
        ],
        [
            { name: 'min_segment_ratio', label: '分区比', type: 'range', min: 0, max: 0.1, step: 0.0001, default: 0.001 }
        ]
    ],
    N1_KMeans: [
        [
            { name: 'K', label: '聚类数', type: 'number', min: 2, max: 20, step: 1, default: 5 },
            { name: 'strength', label: '强度', type: 'range', min: 0, max: 1, step: 0.01, default: 1.0 }
        ]
    ],
    N1A: [
        [
            { name: 'smooth_sigma', label: '平滑', type: 'range', min: 0.001, max: 0.1, step: 0.001, default: 0.02 },
            { name: 'height_ratio', label: '峰值', type: 'range', min: 0.001, max: 0.1, step: 0.001, default: 0.01 }
        ],
        [
            { name: 'strength', label: '强度', type: 'range', min: 0, max: 1, step: 0.01, default: 1.0 }
        ]
    ],
    N2: [
        [
            { name: 'model', label: '模型', type: 'select', options: ['auto','uniform','norm_05_02','norm_02_01','norm_08_01','bimodal','left_peak','right_peak'], default: 'auto' },
            { name: 'smooth_sigma', label: '平滑', type: 'range', min: 0, max: 5, step: 0.1, default: 2.0 }
        ],
        [
            { name: 'strength', label: '强度', type: 'range', min: 0, max: 1, step: 0.01, default: 1.0 }
        ]
    ]
};

function buildParamGroup(row, cfg) {
    const label = document.createElement('span');
    label.className = 'p-label';
    label.textContent = cfg.label;
    row.appendChild(label);
    const wrapper = document.createElement('div');
    wrapper.className = 'control-wrapper' + (cfg.type === 'range' ? ' grow' : '');
    let input;
    if (cfg.type === 'range') {
        input = document.createElement('input');
        input.type = 'range';
        input.min = cfg.min; input.max = cfg.max; input.step = cfg.step || 0.01;
        input.value = cfg.default;
        input.dataset.paramName = cfg.name;
        wrapper.appendChild(input);
        const valueSpan = document.createElement('span');
        valueSpan.className = 'range-value';
        valueSpan.textContent = input.value;
        wrapper.appendChild(valueSpan);
        input.addEventListener('input', () => { valueSpan.textContent = input.value; });
    } else if (cfg.type === 'number') {
        input = document.createElement('input');
        input.type = 'number';
        input.min = cfg.min; input.max = cfg.max; input.step = cfg.step || 1;
        input.value = cfg.default;
        input.dataset.paramName = cfg.name;
        wrapper.appendChild(input);
    } else if (cfg.type === 'select') {
        input = document.createElement('select');
        cfg.options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            if (String(opt) === String(cfg.default)) option.selected = true;
            input.appendChild(option);
        });
        input.dataset.paramName = cfg.name;
        wrapper.appendChild(input);
    }
    row.appendChild(wrapper);
}

function buildParamsInto(container, configs) {
    if (!container) return;
    container.innerHTML = '';
    if (!configs) return;
    configs.forEach(cfg => {
        const row = document.createElement('div');
        row.className = 'param-row';
        (Array.isArray(cfg) ? cfg : [cfg]).forEach(g => buildParamGroup(row, g));
        container.appendChild(row);
    });
}

function buildParams(algorithm) {
    buildParamsInto(paramsContainer, (algorithm === 'none') ? null : algoParams[algorithm]);
}

function collectParamsFrom(container) {
    const params = {};
    if (!container) return params;
    const inputs = container.querySelectorAll('input, select');
    inputs.forEach(el => {
        const name = el.dataset.paramName;
        if (el.type === 'number' || el.type === 'range') {
            params[name] = parseFloat(el.value);
        } else {
            params[name] = el.value;
        }
    });
    return params;
}

function collectParams(algorithm) {
    if (algorithm === 'none') return {};
    return collectParamsFrom(paramsContainer);
}

// ---------- 模型下拉（由服务端检视结果决定选项） ----------
function populateModels(foundKeys) {
    modelSelect.innerHTML = '';
    let firstEnabled = null;
    MODEL_OPTIONS.forEach(m => {
        // 服务端已检视时，未检出权重的模型直接不显示（而非灰置）
        if (foundKeys && !foundKeys.includes(m.key)) return;
        const opt = document.createElement('option');
        opt.value = m.key;
        opt.textContent = m.display;
        if (!firstEnabled) firstEnabled = m.key;
        modelSelect.appendChild(opt);
    });
    if (firstEnabled) modelSelect.value = firstEnabled;
}

async function syncModelsFromServer() {
    try {
        const res = await fetch(PING_URL, { mode: 'cors', credentials: 'omit' });
        if (!res.ok) return false;
        const data = await res.json();
        if (String(data.version) !== SERVER_EXPECTED_VERSION) return false;
        const found = (data.available_models || []).filter(m => m.found).map(m => m.key);
        const missing = (data.available_models || []).filter(m => !m.found).map(m => m.key);
        populateModels(found);
        if (missing.length) {
            log(`⚠️ 未检出的模型权重（下拉已禁用）: ${missing.join(', ')}`);
        }
        return true;
    } catch (e) {
        return false;
    }
}

// ---------- 日志与状态 ----------
function log(msg) {
    const time = new Date().toLocaleTimeString();
    const entry = `[${time}] ${msg}`;
    if (logDiv) {
        logDiv.innerHTML += `<div>${entry}</div>`;
        logDiv.scrollTop = logDiv.scrollHeight;
    } else {
        console.log(entry);
    }
}

function setStatus(msg) {
    if (statusDiv) {
        statusDiv.textContent = msg;
    } else {
        console.log('Status:', msg);
    }
}

function logDiagnostics(diagnostics) {
    if (!diagnostics) return;
    const d = diagnostics;
    if (d.model) log(`  ├─ 模型: ${d.model}（native ${JSON.stringify(d.native_shape || '?')}，${d.model_device || '?'}）`);
    if (d.invert_direction !== undefined) log(`  │  方向: ${d.invert_direction ? '已反转（远亮）' : '远亮'}`);
    if (d.upscale) log(`  │  超分: ${d.upscale.mode}${d.upscale.radius ? ' r=' + d.upscale.radius : ''}${d.upscale.fallback ? '（回退: ' + d.upscale.fallback + '）' : ''}`);
    if (d.histogram) log(`  │  直方图: ${d.histogram.algorithm}${d.histogram.applied ? '' : '（未应用）'}`);
    log(`  └─ 输出位深: ${d.output_bit_depth || '?'}bit，尺寸 ${JSON.stringify(d.output_shape || '?')}`);
}

// ---------- 主生成流程 ----------
async function generateDepthMask() {
    let selectedMode = 'depth';
    for (let radio of radioModes) { if (radio.checked) { selectedMode = radio.value; break; } }
    const options = [selectedMode];
    const model = modelSelect ? modelSelect.value : 'depthpro';
    const algo = algoSelect.value;
    const params = collectParams(algo);
    const postprocess = { algorithm: algo, params: params };
    const upscaleMode = upscaleSelect ? upscaleSelect.value : 'bilinear';
    const upscale = { mode: upscaleMode, params: {} };
    if (upscaleMode === 'wgif') {
        upscale.params = { radius: parseInt(wgifRadius ? wgifRadius.value : '2', 10) };
    } else if (upscaleMode === 'jbu-nc') {
        // σr 已固定服务端默认（0.05）：实测 0.02–0.10 差异低于 8-bit 可见阈值
        upscale.params = {};
    }

    const doc = app.activeDocument;
    if (!doc) { app.showAlert('请先打开一张图片'); return; }
    if (doc.mode !== ps.constants.DocumentMode.RGB) {
        await core.executeAsModal(async () => {
            await doc.changeMode(ps.constants.ChangeMode.RGB);
        }, { commandName: 'convertDocumentToRGB' });
    }

    generateBtn.disabled = true;
    setStatus('准备图像...');
    log('📤 正在导出当前图像...');

    try {
        const maxTransmitDim = MAX_TRANSMIT_DIM;
        const { base64: base64Image, origWidth, origHeight, scale, bits: uploadBits } = await getResizedImageData(doc, maxTransmitDim);
        log(`  ├─ 图像缩放比例: ${scale.toFixed(3)}，原始尺寸: ${origWidth}x${origHeight}，上传 PNG 位深: ${uploadBits}bit`);

        const requestBody = {
            model: model,
            image: base64Image,
            options: options,
            postprocess: postprocess,
            original_size: [origWidth, origHeight],
            upscale: upscale
        };
        log(`  ├─ 模型: ${model}，输出: ${selectedMode}，后处理: ${algo}，超分: ${upscaleMode}${upscaleMode === 'wgif' ? ' r=' + upscale.params.radius : ''}`);
        if (upscaleMode === 'none') log(`  ├─ 不超分：深度按模型原生分辨率返回，由 PS 端放大`);
        setStatus('正在处理...');
        log('⏳ 发送到深度服务...');
        // 超时兜底：服务端 WORKER_TIMEOUT=7200s，前端放宽到 2.2h；AbortController
        // 在 UXP 上可能不可用，做能力检测（无则维持旧行为，不设超时）。
        let abortCtrl = null;
        let fetchTimer = null;
        const FETCH_TIMEOUT_MS = 7920 * 1000;
        const fetchInit = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        };
        try {
            if (typeof AbortController === 'function') {
                abortCtrl = new AbortController();
                fetchInit.signal = abortCtrl.signal;
                fetchTimer = setTimeout(() => abortCtrl.abort(), FETCH_TIMEOUT_MS);
            }
        } catch (e) { abortCtrl = null; }
        let response;
        try {
            response = await fetch(SERVER_URL, fetchInit);
        } catch (err) {
            if (abortCtrl && abortCtrl.signal && abortCtrl.signal.aborted) {
                throw new Error('请求超时（>2 小时）：深度服务未在期限内返回。'
                    + '可尝试换更快的模型（DepthPro/DistillAnyDepth）、关闭 Iris/PPD 或缩小输出尺寸。');
            }
            throw new Error('无法连接深度服务：' + (err.message || err)
                + '（depth_server.exe 未运行？请重试生成以自动拉起服务）');
        } finally {
            if (fetchTimer) clearTimeout(fetchTimer);
        }
        if (!response.ok) {
            const errText = await response.text();
            if (response.status === 404) {
                throw new Error(`服务路由不匹配（404）：通常是残留的旧版 depth_server.exe 占用端口。请在任务管理器结束 depth_server.exe 后重试，或重启 Photoshop。详情: ${errText}`);
            }
            throw new Error(`服务器错误 (${response.status}): ${errText}`);
        }
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        logDiagnostics(result.diagnostics);

        setStatus('导入通道...');
        log('📥 开始导入深度通道');
        const tempFolder = await storage.localFileSystem.getTemporaryFolder();
        const resizeTo = upscaleMode === 'none' ? true : null;

        if (result.fog) {
            await core.executeAsModal(async () => {
                await importChannelStable(doc, 'fog', result.fog, tempFolder, resizeTo);
            }, { commandName: 'importFog' });
        }
        if (result.depth) {
            await core.executeAsModal(async () => {
                await importChannelStable(doc, 'depth', result.depth, tempFolder, resizeTo);
            }, { commandName: 'importDepth' });
        }
        if (result.fog) {
            await core.executeAsModal(async () => {
                const fogChan = doc.channels.getByName('fog');
                if (fogChan) {
                    await doc.selection.load(fogChan, ps.constants.SelectionType.REPLACE, false);
                    log('✨ Fog 通道已载入选区');
                }
            }, { commandName: 'loadFogSelection' });
        }

        setStatus('完成');
        log('🎉 深度掩码生成完毕');
    } catch (error) {
        console.error('完整错误:', error);
        setStatus('错误');
        log(`❌ 错误: ${error.message || error}`);
        app.showAlert('生成失败: ' + (error.message || error));
    } finally {
        generateBtn.disabled = false;
    }
}

// ============================================================
// 派生通道生成（depth/antidepth/middepth 各 1-3 层，每行一键）
// ============================================================

async function duplicateChannel(srcName, newName) {
    const doc = app.activeDocument;
    try { doc.channels.getByName(srcName); } catch(e) { throw new Error(`源通道 "${srcName}" 不存在`); }
    try {
        const existing = doc.channels.getByName(newName);
        if (existing) existing.remove();
    } catch (e) {}
    await batchPlay([
        {
            _obj: "duplicate",
            _target: { _ref: "channel", _name: srcName },
            to: { _ref: "document", _id: doc.id },
            name: newName
        }
    ], { synchronousExecution: true });
    log(`  └─ 复制通道 "${srcName}" → "${newName}"`);
}

function removeChannelIfExists(name) {
    const doc = app.activeDocument;
    try {
        const ch = doc.channels.getByName(name);
        if (ch) ch.remove();
    } catch(e) {}
}

async function calculateChannel(srcA, srcB, blendMode, dstName) {
    const doc = app.activeDocument;
    try { doc.channels.getByName(srcA); } catch(e) { throw new Error(`源通道 "${srcA}" 不存在`); }
    if (srcB) {
        try { doc.channels.getByName(srcB); } catch(e) { throw new Error(`源通道 "${srcB}" 不存在`); }
    }
    try {
        const existing = doc.channels.getByName(dstName);
        if (existing) existing.remove();
    } catch (e) {}
    const calculationCmd = {
        "_obj": "make",
        "new": { "_class": "channel" },
        "using": {
            "_obj": "calculation",
            "to": { "_ref": "channel", "_name": srcA },
            "calculation": { "_enum": "calculationType", "_value": blendMode },
            "source2": { "_ref": "channel", "_name": srcB || srcA }
        },
        "_isCommand": true
    };
    await batchPlay([calculationCmd], {});
    const channels = doc.channels;
    const newChannel = channels[channels.length - 1];
    const tempName = newChannel.name;
    if (tempName !== dstName) {
        await duplicateChannel(tempName, dstName);
        removeChannelIfExists(tempName);
    }
    log(`  └─ 计算完成: ${srcA} ${blendMode} ${srcB || srcA} → ${dstName}`);
}

async function squareChannel(srcName, dstName) {
    await calculateChannel(srcName, srcName, "multiply", dstName);
}

async function invertChannel(srcName, dstName) {
    const doc = app.activeDocument;
    try { doc.channels.getByName(srcName); } catch(e) { throw new Error(`源通道 "${srcName}" 不存在`); }
    try { const ch = doc.channels.getByName(dstName); if (ch) ch.remove(); } catch(e) {}
    await duplicateChannel(srcName, dstName);
    await batchPlay([
        { _obj: "invert", _target: { _ref: "channel", _name: dstName } }
    ], { synchronousExecution: true });
}

async function addAndInvert(src1Name, src2Name, dstName) {
    const tempName = '__temp_add_' + Date.now();
    await calculateChannel(src1Name, src2Name, "add", tempName);
    await invertChannel(tempName, dstName);
    removeChannelIfExists(tempName);
}

function getSourceChannelName() {
    const doc = app.activeDocument;
    try { doc.channels.getByName('depth'); return 'depth'; } catch(e) {}
    try { doc.channels.getByName('fog'); return 'fog'; } catch(e) {}
    return null;
}

async function generateDerivedChannels(type, levels, keepAll = false) {
    const doc = app.activeDocument;
    if (!doc) throw new Error('请先打开一张图片');
    const srcName = getSourceChannelName();
    if (!srcName) throw new Error('当前文档没有 "depth" 或 "fog" 通道，请先生成深度掩码。');
    log(`📌 使用 "${srcName}" 作为源通道生成派生通道`);

    let levelList = [...new Set(levels)].sort((a,b) => a-b);
    levelList = levelList.filter(l => l >= 1 && l <= DERIVED_MAX_LEVEL);
    if (levelList.length === 0) throw new Error(`${type} 只支持 1-${DERIVED_MAX_LEVEL} 级`);

    const maxLevel = Math.max(...levelList);
    const generated = new Set();

    const ensureDepth = async (l) => {
        if (generated.has(`depth${l}`)) return;
        if (l === 1) {
            await duplicateChannel(srcName, 'depth1');
            generated.add('depth1');
            log(`  ✅ 生成 depth1`);
            return;
        }
        await ensureDepth(l - 1);
        await squareChannel(`depth${l-1}`, `depth${l}`);
        generated.add(`depth${l}`);
        log(`  ✅ 生成 depth${l}`);
    };

    const ensureAntiDepth = async (l) => {
        if (generated.has(`antidepth${l}`)) return;
        if (l === 1) {
            await ensureDepth(1);
            await invertChannel('depth1', 'antidepth1');
            generated.add('antidepth1');
            log(`  ✅ 生成 antidepth1`);
            return;
        }
        await ensureAntiDepth(l - 1);
        await squareChannel(`antidepth${l-1}`, `antidepth${l}`);
        generated.add(`antidepth${l}`);
        log(`  ✅ 生成 antidepth${l}`);
    };

    if (type === 'depth') {
        for (let l = 1; l <= maxLevel; l++) { await ensureDepth(l); }
    } else if (type === 'antidepth') {
        await ensureDepth(1);
        for (let l = 1; l <= maxLevel; l++) { await ensureAntiDepth(l); }
    } else if (type === 'middepth') {
        const need = maxLevel + 1;
        await ensureDepth(need);
        await ensureAntiDepth(need);
        for (let l = 1; l <= maxLevel; l++) {
            const dep = `depth${l+1}`;
            const ant = `antidepth${l+1}`;
            const target = `middepth${l}`;
            await addAndInvert(dep, ant, target);
            generated.add(target);
            log(`  ✅ 生成 ${target}`);
        }
    }

    if (!keepAll) {
        const toDelete = [];
        if (type === 'depth') {
            for (let l = 1; l < maxLevel; l++) toDelete.push(`depth${l}`);
        } else if (type === 'antidepth') {
            for (let l = 1; l < maxLevel; l++) toDelete.push(`antidepth${l}`);
            toDelete.push('depth1');
        } else if (type === 'middepth') {
            // 只清理最深层纯过程通道（need = maxLevel+1，即 depth4/anti4）；
            // depth1-3 / anti1-3 无论原本有还是本次生成都保留
            toDelete.push(`depth${maxLevel + 1}`);
            toDelete.push(`antidepth${maxLevel + 1}`);
        }
        const uniqueDelete = [...new Set(toDelete)];
        for (let name of uniqueDelete) {
            if (name === 'depth' || name === 'fog') continue;
            try {
                const ch = doc.channels.getByName(name);
                if (ch) { ch.remove(); log(`  🗑️ 删除中间通道 ${name}`); }
            } catch(e) {}
        }
    }
    log(`🎉 ${type} 系列通道生成完成`);
}

// ============================================================
// AlphaMixer：提取两个通道灰度 → 前端 JS 混合 → 生成 mixerN 通道
// ============================================================

function getMixChannelName(sel, custom) {
    // UXP 的 select.value 在某些情况下为 undefined，回退到 selectedIndex 取选项值
    let v = sel && sel.value;
    if (!v && sel && sel.selectedIndex >= 0 && sel.options && sel.options[sel.selectedIndex]) {
        v = sel.options[sel.selectedIndex].value;
    }
    if (v === '__custom__') {
        const name = (custom.value || '').trim();
        if (!name) throw new Error('自定义通道名为空');
        return name;
    }
    return v;
}

function nextMixerName(doc) {
    let i = 1;
    for (;;) {
        const name = 'mixer' + i;
        let exists = false;
        try { exists = !!doc.channels.getByName(name); } catch (e) { exists = false; }
        if (!exists) return name;
        i++;
    }
}

async function mixChannels() {
    const doc = app.activeDocument;
    if (!doc) { app.showAlert('请先打开一张图片'); return; }
    let nameA, nameB;
    try {
        nameA = getMixChannelName(mixASelect, mixACustom);
        nameB = getMixChannelName(mixBSelect, mixBCustom);
    } catch (e) {
        app.showAlert(e.message);
        return;
    }
    // L / Lum Lock 支持亮度回退（无同名通道时按明度提取），不做存在性预检
    const LUM_FALLBACK = ['L', 'Lum Lock'];
    for (const [nm, isLum] of [[nameA, LUM_FALLBACK.includes(nameA)], [nameB, LUM_FALLBACK.includes(nameB)]]) {
        if (isLum) continue;
        let exists = false;
        try { exists = !!doc.channels.getByName(nm); } catch (e) { exists = false; }
        if (!exists) { app.showAlert(`通道 "${nm}" 不存在`); return; }
    }

    const op = mixOpSelect.value || 'average'; // UXP select 无 selected 时 value 可能 undefined
    // 滑块方向与下拉栏布局一致（左端=左下拉栏 A）。average/multiply/screen/rms 的
    // w=A 权重（w=1→纯 A，w=0→纯 B），故 w = 1 - 滑块位置；difference/max/min/overlay
    // 保持"A→运算结果 R"渐变语义（w=0→纯 A，w=1→R），直接 w = 滑块位置。
    const LINEAR_GROUP = ['average', 'multiply', 'screen', 'rms'];
    const v = parseInt(mixRatio.value, 10) / 100;
    const linear = LINEAR_GROUP.includes(op);
    const w = linear ? 1 - v : v;
    mixBtn.disabled = true;
    setStatus('混合中...');
    log(`🎛️ AlphaMixer: ${nameA} ${op} ${nameB}，滑块 ${Math.round(v * 100)}%` +
        (linear ? `（左端=${nameA}，右端=${nameB}）` : `（左端=${nameA}，右端=${nameA}${op}${nameB}）`));
    const tempFolder = await storage.localFileSystem.getTemporaryFolder();
    try {
        let pngA, pngB;
        let tAll = _perfNow(), t0;
        // 提取走纯 UXP 流程（extractChannelPngUxp 内部包 executeAsModal——通道
        // 复制/applyImage/saveAs 均为 UXP 官方 API，modal 内可用）；明度回退由
        // extractChannelPng 内部自行包 modal。
        pngA = await extractChannelPng(doc, nameA, tempFolder, MIX_MAX_DIM);
        pngB = await extractChannelPng(doc, nameB, tempFolder, MIX_MAX_DIM);
        mixStep('mix extractChannels', tAll);

        t0 = _perfNow();
        const decA = PngCodec.decode(pngA);
        const decB = PngCodec.decode(pngB);
        mixStep(`mix decode (${pngA.length + pngB.length} bytes)`, t0);
        if (decA.width !== decB.width || decA.height !== decB.height) {
            throw new Error(`两张通道尺寸不一致（${decA.width}x${decA.height} vs ${decB.width}x${decB.height}）`);
        }
        t0 = _perfNow();
        const ga = PngCodec.grayFloat(decA);
        const gb = PngCodec.grayFloat(decB);
        mixStep(`mix grayFloat (${decA.width}x${decA.height})`, t0);
        t0 = _perfNow();
        const res = MixCore.mix(ga, gb, op, w);
        mixStep('mix compute', t0);
        t0 = _perfNow();
        const bits = docPngBits(doc);
        const outPng = PngCodec.encodeGray(res.data, decA.width, decA.height, bits);
        mixStep('mix encodeGray', t0);

        const mixerName = nextMixerName(doc);
        const needResize = !(decA.width === Number(doc.width) && decA.height === Number(doc.height));
        t0 = _perfNow();
        await core.executeAsModal(async () => {
            await createChannelFromPngBytes(doc, mixerName, outPng, tempFolder, needResize);
        }, { commandName: 'importMixer' });
        mixStep('mix importChannel', t0);
        mixStepLog(`  ⏱ mix 总耗时: ${(_perfNow() - tAll).toFixed(0)}ms`);

        setStatus('完成');
        log(`🎉 已生成通道 ${mixerName}（${op}，${decA.width}x${decA.height}，${bits}bit）`);
    } catch (e) {
        setStatus('错误');
        log(`❌ mix 失败: ${e.message || e}`);
        app.showAlert('mix 失败: ' + (e.message || e));
    } finally {
        mixBtn.disabled = false;
    }
}

// ============================================================
// 事件绑定
// ============================================================

// ---------- 全局互斥锁 ----------
// Photoshop 的 modal scope 全局唯一，生成/混合/派生通道任一流程执行期间，
// 其他入口并发 executeAsModal / AdobeScriptAutomation 会被拒绝（错误 9 或
// "only allowed from inside a modal scope"）。所有重操作入口统一走 runExclusive。
let pluginBusy = false;

async function runExclusive(fn) {
    if (pluginBusy) {
        app.showAlert('正在处理中，请稍候...');
        return false;
    }
    pluginBusy = true;
    try {
        await fn();
        return true;
    } finally {
        pluginBusy = false;
    }
}

document.querySelectorAll('.gen-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const type = btn.dataset.type;
        const level = btn.dataset.level;
        await runExclusive(async () => {
            try {
                if (level === 'all') {
                    const maxLevel = DERIVED_MAX_LEVEL;
                    const doc = app.activeDocument;
                    if (!doc) { app.showAlert('请先打开一张图片'); return; }
                    let allExist = true;
                    for (let i = 1; i <= maxLevel; i++) {
                        const name = type + i;
                        const ch = doc.channels.getByName(name);
                        if (!ch) { allExist = false; break; }
                    }
                    if (allExist) {
                        await core.executeAsModal(async () => {
                            for (let i = 1; i <= maxLevel; i++) {
                                const name = type + i;
                                try {
                                    const ch = doc.channels.getByName(name);
                                    if (ch) ch.remove();
                                } catch(e) {}
                            }
                            log(`🗑️ 已删除 ${type} 系列全部通道`);
                        }, { commandName: 'deleteAllDerived' });
                    } else {
                        const levels = Array.from({ length: maxLevel }, (_, i) => i+1);
                        await core.executeAsModal(async () => {
                            await generateDerivedChannels(type, levels, true);
                        }, { commandName: 'genDerivedAll' });
                    }
                }
            } catch (e) {
                const msg = e.message || e.toString() || '未知错误';
                log(`❌ 操作失败: ${msg}`);
                app.showAlert(`操作失败: ${msg}`);
            }
        });
    });
});

generateBtn.addEventListener('click', async () => {
    await runExclusive(async () => {
        try {
            setStatus('正在启动深度服务器…');
            await ensureServer();
            await generateDepthMask();
        } catch (e) {
            const msg = e.message || e.toString() || '未知错误';
            log(`❌ 生成过程出错: ${msg}`);
            setStatus('错误');
            app.showAlert(`生成失败: ${msg}`);
        }
    });
});

mixBtn.addEventListener('click', () => runExclusive(mixChannels));

algoSelect.addEventListener('change', () => {
    buildParams(algoSelect.value);
});

upscaleSelect.addEventListener('change', () => {
    if (wgifRadiusRow) wgifRadiusRow.style.display = upscaleSelect.value === 'wgif' ? 'flex' : 'none';
});

if (wgifRadius) {
    wgifRadius.addEventListener('input', () => {
        if (wgifRadiusValue) wgifRadiusValue.textContent = wgifRadius.value;
    });
}

if (mixRatio) {
    mixRatio.addEventListener('input', () => {
        if (mixRatioValue) mixRatioValue.textContent = mixRatio.value + '%';
    });
}

// 自定义输入共享一行（任一选"自定义"才出现，仅 +1 行，下拉保留可退出）
function syncMixCustom() {
    if (!mixCustomRow) return;
    const aCustom = mixASelect.value === '__custom__';
    const bCustom = mixBSelect.value === '__custom__';
    mixCustomRow.style.display = (aCustom || bCustom) ? '' : 'none';
    mixACustom.style.display = aCustom ? '' : 'none';
    mixBCustom.style.display = bCustom ? '' : 'none';
}
mixASelect.addEventListener('change', syncMixCustom);
mixBSelect.addEventListener('change', syncMixCustom);
syncMixCustom();

// 初始化
buildParams('S1');
populateModels(null);   // 先全量占位（服务未起时也可选）
syncModelsFromServer(); // 服务已运行则按检视结果刷新
log('🚀 Depth Mask 插件 0.2.1 已加载（协议 7；模块化多模型；AlphaMixer 前端 JS 混合）');
setStatus('就绪');
