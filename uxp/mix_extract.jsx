// mix_extract.jsx — 把命名通道内容导出为灰度 PNG（JSX 桥）
// 由 UXP 侧 jsxExtractPng 通过 AdobeScriptAutomation Scripts 事件在 modal scope 内执行。
// 注意：本桥不切换通道视图——modal 内 slct 对 alpha 通道 8800"选择不可用"，
// 改为：新建同尺寸同位深灰度文档 → duplicate 源通道为临时 alpha →
// applyImage 把 alpha 应用为合成 → 缩放 → 存灰度 PNG。
// 参数经同目录 mix_extract_args.txt 传入：第1行源文档名，第2行通道名，第3行输出 PNG 路径，第4行长边上限(可空)
var argsPath = (new File($.fileName)).parent.fsName + '/mix_extract_args.txt';
var argsFile = new File(argsPath);
if (!argsFile.exists) { throw new Error('mix_extract_args.txt not found'); }
argsFile.open('r');
var docName = argsFile.readln();   // 第1行：源文档名
var chanSpec = argsFile.readln();  // 第2行：通道名
var outPng = argsFile.readln();    // 第3行：输出 PNG 路径
var maxDimS = argsFile.readln();   // 第4行：长边上限
argsFile.close();
var maxDim = parseInt(maxDimS, 10) || 0;

// 兼容 'name:xxx' 前缀（防御旧参数格式）
var chanName = chanSpec;
if (chanName.indexOf('name:') === 0) { chanName = chanName.substring(5); }

// 按文档名定位源文档并显式激活（UXP modal 内活动文档可能残留临时文档，必须显式切回）
var doc = null;
for (var di = 0; di < app.documents.length; di++) {
    if (app.documents[di].name === docName) { doc = app.documents[di]; break; }
}
if (!doc) { throw new Error('doc not found: ' + docName); }
app.activeDocument = doc;

// 新建同尺寸同位深灰度文档（单通道数据，复制/缩放/存储成本最低）
var nd = app.documents.add(Number(doc.width), Number(doc.height), 72, 'mix_ext',
    NewDocumentMode.GRAYSCALE, DocumentFill.WHITE, 1, doc.bitsPerChannel);

try {
    // 1) 把源通道复制为新文档的临时 alpha（不涉及 slct；目标文档用 id 精确指定）
    var dupDesc = new ActionDescriptor();
    var dupRef = new ActionReference();
    dupRef.putName(charIDToTypeID('Chnl'), chanName);
    dupDesc.putReference(charIDToTypeID('null'), dupRef);
    var toRef = new ActionReference();
    toRef.putIdentifier(charIDToTypeID('Dcmn'), nd.id);
    dupDesc.putReference(charIDToTypeID('T   '), toRef);
    dupDesc.putString(charIDToTypeID('Nm  '), 'mix_src');
    executeAction(charIDToTypeID('Dplc'), dupDesc, DialogModes.NO);

    // 2) applyImage：把 alpha 内容应用为合成（正常混合 100%，合成=alpha 值）
    var alphaChan = null;
    try { alphaChan = nd.channels.getByName('mix_src'); } catch (e) {}
    if (!alphaChan) { throw new Error('duplicate channel failed: ' + chanName); }
    nd.applyImage(alphaChan, BlendMode.NORMAL, 100);
    alphaChan.remove();

    // 3) 缩放
    if (maxDim > 0) {
        var maxEdge = Math.max(Number(nd.width), Number(nd.height));
        if (maxEdge > maxDim) {
            var sc = maxDim / maxEdge;
            nd.resizeImage(Math.max(1, Math.round(Number(nd.width) * sc)),
                Math.max(1, Math.round(Number(nd.height) * sc)), undefined, ResampleMethod.BICUBIC);
        }
    }

    // 4) 存灰度 PNG 并关闭
    nd.saveAs(new File(outPng), new PNGSaveOptions(), true, Extension.LOWERCASE);
    nd.close(SaveOptions.DONOTSAVECHANGES);
} catch (e) {
    // 失败时清理临时文档，避免残留；源文档仍恢复激活
    try { nd.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
    app.activeDocument = doc;
    throw e;
}
app.activeDocument = doc;
