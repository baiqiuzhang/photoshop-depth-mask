#!/usr/bin/env node
/* eslint-env node */
/* 位深链路回归测试（Node，不依赖 Python/UXP）
 *
 * 起因：UXP 的 Document.bitsPerChannel 返回字符串枚举 'bitDepth8'/'bitDepth16'/
 * 'bitDepth32'，而插件曾用 `Number(doc.bitsPerChannel) === 16` 判断 → 恒为 false
 * → 16bit 文档在「大图上传 / 明度回退 / 通道提取 / AlphaMixer 输出」四条路径上
 * 全部被降成 8bit，且回写侧的位深对齐是永不执行的死代码。
 *
 * 本测试做三件事：
 *   1) 从 common.js 抽取出真实的 docPngBits / setDocBits 源码来跑（不是复制一份），
 *      覆盖字符串枚举、数字形态、已同位深时的空操作；
 *   2) 静态守卫：旧写法一旦复活就失败（含「每个 documents.add 后必须有 setDocBits」）；
 *   3) 用真实 PNG 字节验证上传位深诊断用的 IHDR 偏移（pngBytes[24]）确实等于位深。
 *
 * 运行：node test_bitdepth.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const PLUGIN = 'D:/depth_pro_photoshop_jsx/0.2.1/com.zk21.depthpro';
const PngCodec = require(path.join(PLUGIN, 'png_codec.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
    if (cond) { pass++; console.log('  ✅ ' + name); }
    else { fail++; console.log('  ❌ ' + name + (detail ? '  ' + detail : '')); }
}

const commonSrc = fs.readFileSync(path.join(PLUGIN, 'common.js'), 'utf8');
const mainSrc = fs.readFileSync(path.join(PLUGIN, 'main.js'), 'utf8');

// 按大括号配平截出顶层函数源码，再用 new Function 求值，保证测的是磁盘上的真实实现
function extractFn(src, name) {
    const start = src.indexOf('function ' + name + '(');
    if (start < 0) return null;
    const i = src.indexOf('{', start);
    let depth = 0;
    for (let j = i; j < src.length; j++) {
        if (src[j] === '{') depth++;
        else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
    }
    return null;
}
function loadFn(src, name) {
    const body = extractFn(src, name);
    if (!body) throw new Error('未找到函数 ' + name + '（源码结构变了？）');
    return new Function(body + '\nreturn ' + name + ';')();
}

// ============ 1. docPngBits / setDocBits 行为 ============
console.log('== 位深判断与设置 ==');
let docPngBits, setDocBits;
try {
    docPngBits = loadFn(commonSrc, 'docPngBits');
    setDocBits = loadFn(commonSrc, 'setDocBits');
    check('common.js 中存在 docPngBits / setDocBits', true);
} catch (e) {
    check('common.js 中存在 docPngBits / setDocBits', false, e.message);
}

function fakeDoc(init) {
    const d = { _bits: init, sets: [] };
    Object.defineProperty(d, 'bitsPerChannel', {
        get() { return this._bits; },
        set(v) { this._bits = v; this.sets.push(v); },
    });
    return d;
}

if (docPngBits) {
    check("bitsPerChannel='bitDepth16' → 16", docPngBits(fakeDoc('bitDepth16')) === 16);
    check("bitsPerChannel='bitDepth8'  → 8", docPngBits(fakeDoc('bitDepth8')) === 8);
    check("bitsPerChannel='bitDepth32' → 8（PNG 无 32bit）", docPngBits(fakeDoc('bitDepth32')) === 8);
    check('数字形态 16 → 16（兼容）', docPngBits(fakeDoc(16)) === 16);
    check('旧写法必须失效：Number(\'bitDepth16\') !== 16', Number('bitDepth16') === 16 ? false : true);
}

if (setDocBits) {
    let d = fakeDoc('bitDepth8');
    setDocBits(d, 16);
    check("setDocBits(16) 写入 'bitDepth16'", d.bitsPerChannel === 'bitDepth16', d.bitsPerChannel);
    check('setDocBits(16) 恰好写 1 次', d.sets.length === 1, JSON.stringify(d.sets));

    d = fakeDoc('bitDepth16');
    setDocBits(d, 16);
    check('已同位深时空操作（不写）', d.sets.length === 0, JSON.stringify(d.sets));

    d = fakeDoc('bitDepth16');
    setDocBits(d, 8);
    check("setDocBits(8) 写入 'bitDepth8'", d.bitsPerChannel === 'bitDepth8' && d.sets.length === 1, d.bitsPerChannel);

    d = fakeDoc('bitDepth32');
    setDocBits(d, 8);
    check('32bit → 8bit 对齐可执行', d.bitsPerChannel === 'bitDepth8', d.bitsPerChannel);
}

// ============ 2. 静态守卫 ============
console.log('== 静态守卫（旧写法不得复活）==');
const both = [{ f: 'common.js', s: commonSrc }, { f: 'main.js', s: mainSrc }];
const banned = [
    'Number(doc.bitsPerChannel)',
    'Number(tempDoc.bitsPerChannel)',
    'SixteenBitsChannel',
    'EightBitsChannel',
    'ChangeMode.EIGHT',
    'ChangeMode.SIXTEEN',
    'ChangeMode.THIRTYTWO',
];
for (const p of banned) {
    const hits = both.filter(x => x.s.includes(p)).map(x => x.f);
    check(`无残留 "${p}"`, hits.length === 0, hits.join(','));
}

// 每个 documents.add(...) 之后（同一段代码内）必须出现 setDocBits，
// 防止将来新增临时文档时又忘掉位深
for (const x of both) {
    const re = /await\s+app\.documents\.add\(/g;
    let m, bad = [], total = 0;
    while ((m = re.exec(x.s)) !== null) {
        total++;
        const tail = x.s.slice(m.index, m.index + 900);
        if (!/setDocBits\(/.test(tail)) bad.push('offset ' + m.index);
    }
    check(`${x.f}: ${total} 处 documents.add 均跟随 setDocBits`, bad.length === 0, bad.join(','));
}

// 上传/导入两条路径的诊断字段都应在位
check('common.js 返回对象带 bits 诊断字段',
    (commonSrc.match(/bits:\s*pngBytes\[24\]/g) || []).length === 2,
    String((commonSrc.match(/bits:\s*pngBytes\[24\]/g) || []).length));
check('main.js 打印上传 PNG 位深', /上传 PNG 位深/.test(mainSrc));

// ============ 3. 上传位深诊断用的 IHDR 偏移 ============
console.log('== IHDR 位深字节偏移（诊断取值正确性）==');
function ihdrBitsOf(png) { return png[24]; }
for (const bits of [16, 8]) {
    const w = 4, h = 2;
    const data = new Float32Array(w * h);
    for (let i = 0; i < data.length; i++) data[i] = i / (data.length - 1);
    const png = PngCodec.encodeGray(data, w, h, bits);
    const dec = PngCodec.decode(png);
    check(`${bits}bit PNG: png[24]===${bits} 且 decode 报 ${bits}`,
        ihdrBitsOf(png) === bits && dec.bitDepth === bits,
        `png[24]=${ihdrBitsOf(png)} decode=${dec.bitDepth}`);
}
// 16bit 往返必须保留超过 256 级（这才是「16bit 有效」的核心断言）
{
    const w = 4096, h = 1;
    const data = new Float32Array(w);
    for (let i = 0; i < w; i++) data[i] = i / (w - 1);
    const png16 = PngCodec.encodeGray(data, w, h, 16);
    const png8 = PngCodec.encodeGray(data, w, h, 8);
    const d16 = PngCodec.decode(png16), d8 = PngCodec.decode(png8);
    const uniq = a => new Set(Array.from(a.data)).size;
    check('16bit 编码保留 4096 个不同电平', uniq(d16) === 4096, String(uniq(d16)));
    check('8bit 编码只有 256 个不同电平（对照）', uniq(d8) === 256, String(uniq(d8)));
    check('两端电平精确：16bit 到 65535 / 8bit 到 255',
        d16.data[w - 1] === 65535 && d8.data[w - 1] === 255,
        `${d16.data[w - 1]} / ${d8.data[w - 1]}`);
    // 8bit 网格上的值都是 257 的倍数；16bit 输出绝大多数不是 → 证明没走 8bit 网格
    const offGrid = Array.from(d16.data).filter(v => v % 257 !== 0).length;
    check('16bit 输出绝大多数不落在 8bit 网格上（非 257 倍数）', offGrid > 4000, String(offGrid));
}

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail ? 1 : 0);
