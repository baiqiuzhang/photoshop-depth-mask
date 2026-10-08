"""
Depth Mask 深度估计服务 (v7, 协议 7)
=====================================
- 模块化多模型：bridge / depthpro / distillanydepth / iris / ppd
- 启动/每次 /ping 检视插件目录，available_models 反馈给前端确定"模型"选项
- 新流水线：模型原生推理 → 离群值裁切(0.025-99.925) → 超分辨率(bilinear/gf/none)
  → 直方图均衡化 → 雾气深度变换 → 归一化 → 采样到 16bit/8bit 返回 PS
- 输出位深由输入 PNG 位深决定（PS 16-bit 文档 → 16-bit I;16；8-bit → 8-bit L）
- 每个模型在一次性子进程中推理（.npy 工件交换），CUDA 失败回退 CPU
"""
import base64
import gc
import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from flask import Flask, request, jsonify

import img_io
import models
import postprocess as pipeline

# ---------- 配置 ----------
APP_HOST = '127.0.0.1'
APP_PORT = 8766
SERVER_VERSION = "7"
MAX_CONTENT_LENGTH = 500 * 1024 * 1024
LOG_FILE = "depth_server.log"
WORKER_TIMEOUT = 7200
MODEL_TIMEOUT = 5400
VALID_OPTIONS = ('depth', 'fog')
VALID_POSTPROCESS = ('none', 'S1', 'S5', 'N1_KMeans', 'N1A', 'N2')
VALID_UPSCALE = ('bilinear', 'wgif', 'jbu-nc', 'none')
MAX_OUTPUT_PIXELS = 64_000_000
MAX_IMAGE_DIMENSION = 16000

os.environ.setdefault('TQDM_DISABLE', '1')
if sys.platform != 'win32':
    # Windows 版 torch 不支持 expandable_segments，设置只会产生 UserWarning
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')


# ---------- 路径处理 ----------
def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

for module_dir in (SOURCE_DIR, BASE_DIR, getattr(sys, '_MEIPASS', None)):
    if module_dir and module_dir not in sys.path:
        sys.path.insert(0, module_dir)

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass


