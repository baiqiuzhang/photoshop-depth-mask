// =======================================================
//  common.js — 主面板与频谱面板共享的 UXP 助手（协议 7）
//  服务器引导 / 图像导出 / 通道导入 / 通道提取 / PNG 通道创建
// =======================================================

const ps = require('photoshop');
const app = ps.app;
const core = ps.core;
const batchPlay = ps.action.batchPlay;
// 不再使用 UXP 的 fs 垫片（PS-57707 同族缺陷 "Route not found"：垫片按路径字符串
// 解析内部路由表，PS 2023 部分环境下匹配失败即抛错）。文件读取统一走 storage API
// （File.read），插件因此不依赖 UXP fs 模块。
const { storage } = require('uxp');
const { localFileSystem } = require("uxp").storage;

const SERVER_PORT = 8766;
const SERVER_URL = `http://127.0.0.1:${SERVER_PORT}/process`;
const PING_URL   = `http://127.0.0.1:${SERVER_PORT}/ping`;
const SERVER_EXPECTED_VERSION = "7";   // 与 depth_server.py 的 SERVER_VERSION 保持一致

const MAX_TRANSMIT_DIM = 4608;

// ========== 位深 ==========
// UXP 的 Document.bitsPerChannel 返回的是字符串枚举 'bitDepth8'/'bitDepth16'/
// 'bitDepth32'（PS 内置 API 把 batchPlay 的 depth 8/16/32 映射成这个字符串），
// 不是数字——用 Number(...) === 16 判断会恒为 false，16bit 文档会被当成 8bit。
// PNG 只支持 8/16bit，32bit 文档按 8bit 传输（要传 16bit 必须先降位深）。
function docPngBits(doc) {
    const b = doc.bitsPerChannel;
    return (b === 'bitDepth16' || b === 16) ? 16 : 8;
}

// 改文档位深必须用 setter（内部走 convertBitDepth）；Documents.add 没有位深选项，
// 传 bitsPerChannel 会被静默忽略、新文档取 PS 默认位深。
function setDocBits(doc, bits) {
    const want = bits === 16 ? 'bitDepth16' : 'bitDepth8';
    if (doc.bitsPerChannel !== want) doc.bitsPerChannel = want;
}

// 导出链路的内容契约：源图有内容、导出却得到纯色图 ⇒ 合并拷贝/粘贴失效。
// （8256×5504 的 16bit 文档实测过这种"纯白上传"，模型对空白图推理的结果毫无意义。
//  直接中止，不静默产出垃圾深度；源图本身就是纯色时不判定为失败。）
function assertExportPreserved(srcDoc, tempDoc) {
    const flat = (h) => {
        let nonzero = 0;
        for (let i = 0; i < h.length; i++) if (h[i] > 0) nonzero++;
        return nonzero <= 1;
    };
    let srcHist, tmpHist;
    try {
        srcHist = srcDoc.histogram;
        tmpHist = tempDoc.histogram;
    } catch (e) {
        return;   // 拿不到直方图就不阻断（不把契约变成新的失败源）
    }
    if (!srcHist || !tmpHist || !srcHist.length || !tmpHist.length) return;
    if (flat(srcHist)) return;
    if (flat(tmpHist)) {
        throw new Error('大图导出得到纯色图（源图有内容）——合并拷贝/粘贴链路失效，'
            + '已中止以免产出无意义的深度。请检查导出顺序与剪贴板状态。');
    }
}

// ========== 辅助函数 ==========
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ---------- mix 性能打点 ----------
// log() 由 main.js 以顶层函数声明定义（经典脚本全局作用域），运行时必然已加载；
// 本文件也被频谱插件共享，防御性回退到 console。
function _perfNow() {
    return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
}
function mixStepLog(msg) {
    if (typeof log === 'function') log(msg);
    else console.log(msg);
}
function mixStep(msg, t0) {
    mixStepLog(`  ⏱ ${msg}: ${(_perfNow() - t0).toFixed(0)}ms`);
}

