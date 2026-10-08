# -*- coding: utf-8 -*-
"""
PNG 解码辅助模块
=================
PIL 读取 16bit RGB/RGBA PNG 时会直接丢弃低 8 位（把 16bit 压成 8bit），
导致 16bit 文档的输入精度在进入模型前就损失掉。
本模块对 16bit 真彩色 PNG 走 cv2（libpng 原生解码，完整保留 16bit 精度；
支持 Adam7 交错与全部 5 种扫描线滤波器，C 实现远快于逐像素 Python 循环）；
其余位深/格式回退 PIL 解码。

load_image_16bit(data: bytes) -> np.ndarray float32 (H, W, 3)，值域 [0, 1]
"""
import io
import struct

import numpy as np
from PIL import Image


MAX_IMAGE_DIMENSION = 16000
MAX_IMAGE_PIXELS = 64_000_000
_PNG_SIG = b'\x89PNG\r\n\x1a\n'


def _png_ihdr(data):
    """从 PNG 字节流解析 IHDR，返回 (width, height, bit_depth, color_type)；
    非法/缺失时返回 None。只读前几个 chunk，不解压 IDAT。"""
    if not isinstance(data, (bytes, bytearray)) or bytes(data[:8]) != _PNG_SIG:
        return None
    if len(data) < 8 + 8 + 13:
        return None
    length = struct.unpack('>I', data[8:12])[0]
    if length < 13 or data[12:16] != b'IHDR':
        return None
    width, height, bit_depth, color_type = struct.unpack('>IIBB', data[16:26])
    return width, height, bit_depth, color_type


def load_image_16bit(data):
    """返回 float32 (H, W, 3) 值域 [0,1] 的 RGB 图像。16bit PNG 无损解码，其余回退 PIL。"""
    data = bytes(data)
    ihdr = _png_ihdr(data)
    if ihdr is not None:
        width, height, bit_depth, color_type = ihdr
        if (width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or
                height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS):
            raise ValueError('PNG dimensions exceed safety limits')
        if bit_depth == 16 and color_type in (2, 6):
            # 惰性导入：协调进程通常只走 8bit 回退路径，避免常驻 cv2 内存
            import cv2
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if arr is None or arr.ndim != 3 or arr.shape[2] not in (3, 4):
                raise ValueError('16bit PNG 解码失败')
            if arr.dtype != np.uint16:
                raise ValueError('16bit PNG 解码位深不符')
            # OpenCV 通道序为 BGR(A)，转回 PNG 语义的 RGB(A)
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA if arr.shape[2] == 4
                               else cv2.COLOR_BGR2RGB)
            return arr[:, :, :3].astype(np.float32) * (1.0 / 65535.0)

    return _pil_fallback(data)


def _pil_fallback(data):
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        if (width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or
                height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS):
            raise ValueError('image dimensions exceed safety limits')
        img = source.convert('RGB')
        return np.asarray(img, dtype=np.float32) / 255.0


def probe_png_bits(data):
    """返回输入 PNG 的位深（16 或 8），用于决定输出位深。

    协议 7 约定：PS 提供 16-bit 图像则返回 16-bit，8-bit 图像则返回 8-bit。
    16bit PNG 以 IHDR 为准；其余位深按 PIL mode 判定（I;16 -> 16，其余 -> 8）。
    """
    if isinstance(data, (bytes, bytearray)) and bytes(data[:8]) == _PNG_SIG:
        if len(data) >= 8 + 8 + 13:
            length = struct.unpack('>I', data[8:12])[0]
            if length >= 13 and data[12:16] == b'IHDR':
                # IHDR 数据从偏移 16 开始：width(4) height(4) bit_depth(1) color_type(1)
                bit_depth = data[24]
                if bit_depth == 16:
                    return 16
    try:
        with Image.open(io.BytesIO(bytes(data))) as source:
            return 16 if source.mode in ('I;16', 'I;16L', 'I;16B') else 8
    except Exception:
        return 8
