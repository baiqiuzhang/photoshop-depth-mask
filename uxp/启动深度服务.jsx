// ============================================================
// 启动深度服务.jsx  (版本感知统一启动器)
// - 多层自动定位 exe：脚本目录/父目录/任意子文件夹（兼容 onedir 布局与任意文件夹名）
// - 版本感知探测 /ping：识别残留旧版服务，自动 taskkill 清理后重启（解决 route not found）
// - 直接 start 拉起 exe（环境与手动双击一致），失败时回退临时 bat
// - 明确的中文错误提示
// ============================================================

var jsxFile = File($.fileName);
var scriptFolder = jsxFile.parent;
var isWin = ($.os.indexOf("Windows") > -1);
var exeName = isWin ? "depth_server.exe" : "depth_server";
var EXPECTED_VERSION = "7";   // 与 depth_server.py 的 SERVER_VERSION 保持一致

// ---------- 1. 查找可执行文件（多层搜索） ----------
function findExe() {
    var candidates = [];

    // ① 脚本所在目录
    candidates.push(scriptFolder + "/" + exeName);
    // ② 父目录
    candidates.push(scriptFolder.parent + "/" + exeName);

    // ③ 脚本目录下的一级子文件夹（覆盖 onedir 子目录、任意文件夹名）
    //    注意：只扫描脚本自身目录，避免在 Plug-ins 父目录下拾取兄弟插件的 exe
    var folder = Folder(scriptFolder);
    if (folder.exists) {
        var entries = folder.getFiles();
        for (var i = 0; i < entries.length; i++) {
            if (entries[i] instanceof Folder) {
                candidates.push(entries[i] + "/" + exeName);
            }
        }
    }

    // 去重并检查存在性
    var seen = {};
    for (var c = 0; c < candidates.length; c++) {
        var path = candidates[c];
        if (seen[path]) continue;
        seen[path] = true;
        if (File(path).exists) return path;
    }
    return null;
}

// ---------- 2. 版本感知的服务探测 ----------
// 返回: "ok" = 本版本服务已运行; "stale" = 旧版/异常服务占用端口; "offline" = 无服务
function probeServer() {
    var conn;
    try {
        conn = new Socket();
        if (!conn.open("127.0.0.1:8766", "UTF-8")) return "offline";
        try { conn.timeout = 3; } catch (e) {}   // 防止异常服务不关连接导致挂起
        conn.write("GET /ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n");
        var buf = "";
        var guard = 0;
        while (true) {
            var chunk = conn.read(4096);
            if (chunk === null || chunk === "") break;
            buf += chunk;
            guard++;
            if (guard > 100) break;
        }
        conn.close();
        if (buf.indexOf("200") < 0) return "offline";
        var m = buf.match(/"version"\s*:\s*"?([^",}\s]+)/);
        var ver = m ? m[1] : "";
        if (ver === EXPECTED_VERSION) return "ok";
        return "stale";
    } catch (e) {
        try { if (conn) conn.close(); } catch (e2) {}
        return "offline";
    }
}

// ---------- 3. 主流程 ----------
var exePath = findExe();
if (!exePath) {
    var msg = "❌ 未找到 " + exeName + "！\n\n" +
              "请确保可执行文件位于插件目录（或其子文件夹）下。\n" +
              "当前脚本位置：" + scriptFolder.fsName + "\n\n" +
              "可能原因：\n" +
              "1. 杀毒软件误删了 exe 文件\n" +
              "2. 插件包解压不完整\n" +
              "3. 文件被移动或重命名\n\n" +
              "请检查插件目录，或将 exe 文件复制到此目录。";
    alert(msg);
    throw new Error("exe 文件未找到");
}

var state = probeServer();

if (state === "ok") {
    // 本版本服务已运行，无需操作
} else {
    if (state === "stale") {
        // 端口被旧版服务占用 → 强制结束所有 depth_server 进程后重启
        try {
            app.system("taskkill /F /IM depth_server.exe");
        } catch (e) {}
        $.sleep(1500);
        if (probeServer() !== "offline") {
            alert("⚠️ 8766 端口被其他程序占用！\n\n" +
                  "检测到端口上有服务响应，但清理 depth_server.exe 后仍未释放。\n" +
                  "请检查是否有其他程序占用 8766 端口，或重启 Photoshop 后重试。");
            throw new Error("port occupied");
        }
    }

    // ---------- 启动服务（直接 start 拉起，环境与手动双击一致） ----------
    // 说明：旧实现写临时 bat 再由 cmd 执行，cmd 对 bat 的引号解析与 errorlevel 残留
    // 在部分机器上会返回非 0 退出码或启动失败，且清空 PATH 与手动启动环境不一致。
    // 直接 start 无临时文件、无编码与引号解析差异；启动成败由下方探测与 UXP 侧 90s 轮询判定。
    var exeNative = File(exePath).fsName;   // 系统原生路径（含中文）
    var workDir = File(exePath).parent.fsName;
    var rc = app.system('start "" /D "' + workDir + '" "' + exeNative + '"');
    // app.system 在 Windows 返回数值退出码（0=成功），个别环境返回布尔；仅数值非 0 视为失败
    var directFailed = (typeof rc === 'number' && rc !== 0);

    // ---------- 等待服务就绪（快速路径 10 秒，超时后由 UXP 侧继续长轮询） ----------
    var wait = 0;
    while (wait < 10) {
        if (probeServer() === "ok") break;
        $.sleep(1000);
        wait++;
    }

    if (wait >= 10 && directFailed) {
        // 回退：临时 bat（exit /b 0 固定退出码，避免 cmd errorlevel 残留导致误判）
        var ts = (new Date()).getTime();
        var tempFolder = Folder.temp;
        var batFile = new File(tempFolder + "/start_depth_server_" + ts + ".bat");
        batFile.encoding = "UTF-8";         // 配合 chcp 65001，任意系统代码页下可解析非 ASCII 路径
        batFile.open("w");
        batFile.writeln('@echo off');
        batFile.writeln('chcp 65001 >nul');
        batFile.writeln('cd /d "' + workDir + '"');
        batFile.writeln('set PATH=' + workDir + ';%PATH%');
        batFile.writeln('set PYTHONPATH=');
        batFile.writeln('set PYTHONHOME=');
        batFile.writeln('set PYTHONNOUSERSITE=1');
        batFile.writeln('set PYTHONSAFEPATH=1');
        batFile.writeln('start "" "' + exeNative + '"');
        batFile.writeln('exit /b 0');
        batFile.close();
        app.system('"' + batFile.fsName + '"');
        var wait2 = 0;
        while (wait2 < 15) {
            if (probeServer() === "ok") break;
            $.sleep(1000);
            wait2++;
        }
    }
    // 仍不就绪：交由 UXP 侧 90 秒长轮询兜底，超时后给出排查指引
}