function uint8ToBase64(uint8) {
    // UXP 基于 Chromium，btoa 可用（importChannelStable 已在用 atob）。
    // 分块转换避免超大字节数组展开爆栈；块大小必须为 3 的倍数，
    // 否则各块独立编码时跨块边界 base64 对齐错位（服务器解码失败）。
    const CHUNK = 0x6000; // 24576 = 3 的倍数
    let b64 = '';
    for (let i = 0; i < uint8.length; i += CHUNK) {
        b64 += btoa(String.fromCharCode.apply(null, uint8.subarray(i, i + CHUNK)));
    }
    return b64;
}

// ========== 服务器相关 ==========
async function isServerRunning() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1000);
    try {
        const res = await fetch(PING_URL, { signal: controller.signal, mode: 'cors', credentials: 'omit' });
        clearTimeout(timeoutId);
        if (!res.ok) return false;
        const data = await res.json();
        if (data.scheduler !== 'ok' || data.depth_server !== true) return false;
        if (String(data.version) !== SERVER_EXPECTED_VERSION) return false;
        return true;
    } catch (e) {
        clearTimeout(timeoutId);
        return false;
    }
}

async function launchServer() {
    const pluginFolder = await localFileSystem.getPluginFolder();
    let jsxFile = null;
    try {
        jsxFile = await pluginFolder.getEntry('启动深度服务.jsx');
    } catch (e) {
        const entries = await pluginFolder.getEntries();
        for (const entry of entries) {
            if (entry.name.includes('启动深度服务') || entry.name.includes('start_server')) {
                jsxFile = entry;
                break;
            }
        }
    }
    if (!jsxFile) {
        throw new Error('未找到 启动深度服务.jsx 文件，请确认它位于插件目录。');
    }
    const token = await localFileSystem.createSessionToken(jsxFile);
    await core.executeAsModal(async () => {
        await batchPlay([
            {
                _obj: 'AdobeScriptAutomation Scripts',
                javaScript: {
                    _kind: 'local',
                    _path: token
                },
                javaScriptMessage: 'JSM',
                _options: {
                    dialogOptions: 'dontDisplay'
                }
            }
        ], {});
    }, { commandName: '启动深度服务器', interactive: false });

    // 长轮询 90s：覆盖 torch 冷启动（46MB exe + 5GB _internal）与杀软首次扫描的耗时。
    // JSX 侧只做 15s 快速等待，超时后由这里继续探测；服务一旦就绪即返回并自动继续生成。
    for (let i = 0; i < 90; i++) {
        await sleep(1000);
        if (await isServerRunning()) return true;
        if ((i + 1) % 5 === 0) console.log('等待服务器启动... ' + (i + 1) + 's');
    }
    throw new Error('服务器启动超时（90 秒），depth_server.exe 未能在等待窗口内就绪。\n\n' +
        '请按顺序排查：\n' +
        '1. 打开插件目录下的 depth_server.log，查看最后的启动/报错信息；\n' +
        '2. 手动双击插件目录下的 depth_server.exe，确认窗口能正常打开并保持运行；\n' +
        '3. 手动能启动而自动启动失败时，多为安全软件拦截未签名 exe（右键 exe → 属性 → 解除锁定），' +
        '或首次运行的 SmartScreen 提示未处理；\n' +
        '4. 手动启动成功后，直接再次点击"生成"即可继续，无需重启 Photoshop。');
}

async function ensureServer() {
    if (await isServerRunning()) return true;
    await launchServer();
    return true;
}