# ---------- 日志 ----------
def log(msg):
    msg = str(msg)
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(os.path.join(BASE_DIR, LOG_FILE), 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


# ---------- 模型检视（寻址鲁棒性） ----------
def available_models_snapshot():
    """每次调用重新检视插件目录（cost 极低：若干 isfile），返回 {key: {...}}。"""
    return models.detect_available_models(BASE_DIR, SOURCE_DIR)


# ---------- 模型子进程 ----------
def model_worker_process(model_key, image_path, model_root, native_out_path, force_cpu, conn):
    """一次性模型进程；深度以 .npy 工件落盘，Pipe 只传状态。"""
    status = {'ok': False, 'model': model_key, 'pid': os.getpid(),
              'force_cpu': bool(force_cpu)}
    try:
        import numpy as np
        depth, diagnostics = models.run_model(model_key, image_path, model_root, force_cpu)
        np.save(native_out_path, np.asarray(depth, dtype=np.float32), allow_pickle=False)
        status['ok'] = True
        status['diagnostics'] = diagnostics
    except Exception as exc:
        status['error'] = str(exc)
        status['traceback'] = traceback.format_exc()
        log(f"❌ {model_key} 模型进程异常: {exc}")
        log(status['traceback'])
    finally:
        try:
            conn.send(status)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        os._exit(0)


def _kill_pid_tree(pid):
    if not pid:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
        else:
            os.kill(int(pid), 9)
    except Exception as exc:
        log(f"⚠️ 清理进程树失败 pid={pid}: {exc}")


def _stop_process(process):
    if process is None or not process.is_alive():
        return
    if os.name == 'nt':
        _kill_pid_tree(process.pid)
    else:
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        _kill_pid_tree(process.pid)
        process.join(timeout=5)
    if process.is_alive():
        raise RuntimeError(f'无法终止进程 pid={process.pid}')


def _run_model_once(model_key, model_root, image_path, native_out_path, deadline, force_cpu=False):
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(
        target=model_worker_process,
        args=(model_key, image_path, model_root, native_out_path, force_cpu, child_conn),
        daemon=False,
    )
    process.start()
    child_conn.close()
    pid_file = os.path.join(os.path.dirname(image_path), 'current_model_pid.txt')
    with open(pid_file, 'w', encoding='ascii') as handle:
        handle.write(str(process.pid))
    status = None
    try:
        while time.monotonic() < deadline:
            if parent_conn.poll(min(0.5, max(0.0, deadline - time.monotonic()))):
                try:
                    status = parent_conn.recv()
                except EOFError:
                    pass
                break
            if not process.is_alive():
                break
        if status is None and process.is_alive():
            _stop_process(process)
            raise TimeoutError(f'{model_key} 模型进程超时')
        process.join(timeout=5)
        if process.is_alive():
            _stop_process(process)
        if status is None:
            raise RuntimeError(f'{model_key} 模型进程异常退出 (exitcode={process.exitcode})')
        return status
    finally:
        parent_conn.close()
        if process.is_alive():
            _stop_process(process)
        try:
            if os.path.isfile(pid_file):
                os.remove(pid_file)
        except OSError:
            pass


# ---------- 请求协调 ----------
def estimate_depth(image_base64, out_options, postprocess=None, original_size=None,
                   upscale=None, model_name='depthpro', work_dir=None, deadline=None):
    import numpy as np

    _t0 = time.monotonic()
    raw = base64.b64decode(image_base64)
    out_bits = img_io.probe_png_bits(raw)
    img_np = img_io.load_image_16bit(raw)
    _t_decode = time.monotonic()
    input_size = tuple(img_np.shape[:2])
    target_size = ((int(original_size[1]), int(original_size[0]))
                   if original_size is not None else input_size)

    upscale = dict(upscale or {})
    upscale_mode = upscale.get('mode', 'bilinear')
    upscale_params = dict(upscale.get('params') or {})

    snapshot = available_models_snapshot()
    entry = snapshot.get(model_name)
    if not entry or not entry.get('found'):
        raise ValueError(f'模型 {model_name} 的权重文件夹未检出（插件目录缺少 '
                         f'{models.MODEL_SPECS[model_name]["folder"]}/）')
    model_root = entry['root']

    owns_work_dir = work_dir is None
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix='depth_v7_')
    deadline = deadline if deadline is not None else time.monotonic() + WORKER_TIMEOUT
    try:
        image_path = os.path.join(work_dir, 'input.npy')
        np.save(image_path, img_np, allow_pickle=False)
        native_out_path = os.path.join(work_dir, 'native_depth.npy')

        model_deadline = min(deadline, time.monotonic() + MODEL_TIMEOUT)
        status = None
        attempts = []
        # 首选 CUDA；CUDA 失败时 CPU 重试（Iris 在适配器内部自行降 processing_res）
        for force_cpu in (False, True):
            if time.monotonic() >= model_deadline:
                break
            attempt = {'force_cpu': force_cpu}
            try:
                status = _run_model_once(model_name, model_root, image_path,
                                         native_out_path, model_deadline,
                                         force_cpu=force_cpu)
                attempt['ok'] = bool(status.get('ok'))
                attempts.append(attempt)
                if status.get('ok'):
                    break
                error_text = ((status.get('error', '') + '\n'
                               + status.get('traceback', '')).lower())
                attempt['error'] = status.get('error')
                is_cuda = any(token in error_text for token in (
                    'cuda', 'cudnn', 'cublas', 'no kernel image', 'out of memory'))
                if not is_cuda:
                    break
                log(f"⚠️ {model_name} CUDA 失败，尝试 CPU 重试: {status.get('error')}")
            except Exception as exc:
                attempt['ok'] = False
                attempt['error'] = str(exc)
                attempts.append(attempt)
                break
        if not status or not status.get('ok'):
            raise RuntimeError((status or {}).get('error', f'{model_name} 模型推理失败'))
        model_diagnostics = {
            'device': (status.get('diagnostics') or {}).get('device', 'unknown'),
            'native_shape': (status.get('diagnostics') or {}).get('native_shape'),
            'attempts': attempts,
            'inference': status.get('diagnostics') or {},
        }
        log(f"  🧠 {model_name} 推理完成: "
            f"native={status.get('diagnostics', {}).get('native_shape')} "
            f"device={model_diagnostics['device']}")

        native_depth = np.load(native_out_path, allow_pickle=False)
        _t_infer = time.monotonic()

        # ---- 离群值裁切（0.025-99.925 对称分位 + 方向归一化到远亮） ----
        clipped = pipeline.sanitize_and_clip(native_depth, entry['invert_direction'])

        # ---- 归一化 + 直方图均衡化（在原生分辨率、超分之前执行） ----
        # 直方图均衡的 5 个算法均为纯值 LUT（无空间操作），在原生分辨率执行后由
        # 超分插值重建过渡坡。旧序（超分后均衡）会把超分产生的宽过渡带的稀疏
        # 中间调经 CDF 压成"平架+陡台阶"——即天地边界处与边缘平行的灰带
        # （_DSC9662 实测：换序后暗侧斜率 0.083→0.022/k，平台度 0.92，平架消失）。
        # 附带收益：S5 GMM 等重算法从最大 64MP 降到原生 ~0.4MP。
        depth01_native = pipeline.normalize_minmax(clipped)
        algorithm = (postprocess or {}).get('algorithm', 'none')
        postprocess_params = dict((postprocess or {}).get('params') or {})
        histogram_applied = False
        if algorithm != 'none':
            try:
                import histogram_eq
                depth01_native = histogram_eq.apply_postprocess(
                    depth01_native, algorithm, postprocess_params)
                histogram_applied = True
            except Exception as exc:
                log(f"  ⚠️ 直方图均衡化失败: {exc}，回退归一化深度")
        _t_hist = time.monotonic()

        # ---- 超分辨率 ----
        guide_np = None
        if upscale_mode in ('bilinear', 'wgif', 'jbu-nc'):
            guide_np = img_np if input_size == target_size else _resize_rgb(img_np, target_size)
        depth, sr_diagnostics = pipeline.upscale(
            depth01_native, target_size, upscale_mode, upscale_params, guide=guide_np)
        _t_sr = time.monotonic()
        log(f"  🔍 超分: {sr_diagnostics}")

        # ---- 仿射收尾（超分全程为凸组合，值域不会越界；min-max 重归一化保持
        #      剖面形状不重建平台） ----
        depth01 = np.clip(np.asarray(pipeline.normalize_minmax(depth), dtype=np.float32),
                          0.0, 1.0)

        # ---- 雾气深度变换 ----
        fog01 = pipeline.fog_transform(depth01)
        _t_fog = time.monotonic()

        results = {
            'diagnostics': {
                'protocol_version': SERVER_VERSION,
                'model': model_name,
                'input_shape': [int(v) for v in input_size],
                'output_shape': [int(v) for v in target_size],
                'input_bit_depth': out_bits,
                'output_bit_depth': out_bits,
                'invert_direction': bool(entry['invert_direction']),
                'native_shape': model_diagnostics.get('native_shape'),
                'model_device': model_diagnostics['device'],
                'model_attempts': attempts,
                'upscale': sr_diagnostics,
                'histogram': {'algorithm': algorithm, 'applied': histogram_applied,
                              'stage': 'pre_upscale_native'},
                'postprocess_params': postprocess_params,
                'stage_seconds': {
                    'decode_png': round(_t_decode - _t0, 3),
                    'model_infer': round(_t_infer - _t_decode, 3),
                    'normalize_hist': round(_t_hist - _t_infer, 3),
                    'upscale_sr': round(_t_sr - _t_hist, 3),
                    'fog': round(_t_fog - _t_sr, 3),
                },
            }
        }

        def _encode(image_obj):
            buffer = io.BytesIO()
            image_obj.save(buffer, format='PNG')
            return base64.b64encode(buffer.getvalue()).decode('utf-8')

        if 'fog' in out_options:
            results['fog'] = _encode(pipeline.quantize_png(fog01, out_bits))
        if 'depth' in out_options:
            results['depth'] = _encode(pipeline.quantize_png(depth01, out_bits))
        results['diagnostics']['stage_seconds']['encode_png'] = round(
            time.monotonic() - _t_fog, 3)
        return results
    finally:
        if owns_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def _resize_rgb(values, target_size):
    import numpy as np
    from PIL import Image
    target_h, target_w = map(int, target_size)
    channels = [_resize_float(values[..., index], (target_h, target_w))
                for index in range(values.shape[2])]
    return np.stack(channels, axis=-1).astype(np.float32)


