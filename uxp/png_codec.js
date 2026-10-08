/*!
 * png_codec.js — 纯 JS PNG 解码/编码（无依赖，除 pako）
 * - 解码：color type 0(灰度)/2(RGB)/6(RGBA) × 8/16bit，支持 Sub/Up/Average/Paeth 滤波撤销
 *   （移植自 img_io.py 的手工解码逻辑，16-bit 无损）
 * - 编码：灰度（color type 0）8/16bit 输出，filter 0 + pako deflate
 * - Node（module.exports）与 UXP（全局 PngCodec）双兼容
 */
(function (root, factory) {
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = factory(require('./vendor/pako.min.js'));
    } else {
        root.PngCodec = factory(root.pako);
    }
})(typeof self !== 'undefined' ? self : this, function (pako) {
    'use strict';

    // ---------- CRC32 ----------
    var crcTable = (function () {
        var t = new Uint32Array(256);
        for (var n = 0; n < 256; n++) {
            var c = n;
            for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
            t[n] = c >>> 0;
        }
        return t;
    })();

    function crc32(bytes, start, end) {
        var c = 0xFFFFFFFF;
        for (var i = start; i < end; i++) c = crcTable[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
        return (c ^ 0xFFFFFFFF) >>> 0;
    }

    // ---------- PNG chunk 组装 ----------
    function chunk(type, payload) {
        var out = new Uint8Array(12 + payload.length);
        var dv = new DataView(out.buffer);
        dv.setUint32(0, payload.length);
        for (var i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
        out.set(payload, 8);
        dv.setUint32(8 + payload.length, crc32(out, 4, 8 + payload.length));
        return out;
    }

    // ---------- 解码 ----------
    function decodePNG(bytes) {
        var u8 = (bytes instanceof Uint8Array) ? bytes : new Uint8Array(bytes);
        if (u8.length < 8 || u8[0] !== 0x89 || u8[1] !== 0x50 || u8[2] !== 0x4E || u8[3] !== 0x47) {
            throw new Error('not a PNG');
        }
        var pos = 8;
        var idat = [];
        var width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0;
        while (pos + 8 <= u8.length) {
            var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
            var len = dv.getUint32(pos);
            var type = String.fromCharCode(u8[pos + 4], u8[pos + 5], u8[pos + 6], u8[pos + 7]);
            var data = u8.subarray(pos + 8, pos + 8 + len);
            if (type === 'IHDR') {
                width = dv.getUint32(pos + 8);
                height = dv.getUint32(pos + 12);
                bitDepth = u8[pos + 16];
                colorType = u8[pos + 17];
                interlace = u8[pos + 20];
            } else if (type === 'IDAT') {
                idat.push(data);
            } else if (type === 'IEND') {
                break;
            }
            pos += 12 + len;
        }
        if (!width || !height || !bitDepth) throw new Error('invalid PNG header');
        if (interlace !== 0) throw new Error('interlaced PNG not supported');
        if (bitDepth !== 8 && bitDepth !== 16) throw new Error('unsupported bit depth ' + bitDepth);
        var channels = { 0: 1, 2: 3, 4: 2, 6: 4 }[colorType]; // 4=灰度+alpha（UXP 灰度文档 saveAs 实际输出）
        if (!channels) throw new Error('unsupported color type ' + colorType);

        var bpp = channels * (bitDepth / 8);
        var stride = width * bpp;
        var raw = pako.inflate(concatBytes(idat));

        // 撤销扫描线滤波器
        var out = new Uint8Array(height * stride);
        var prev = new Uint8Array(stride);
        var idx = 0;
        for (var y = 0; y < height; y++) {
            var ft = raw[idx++];
            var line = raw.subarray(idx, idx + stride);
            var row = new Uint8Array(stride);
            row.set(line);
            if (ft === 1) {          // Sub
                for (var i = bpp; i < stride; i++) row[i] = (row[i] + row[i - bpp]) & 0xFF;
            } else if (ft === 2) {   // Up
                for (var j = 0; j < stride; j++) row[j] = (row[j] + prev[j]) & 0xFF;
            } else if (ft === 3) {   // Average
                for (var k = 0; k < stride; k++) {
                    var a = k >= bpp ? row[k - bpp] : 0;
                    row[k] = (row[k] + ((a + prev[k]) >> 1)) & 0xFF;
                }
            } else if (ft === 4) {   // Paeth
                for (var m = 0; m < stride; m++) {
                    var pa = m >= bpp ? row[m - bpp] : 0;
                    var pb = prev[m];
                    var pc = (m >= bpp) ? prev[m - bpp] : 0;
                    var psum = pa + pb - pc;
                    var dpa = Math.abs(psum - pa), dpb = Math.abs(psum - pb), dpc = Math.abs(psum - pc);
                    var pr = (dpa <= dpb && dpa <= dpc) ? pa : (dpb <= dpc ? pb : pc);
                    row[m] = (row[m] + pr) & 0xFF;
                }
            } else if (ft !== 0) {
                throw new Error('unsupported PNG filter ' + ft);
            }
            out.set(row, y * stride);
            prev = row;
            idx += stride;
        }

        var result;
        if (bitDepth === 16) {
            var u16 = new Uint16Array(width * height * channels);
            for (var p = 0; p < width * height * channels; p++) {
                u16[p] = (out[p * 2] << 8) | out[p * 2 + 1];
            }
            result = u16;
        } else {
            result = new Uint8Array(out);
        }
        return {
            width: width, height: height, bitDepth: bitDepth,
            colorType: colorType, channels: channels, data: result,
        };
    }

    function concatBytes(chunks) {
        var total = 0;
        for (var i = 0; i < chunks.length; i++) total += chunks[i].length;
        var out = new Uint8Array(total);
        var off = 0;
        for (var j = 0; j < chunks.length; j++) { out.set(chunks[j], off); off += chunks[j].length; }
        return out;
    }

    // ---------- 灰度浮点提取 ----------
    // 返回 Float32Array（0-1，H*W）：灰度取通道0；RGBA 取 alpha（通道导出路径）；RGB 取亮度
    function grayFloat(png) {
        var w = png.width, h = png.height, ch = png.channels, bits = png.bitDepth;
        var n = w * h;
        var out = new Float32Array(n);
        var maxv = bits === 16 ? 65535 : 255;
        var d = png.data;
        var src = ch === 4 ? 3 : (ch === 1 ? 0 : 0); // RGBA→alpha, gray→0, RGB→R
        for (var i = 0; i < n; i++) {
            out[i] = d[i * ch + src] / maxv;
        }
        return out;
    }

    // ---------- 编码（灰度 color type 0，8/16bit） ----------
    // data01: Float32Array 0-1（长度 w*h），或 Uint8Array/Uint16Array 原始值
    function encodeGray(data01, w, h, bits) {
        bits = bits >= 16 ? 16 : 8;
        var n = w * h;
        var bpp = bits / 8;
        var stride = w * bpp;
        var raw = new Uint8Array(h * (1 + stride));
        for (var y = 0; y < h; y++) {
            var rowStart = y * (1 + stride);
            raw[rowStart] = 0; // filter None
            var sample = rowStart + 1;
            if (bits === 16) {
                for (var i = 0; i < w; i++) {
                    var v = data01[y * w + i] <= 1.0001 ? Math.round(data01[y * w + i] * 65535) : data01[y * w + i];
                    if (v < 0) v = 0; if (v > 65535) v = 65535;
                    raw[sample + i * 2] = (v >> 8) & 0xFF;
                    raw[sample + i * 2 + 1] = v & 0xFF;
                }
            } else {
                for (var j = 0; j < w; j++) {
                    var v8 = data01[y * w + j] <= 1.0001 ? Math.round(data01[y * w + j] * 255) : data01[y * w + j];
                    if (v8 < 0) v8 = 0; if (v8 > 255) v8 = 255;
                    raw[sample + j] = v8;
                }
            }
        }
        var ihdr = new Uint8Array(13);
        var dv = new DataView(ihdr.buffer);
        dv.setUint32(0, w);
        dv.setUint32(4, h);
        ihdr[8] = bits;
        ihdr[9] = 0; // colorType 灰度
        ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0; // 压缩/滤波/交错
        var sig = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
        var idat = pako.deflate(raw);
        return concatBytes([sig, chunk('IHDR', ihdr), chunk('IDAT', idat), chunk('IEND', new Uint8Array(0))]);
    }

    return {
        decode: decodePNG,
        grayFloat: grayFloat,
        encodeGray: encodeGray,
    };
});