// ========== 图像导出 ==========
// 直存导出：doc.saveAs.png 直接保存当前文档到临时文件（不创建临时文档、
// 不弹出新文件）。用于频谱预览/生成（避免 getResizedImageData 的 duplicate 弹窗）。
// saveAs 需在 modal 内执行（同 getResizedImageData 的做法）。
async function exportDocPng(doc, tempFolder) {
    return await core.executeAsModal(async () => {
        const tempFile = await tempFolder.createFile('exp_' + Date.now() + '.png', { overwrite: true });
        await doc.saveAs.png(tempFile, { compression: 6 }, true);
        const fileBuffer = await tempFile.read({ format: storage.formats.binary });
        try { await tempFile.delete(); } catch (e) {}
        return new Uint8Array(fileBuffer);
    }, { commandName: 'exportDocPng' });
}

async function getResizedImageData(doc, maxTransmitDim = MAX_TRANSMIT_DIM) {
    let result = { base64: '', origWidth: 0, origHeight: 0, scale: 1.0 };
    await core.executeAsModal(async () => {
        let t0 = _perfNow();
        const origWidth = Number(doc.width);
        const origHeight = Number(doc.height);
        const maxDim = Math.max(origWidth, origHeight);
        let scale = 1.0;
        if (maxDim > maxTransmitDim) {
            scale = maxTransmitDim / maxDim;
        }
        const newWidth = Math.round(origWidth * scale);
        const newHeight = Math.round(origHeight * scale);

        if (scale >= 1.0) {
            const tempFolder = await storage.localFileSystem.getTemporaryFolder();
            const fileName = 'input_' + Date.now() + '.png';
            const tempFile = await tempFolder.createFile(fileName, { overwrite: true });
            await doc.saveAs.png(tempFile, { compression: 6 }, true);
            const fileBuffer = await tempFile.read({ format: storage.formats.binary });
            try { await tempFile.delete(); } catch(e) {}
            const pngBytes = new Uint8Array(fileBuffer);
            result = { base64: uint8ToBase64(pngBytes), origWidth, origHeight, scale: 1.0,
                       bits: pngBytes[24] };   // PNG IHDR 的位深字节（偏移 8+4+4+8）
            mixStepLog(`  ⏱ export 直存: ${(_perfNow() - t0).toFixed(0)}ms`);
            return;
        }

        // 大图：用「仅合并图层」的副本导出，再缩放。
        // 历史沿革（2026-09-20 定论）：
        //   早期 = doc.duplicate() 复制整个分层文档，8K 下创建/close 慢；
        //   中期 = copyMerged → 新建空白文档 → paste ；实测在 8256×5504 的 16bit 文档上
        //          导出**纯白**图（粘贴内容没落到临时文档，模型收到空白图、深度无意义）；
        //          把顺序改成"先建文档 → 回源文档拷贝 → 切回粘贴"**仍然纯白**，
        //          故根因在剪贴板/粘贴这一步，与顺序或位深无关。
        //   现在 = duplicate(name, mergeLayersOnly=true)：只复制合并像素、不复制图层栈，
        //          45MP/16bit 实测 103ms，尺寸/位深/内容与合成一致，且完全不依赖剪贴板。
        const tempFolder = await storage.localFileSystem.getTemporaryFolder();
        const tempDoc = await doc.duplicate('depthpro_export', true);
        mixStepLog(`  ⏱ export duplicate(merged): ${(_perfNow() - t0).toFixed(0)}ms`);
        try {
            // 副本按定义与源同尺寸同位深；仍显式对齐一次，并做内容契约断言
            setDocBits(tempDoc, docPngBits(doc));
            assertExportPreserved(doc, tempDoc);
            await tempDoc.resizeImage(newWidth, newHeight, undefined, ps.constants.ResampleMethod.BICUBIC);
            const tempFile = await tempFolder.createFile('input_' + Date.now() + '.png', { overwrite: true });
            await tempDoc.saveAs.png(tempFile, { compression: 6 }, true);
            const fileBuffer = await tempFile.read({ format: storage.formats.binary });
            try { await tempFile.delete(); } catch (e) {}
            mixStepLog(`  ⏱ export resize+save: ${(_perfNow() - t0).toFixed(0)}ms`);
            const pngBytes = new Uint8Array(fileBuffer);
            result = { base64: uint8ToBase64(pngBytes), origWidth, origHeight, scale,
                       bits: pngBytes[24] };   // PNG IHDR 的位深字节（偏移 8+4+4+8）
        } finally {
            // 任何一步失败都必须关掉副本，不能把临时文档留在用户的工作区里
            try { await tempDoc.close(ps.constants.SaveOptions.DONOTSAVECHANGES); } catch (e) {}
        }
    }, { commandName: 'exportResizedImage' });
    return result;
}