def _resize_float(values, target_size):
    import numpy as np
    from PIL import Image
    values = np.asarray(values, dtype=np.float32).squeeze()
    target_h, target_w = map(int, target_size)
    if values.shape == (target_h, target_w):
        return values.astype(np.float32, copy=False)
    image = Image.fromarray(values, mode='F')
    image = image.resize((target_w, target_h), resample=Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32)


def worker_process(req, work_dir, deadline, conn):
    result = {}
    try:
        result.update(estimate_depth(
            req['image'], req.get('options', ['depth']), req.get('postprocess', {}),
            req.get('original_size'), req.get('upscale'), req.get('model', 'depthpro'),
            work_dir=work_dir, deadline=deadline,
        ))
    except Exception as exc:
        result = {'error': str(exc), 'traceback': traceback.format_exc()}
        log(f"❌ 协调进程异常: {exc}")
        log(result['traceback'])
    finally:
        try:
            conn.send(result)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


# ---------- Flask 应用 ----------
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH


def _finite_number(value, name, low=None, high=None, integer=False):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是数字')
    if not math.isfinite(float(value)):
        raise ValueError(f'{name} 必须是有限数字')
    if integer and not isinstance(value, int):
        raise ValueError(f'{name} 必须是整数')
    if low is not None and value < low:
        raise ValueError(f'{name} 不能小于 {low}')
    if high is not None and value > high:
        raise ValueError(f'{name} 不能大于 {high}')
    return value