// ========== 通道导入（PNG 字节 → 通道） ==========
// 灰度 PNG 打开即单通道灰度文档：位深对齐后如需放大先在灰度模式 resize
// （单通道数据量为 RGB 的 1/3），再跨文档复制 gray 通道为目标 alpha。
// 旧实现先转 RGB 再复制 red，resize 数据量 3 倍且多一次模式转换。
// 兼容性：部分来源（如服务器 PIL 编码的 16bit 灰度 PNG）打开后可能非标准
// 灰度通道枚举，gray 复制失败时自动回退"转 RGB 复制 red"（任意模式可用）。
async function createChannelFromPngBytes(doc, channelName, pngBytes, tempFolder, resizeTo) {
    let t0 = _perfNow();
    const tempFile = await tempFolder.createFile('dep_' + channelName + '.png', { overwrite: true });
    await tempFile.write(pngBytes, { format: storage.formats.binary });
    let tempDoc = null;
    try {
        tempDoc = await app.open(tempFile);
        mixStep(`import[${channelName}] open (mode=${tempDoc.mode} bits=${tempDoc.bitsPerChannel} chans=${tempDoc.channels.length})`, t0);
        // 对齐到目标文档位深。必须用位深 setter：changeMode 只管颜色模式，且 UXP 的
        // ChangeMode 里没有 EIGHT/SIXTEEN/THIRTYTWO，旧写法是永不执行的死代码。
        setDocBits(tempDoc, docPngBits(doc));
        mixStep(`import[${channelName}] bits`, t0);
        if (resizeTo) {
            const tw = Number(tempDoc.width), th = Number(tempDoc.height);
            const dw = Number(doc.width), dh = Number(doc.height);
            if (tw !== dw || th !== dh) {
                await tempDoc.resizeImage(dw, dh, undefined, ps.constants.ResampleMethod.BICUBIC);
            }
        }
        mixStep(`import[${channelName}] resize`, t0);
        const targetDocId = doc.id;
        try {
            if (channelExists(doc, channelName)) doc.channels.getByName(channelName).remove();
        } catch (e) {}

        // 复制通道：灰度直导优先，失败/非灰度回退"转 RGB 复制 red"。
        // 实测：batchPlay duplicate 灰度合成通道跨文档会静默失败（返回但不执行、
        // 不抛错），必须以"目标文档是否真实出现通道"为准做验证，不能只看是否抛错。
        mixStepLog(`import[${channelName}] 复制前 mode='${tempDoc.mode}'`);
        let success = false;
        if (tempDoc.mode === ps.constants.DocumentMode.GRAYSCALE) {
            try {
                await batchPlay([
                    {
                        _obj: "duplicate",
                        _target: { _ref: "channel", _enum: "channel", _value: "gray" },
                        to: { _ref: "document", _id: targetDocId },
                        name: channelName
                    }
                ], { synchronousExecution: true });
            } catch (e) {
                mixStepLog(`import[${channelName}] gray 复制抛错，回退 RGB: ${e.message || e}`);
            }
            if (channelExists(doc, channelName)) {
                success = true;
                mixStepLog(`import[${channelName}] gray 复制成功（通道已出现）`);
            } else {
                mixStepLog(`import[${channelName}] gray 复制后通道未出现，回退 RGB/red`);
            }
        }
        if (!success) {
            if (tempDoc.mode !== ps.constants.DocumentMode.RGB) {
                await tempDoc.changeMode(ps.constants.ChangeMode.RGB);
            }
            try {
                await batchPlay([
                    {
                        _obj: "duplicate",
                        _target: { _ref: "channel", _enum: "channel", _value: "red" },
                        to: { _ref: "document", _id: targetDocId },
                        name: channelName
                    }
                ], { synchronousExecution: true });
            } catch (e) {
                throw new Error('通道复制失败: ' + (e.message || e));
            }
            if (!channelExists(doc, channelName)) {
                throw new Error(`通道复制失败（red 复制后仍未出现 "${channelName}"）`);
            }
            success = true;
            mixStepLog(`import[${channelName}] red 复制成功（通道已出现）`);
        }
        mixStep(`import[${channelName}] dupChannel`, t0);
        return success;
    } finally {
        // 兜底清理：任何一步抛错都要关掉临时文档并删临时 PNG（防 PS 残留文档标签）。
        // 正常路径下 finally 里的 close/delete 会因对象已关闭/已删除而抛错，被吞掉即可。
        if (tempDoc) {
            try { await tempDoc.close(ps.constants.SaveOptions.DONOTSAVECHANGES); }
            catch (e) { mixStepLog(`import[${channelName}] 兜底 close: ${e.message || e}`); }
        }
        try { await tempFile.delete(); }
        catch (e) { mixStepLog(`import[${channelName}] 兜底 delete: ${e.message || e}`); }
    }
}

// base64 → Uint8Array：分块解码避免超大字符串单次 atob 失败/卡顿（8K 深度 PNG 的
// base64 可达数十 MB）。块字符数须为 4 的倍数（4 字符 = 3 字节），跨块对齐才正确。
function base64ToUint8(b64) {
    const CHUNK = 0x10000; // 65536 字符 = 49152 字节，4 的倍数
    const out = new Uint8Array(Math.floor(b64.length / 4) * 3);
    let o = 0;
    for (let i = 0; i < b64.length; i += CHUNK) {
        const seg = atob(b64.slice(i, i + CHUNK));
        for (let j = 0; j < seg.length; j++) out[o++] = seg.charCodeAt(j);
    }
    return out.subarray(0, o);
}

async function importChannelStable(doc, channelName, base64Data, tempFolder, resizeTo) {
    const pngBytes = base64ToUint8(base64Data);
    return createChannelFromPngBytes(doc, channelName, pngBytes, tempFolder, resizeTo);
}

// ========== 通道提取（通道 → PNG 灰度字节） ==========
// 快速路径：全程不复制整个文档。实测（8K 16-bit、16GB 内存机）旧实现的
// doc.duplicate/close 生命周期会触发数分钟的历史清理与暂存盘整理（PS 纯 CPU），
// 单次混合卡 10 分钟以上。新实现：
//   命名 alpha 通道 → batchPlay "duplicate 到新建文档"（单通道新文档，原生拷贝）
//   分量通道 R/G/B  → 先在源文档内复制为临时 alpha（原生单通道拷贝），用后即删
//   L / Lum Lock    → 无同名 alpha 时用 batchPlay 计算（add + scale 2 = 精确灰色）
//                     把"合并的灰色"复制为临时 alpha，再同上
// 新建文档只在单通道上做 resize/存储，close 也只释放单通道数据。
// L / Lum Lock：无同名 alpha 通道时按"明度"提取（PS 明度 = 合并灰色的通道值）。
const LUMINOSITY_FALLBACK = ['L', 'Lum Lock'];

// UXP 的 channels.getByName() 找不到时行为不统一（返回 undefined 或抛异常），
// 统一按返回值真值判断。
function channelExists(doc, name) {
    try { return !!doc.channels.getByName(name); } catch (e) { return false; }
}