def _validate_params(params, allowed, prefix):
    if not isinstance(params, dict):
        raise ValueError(f'{prefix} 必须是对象')
    unknown = set(params) - set(allowed)
    if unknown:
        raise ValueError(f"{prefix} 包含未知参数: {', '.join(sorted(unknown))}")
    for name, rules in allowed.items():
        if name in params:
            _finite_number(params[name], f'{prefix}.{name}', **rules)


def _validate_request(data):
    if not isinstance(data, dict):
        raise ValueError('请求体必须是 JSON 对象')
    unknown_top = set(data) - {'image', 'model', 'options', 'original_size',
                               'postprocess', 'upscale'}
    if unknown_top:
        raise ValueError(f"请求包含未知字段: {', '.join(sorted(unknown_top))}")

    image = data.get('image')
    if not isinstance(image, str) or not image:
        raise ValueError('缺少或无效 image 字段')
    if len(image) > MAX_CONTENT_LENGTH * 4 // 3:
        raise ValueError('image 数据过大')

    model_name = data.get('model', 'depthpro')
    if not isinstance(model_name, str) or model_name not in models.MODEL_KEYS:
        raise ValueError('无效 model；可选值: ' + ', '.join(models.MODEL_KEYS))
    data['model'] = model_name

    options = data.get('options', ['depth'])
    if (not isinstance(options, list) or len(options) != 1
            or not isinstance(options[0], str) or options[0] not in VALID_OPTIONS):
        raise ValueError('options 必须是单个元素: ["depth"] 或 ["fog"]（both 已废除）')
    data['options'] = [options[0]]

    original_size = data.get('original_size')
    if original_size is not None:
        if (not isinstance(original_size, (list, tuple)) or len(original_size) != 2):
            raise ValueError('original_size 必须是 [width, height]')
        width = _finite_number(original_size[0], 'original_size.width', 1,
                               MAX_IMAGE_DIMENSION, integer=True)
        height = _finite_number(original_size[1], 'original_size.height', 1,
                                MAX_IMAGE_DIMENSION, integer=True)
        if width * height > MAX_OUTPUT_PIXELS:
            raise ValueError('original_size 像素数超过安全限制')

    postprocess = data.get('postprocess', {})
    if not isinstance(postprocess, dict) or set(postprocess) - {'algorithm', 'params'}:
        raise ValueError('postprocess 必须是仅含 algorithm、params 的对象')
    algorithm = postprocess.get('algorithm', 'none')
    if not isinstance(algorithm, str) or algorithm not in VALID_POSTPROCESS:
        raise ValueError('postprocess.algorithm 无效')
    numeric_rules = {
        'strength': {'low': 0.0, 'high': 2.0},
        'K_GMM': {'low': 1, 'high': 32, 'integer': True},
        'min_segment_ratio': {'low': 0.0, 'high': 1.0},
        'K': {'low': 1, 'high': 32, 'integer': True},
        'smooth_sigma': {'low': 0.0, 'high': 100.0},
        'height_ratio': {'low': 0.0, 'high': 1.0},
    }
    algorithm_params = {
        'none': (), 'S1': ('strength',),
        'S5': ('K_GMM', 'min_segment_ratio', 'strength'),
        'N1_KMeans': ('K', 'strength'),
        'N1A': ('smooth_sigma', 'height_ratio', 'strength'),
        'N2': ('smooth_sigma', 'strength'),
    }
    params = postprocess.get('params', {})
    if algorithm == 'N2' and isinstance(params, dict) and 'model' in params:
        if params['model'] != 'auto':
            raise ValueError('postprocess.params.model 仅支持 auto')
        params = dict(params)
        params.pop('model')
    allowed = {name: numeric_rules[name] for name in algorithm_params[algorithm]}
    _validate_params(params, allowed, 'postprocess.params')

    upscale = data.get('upscale', {})
    if not isinstance(upscale, dict) or set(upscale) - {'mode', 'params'}:
        raise ValueError('upscale 必须是仅含 mode、params 的对象')
    upscale_mode = upscale.get('mode', 'bilinear')
    if upscale_mode not in VALID_UPSCALE:
        raise ValueError('upscale.mode 仅支持 bilinear、wgif、jbu-nc、none')
    uparams = upscale.get('params', {})
    if upscale_mode == 'wgif':
        allowed = {'radius': {'low': pipeline.GF_RADIUS_MIN,
                              'high': pipeline.GF_RADIUS_MAX, 'integer': True}}
    elif upscale_mode == 'jbu-nc':
        import upscale_sr
        allowed = {'sigma_r': {'low': upscale_sr.JBU_SIGMA_R_MIN,
                               'high': upscale_sr.JBU_SIGMA_R_MAX}}
    else:
        allowed = {}
    _validate_params(uparams, allowed, 'upscale.params')

    try:
        raw = base64.b64decode(image, validate=True)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as probe:
            width, height = probe.size
    except Exception as exc:
        raise ValueError('image 不是有效 Base64 图像') from exc
    if (width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION or
            height > MAX_IMAGE_DIMENSION or width * height > MAX_OUTPUT_PIXELS):
        raise ValueError('输入图像尺寸超过安全限制')
    return data