// 明度提取（L / Lum Lock 无同名 alpha 时）：
// duplicate(name, mergeLayersOnly=true) 取合并像素副本 → 转灰度 → resize → PNG。
// 副本只有合并像素（无源文档图层/历史），生命周期成本远低于整文档复制。
// 注意：此前的 copyMerged → 新建 → paste 链路在 UXP 侧会得到纯色图（只在 ExtendScript
// 侧验证过），故已整体弃用；见 getResizedImageData 处注释与 assertExportPreserved 断言。
// （这条链路此前只在 ExtendScript(JSX) 侧验证过"可行"，UXP 运行时未验证——两者是不同
//  运行时。2026-09-20 实测 UXP 侧"先拷贝、再新建、再粘贴"会导出**纯白**图，模型收到
//  空白图、深度无意义；见 getResizedImageData 处的顺序修正与内容断言。）
async function extractLuminosityPng(doc, tempFolder, maxDim) {
    let t0 = _perfNow();
    // 与 getResizedImageData 同策略：「仅合并图层」的副本，不碰剪贴板
    // （copyMerged → 新建 → paste 在 UXP 侧会得到纯色图，详见该处注释）
    const tempDoc = await doc.duplicate('depthpro_lum', true);
    try {
        setDocBits(tempDoc, docPngBits(doc));
        assertExportPreserved(doc, tempDoc);
        if (tempDoc.mode !== ps.constants.DocumentMode.GRAYSCALE) {
            await tempDoc.changeMode(ps.constants.ChangeMode.GRAYSCALE);
        }
        mixStep('lum duplicate+gray', t0);

        const maxEdge = Math.max(Number(tempDoc.width), Number(tempDoc.height));
        if (maxDim && maxEdge > maxDim) {
            const scale = maxDim / maxEdge;
            await tempDoc.resizeImage(
                Math.max(1, Math.round(Number(tempDoc.width) * scale)),
                Math.max(1, Math.round(Number(tempDoc.height) * scale)),
                undefined, ps.constants.ResampleMethod.BICUBIC);
            mixStep('lum resize', t0);
        }
        const tempFile = await tempFolder.createFile('ext_' + Date.now() + '.png', { overwrite: true });
        await tempDoc.saveAs.png(tempFile, { compression: 6 }, true);
        mixStep('lum savePng', t0);
        const fileBuffer = await tempFile.read({ format: storage.formats.binary });
        try { await tempFile.delete(); } catch (e) {}
        mixStep('lum readFile', t0);
        return new Uint8Array(fileBuffer);
    } finally {
        // 失败路径也必须关掉副本（旧写法把 close 放在末尾，断言一抛错就留下孤儿文档）
        try { await tempDoc.close(ps.constants.SaveOptions.DONOTSAVECHANGES); } catch (e) {}
    }
}

// 通道 → 灰度 PNG 提取（纯 UXP，无 ExtendScript 桥）
// 流程：新建同尺寸同位深灰度临时文档 → batchPlay duplicate 把源通道复制为临时
// alpha（UXP modal 内可用）→ 新建像素图层 + Layer.applyImage 把 alpha 应用为图层
// 内容（Apply Image 官方 DOM API）→ flatten → resize → saveAs 灰度 PNG → 读回。
// 不使用 ExtendScript 桥：modal scope 内 ExtendScript 的 slct/复制通道(Dplc)等命令
// 会被 PS 以"命令当前不可用"拒绝；本流程全部为 UXP 官方 API，且 applyImage/
// createPixelLayer 的文档示例即要求在 executeAsModal 内调用。
// 注意：batchPlay duplicate 跨文档复制"非激活文档"的通道会静默失败，必须先
// select 源文档到前台（createChannelFromPngBytes 成功是因为其源文档恰好激活）。
async function extractChannelPngUxp(doc, channelName, tempFolder, maxDim) {
    return await core.executeAsModal(async () => {
        let t0 = _perfNow();
        const bits = docPngBits(doc);
        const tempDoc = await app.documents.add({
            width: Number(doc.width),
            height: Number(doc.height),
            resolution: 72,
            mode: ps.constants.DocumentMode.GRAYSCALE
        });
        // 位深必须在跨文档复制通道之前设好，否则 16bit 通道数据在这里就被截断
        setDocBits(tempDoc, bits);
        mixStep('extractUxp newdoc', t0);
        try {
            // 源文档切到前台，再执行跨文档通道复制
            await batchPlay([
                { _obj: 'select', _target: [{ _ref: 'document', _id: doc.id }] }
            ], { synchronousExecution: true });
            const dupRes = await batchPlay([{
                _obj: 'duplicate',
                _target: { _ref: 'channel', _name: channelName },
                to: { _ref: 'document', _id: tempDoc.id },
                name: 'mix_src'
            }], { synchronousExecution: true });
            mixStepLog('extractUxp dup result: ' + JSON.stringify(dupRes || null));
            // 切回临时文档（后续 applyImage 等操作对象为 tempDoc，激活态更稳）
            await batchPlay([
                { _obj: 'select', _target: [{ _ref: 'document', _id: tempDoc.id }] }
            ], { synchronousExecution: true });
            mixStep('extractUxp dupChannel', t0);
            const srcChan = tempDoc.channels.getByName('mix_src');
            if (!srcChan) throw new Error(`通道复制失败: ${channelName}`);
            const lay = await tempDoc.createPixelLayer();
            await lay.applyImage({
                source: {
                    document: tempDoc,
                    layer: ps.constants.ApplyImageLayer.MERGED,
                    channel: srcChan
                }
            });
            mixStep('extractUxp applyImage', t0);
            await tempDoc.flatten();
            const maxEdge = Math.max(Number(tempDoc.width), Number(tempDoc.height));
            if (maxDim && maxEdge > maxDim) {
                const scale = maxDim / maxEdge;
                await tempDoc.resizeImage(
                    Math.max(1, Math.round(Number(tempDoc.width) * scale)),
                    Math.max(1, Math.round(Number(tempDoc.height) * scale)),
                    undefined, ps.constants.ResampleMethod.BICUBIC);
            }
            mixStep('extractUxp resize', t0);
            const tempFile = await tempFolder.createFile('ext_' + Date.now() + '.png', { overwrite: true });
            await tempDoc.saveAs.png(tempFile, { compression: 6 }, true);
            mixStep('extractUxp savePng', t0);
            const fileBuffer = await tempFile.read({ format: storage.formats.binary });
            try { await tempFile.delete(); } catch (e) {}
            return new Uint8Array(fileBuffer);
        } finally {
            await tempDoc.close(ps.constants.SaveOptions.DONOTSAVECHANGES);
        }
    }, { commandName: 'extractChannelPng', interactive: false });
}

async function extractChannelPng(doc, channelName, tempFolder, maxDim) {
    // 明度回退（L / Lum Lock 无同名 alpha）：copyMerged 需 modal 内执行
    if (LUMINOSITY_FALLBACK.includes(channelName) && !channelExists(doc, channelName)) {
        return await core.executeAsModal(
            async () => extractLuminosityPng(doc, tempFolder, maxDim),
            { commandName: 'extractLum' });
    }
    // 命名 alpha：纯 UXP 提取（extractChannelPngUxp 内部包 executeAsModal；
    // 通道复制/applyImage/saveAs 全为 UXP 官方 API，不再用 ExtendScript 桥）。
    // 与 L 回退分支无嵌套问题：L 回退在上面已 return，不会同时进入 modal。
    if (!channelExists(doc, channelName)) {
        throw new Error(`通道 "${channelName}" 不存在`);
    }
    mixStepLog(`extract[${channelName}] UXP 提取中`);
    return await extractChannelPngUxp(doc, channelName, tempFolder, maxDim);
}