@app.route('/ping', methods=['GET'])
def ping():
    rss_mb = None
    try:
        import psutil
        rss_mb = int(psutil.Process().memory_info().rss // (1024 * 1024))
    except Exception:
        pass
    import upscale_sr
    snapshot = available_models_snapshot()
    return jsonify({
        'scheduler': 'ok', 'depth_server': True, 'version': SERVER_VERSION,
        'default_model': 'depthpro',
        'models': [key for key in models.MODEL_KEYS],
        'available_models': [
            {
                'key': key,
                'folder': info['folder'],
                'display': info['display'],
                'found': info['found'],
                'weight_ok': info['weight_ok'],
                'resolution_note': info['resolution_note'],
                'invert_direction': info['invert_direction'],
            }
            for key, info in snapshot.items()
        ],
        'rss_mb': rss_mb,
        'capabilities': {
            'upscale': list(VALID_UPSCALE),
            'upscale_default': 'bilinear',
            'wgif_radius': {'min': pipeline.GF_RADIUS_MIN,
                            'max': pipeline.GF_RADIUS_MAX,
                            'default': pipeline.GF_DEFAULT_RADIUS},
            'jbu_nc_sigma_r': {'min': upscale_sr.JBU_SIGMA_R_MIN,
                               'max': upscale_sr.JBU_SIGMA_R_MAX,
                               'default': upscale_sr.JBU_SIGMA_R_DEFAULT},
            'postprocess': list(VALID_POSTPROCESS),
            'postprocess_default': 'S1',
            'options': ['depth', 'fog'],
            'output_bit_depth': 'auto(随输入 PNG 位深: 16/8)',
            'response_diagnostics': True,
            'artifact_transport': 'npy',
        },
    })


@app.route('/', methods=['GET'])
def index():
    return jsonify({'service': 'depth_server', 'version': SERVER_VERSION,
                    'routes': ['/ping', '/process']})


@app.errorhandler(404)
def not_found(error):
    log(f"⚠️ 404 路由未找到: {request.method} {request.path}")
    return jsonify({'error': 'route not found', 'path': request.path}), 404


@app.route('/process', methods=['POST'])
def process():
    data = request.get_json(silent=True)
    try:
        data = _validate_request(data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    work_dir = tempfile.mkdtemp(prefix='depth_v7_')
    deadline = time.monotonic() + WORKER_TIMEOUT
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    coordinator = None
    try:
        coordinator = multiprocessing.Process(
            target=worker_process, args=(data, work_dir, deadline, child_conn), daemon=False
        )
        coordinator.start()
    except Exception as exc:
        parent_conn.close()
        shutil.rmtree(work_dir, ignore_errors=True)
        return jsonify({'error': '协调进程启动失败: ' + str(exc)}), 500
    finally:
        child_conn.close()

    result = None
    try:
        while time.monotonic() < deadline:
            try:
                if parent_conn.poll(min(0.5, max(0.0, deadline - time.monotonic()))):
                    result = parent_conn.recv()
                    break
            except (EOFError, OSError):
                break
            if not coordinator.is_alive():
                break
        if result is None and coordinator.is_alive():
            pid_file = os.path.join(work_dir, 'current_model_pid.txt')
            child_pid = None
            try:
                if os.path.isfile(pid_file):
                    with open(pid_file, 'r', encoding='ascii') as handle:
                        child_pid = int(handle.read().strip())
            except Exception:
                pass
            if child_pid:
                _kill_pid_tree(child_pid)
            _stop_process(coordinator)
            result = {'error': '处理超时'}
        elif result is None:
            result = {'error': f'协调进程异常退出（exitcode={coordinator.exitcode}）'}
        coordinator.join(timeout=5)
        if coordinator.is_alive():
            _stop_process(coordinator)
    finally:
        parent_conn.close()
        shutil.rmtree(work_dir, ignore_errors=True)
    result.pop('traceback', None)
    if 'error' in result:
        return jsonify({'error': result['error']}), 500
    return jsonify(result)


def _sweep_stale_work_dirs(max_age_hours=24.0):
    """清扫 %TEMP%/depth_v7_* 崩溃残留（进程被硬杀时 finally 无法执行）。"""
    import glob
    cutoff = time.time() - max_age_hours * 3600.0
    removed = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), 'depth_v7_*')):
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    if removed:
        log(f"  🧹 清扫陈旧工作目录 {removed} 个（>{max_age_hours:.0f}h）")


if __name__ == '__main__':
    multiprocessing.freeze_support()
    _sweep_stale_work_dirs()
    startup = available_models_snapshot()
    log(f"🚀 Depth server v{SERVER_VERSION} 启动，监听 {APP_HOST}:{APP_PORT}")
    for key, info in startup.items():
        log(f"  模型 {info['display']:>20} ({key}): "
            f"{'✅ 已检出 ' + info['resolution_note'] if info['found'] else '❌ 未检出'}")
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=False)
