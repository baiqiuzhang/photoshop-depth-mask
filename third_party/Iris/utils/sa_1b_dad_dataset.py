import os
import re
import torch
import numpy as np
from PIL import Image
from torchvision import transforms
from torch.utils.data import Dataset
from datasets import Dataset as Dataset_hf


def _compute_target_hw_from_first_image(first_image_path: str, resolution: int) -> tuple[int, int]:
    """
    Match Hypersim's resizing rule:
    Keep aspect ratio, scale so that the shorter side == resolution.

    Returns:
        (new_h, new_w) as integers.
    """
    w, h = Image.open(first_image_path).size  # PIL gives (W, H)
    if h > w:
        new_w = resolution
        new_h = int(resolution * h / w)
    else:
        new_h = resolution
        new_w = int(resolution * w / h)
    return new_h, new_w


def _parse_sa1b_id_jpg(fname: str) -> int | None:
    m = re.fullmatch(r"sa_(\d+)\.jpg", fname)
    return int(m.group(1)) if m else None


class SA1BTransform:
    """
    Transform for SA-1B dataset images with optional random flipping,
    using a fixed target (H, W) computed once (same rule as Hypersim).
    """

    def __init__(self, size_hw: tuple[int, int], random_flip: bool = True):
        """
        Args:
            size_hw: (H, W) target size (already computed using Hypersim rule).
            random_flip: whether to apply random horizontal flip.
        """
        self.size = size_hw
        self.random_flip = random_flip

    def __call__(self, image: Image.Image, depth: np.ndarray | None = None):
        # Resize RGB to (H, W)
        image = transforms.functional.resize(image, self.size, interpolation=Image.BILINEAR)

        # Resize depth if present
        if depth is not None:
            depth = torch.from_numpy(depth).unsqueeze(0).unsqueeze(0).float()  # (1,1,H,W)
            depth = torch.nn.functional.interpolate(depth, size=self.size, mode="nearest").squeeze(0).squeeze(0)  # (H,W)

        # Random horizontal flip
        if self.random_flip and np.random.random() > 0.5:
            image = transforms.functional.hflip(image)
            if depth is not None:
                depth = torch.flip(depth, dims=[-1])

        # To tensor and normalize to [-1, 1]
        image = transforms.ToTensor()(image)  # [0,1]
        image = transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])(image)  # [-1,1]

        if depth is not None:
            # Assume DA v2 depth in [0,1]; map to [-1,1] and make it 3-channel like RGB path
            depth_norm = (depth - 0.5) * 2.0
            depth_norm = depth_norm.clamp(-1, 1)
            depth_3ch = depth_norm.unsqueeze(0).repeat(3, 1, 1)  # (3,H,W)
            return image, depth_3ch

        return image


class SA1BDataset(Dataset):
    """
    PyTorch Dataset for SA-1B (real domain).
    Expects:
      - data_dir: folder with raw images (e.g., '.../raw')
      - corresponding depth files under data_dir.replace('raw', 'da_depth') with same basename and '.npy' suffix.

    Transforms:
      - Resize to fixed (H, W) computed with Hypersim rule: short side == resolution
      - Optional random horizontal flip
      - RGB normalized to [-1,1] with mean=[0.5,0.5,0.5], std=[0.5,0.5,0.5]
      - Depth (in [0,1]) mapped to [-1,1], expanded to 3 channels
    """

    def __init__(self, data_dir: str, random_flip: bool = True, resolution: int = 512):
        """
        Args:
            data_dir: 原始图片目录（包含 .jpg）
            random_flip: 是否随机水平翻转
            resolution: 短边缩放到该分辨率，保持比例

        注意：样本会在内部按固定阈值 _SA1B_MIN_ID 进行过滤，仅保留 sa_xxxx 中 xxxx >= _SA1B_MIN_ID 的样本。
        """
        self.data_dir = data_dir
        self.random_flip = random_flip
        self.depth_dir = data_dir.replace("raw", "da_depth")

        # Collect samples that have a matching depth file and id >= _SA1B_MIN_ID
        self.image_paths: list[str] = []
        for fname in sorted(os.listdir(data_dir)):
            if not (fname.startswith("sa_") and fname.endswith(".jpg")):
                continue
            sid = _parse_sa1b_id_jpg(fname)
            if sid is None or sid < _SA1B_MIN_ID:
                continue

            img_path = os.path.join(data_dir, fname)
            dep_path = os.path.join(self.depth_dir, fname.replace(".jpg", ".npy"))
            if os.path.exists(dep_path):
                self.image_paths.append(img_path)

        if len(self.image_paths) == 0:
            raise RuntimeError(
                f"No SA-1B samples with depth found under {data_dir} for id >= {_SA1B_MIN_ID}"
            )

        # Compute target (H, W) once using the first image (same as Hypersim)
        new_h, new_w = _compute_target_hw_from_first_image(self.image_paths[0], resolution)
        self.transform = SA1BTransform(size_hw=(new_h, new_w), random_flip=self.random_flip)

    def __len__(self) -> int:
        return len(self.image_paths)

    def __getitem__(self, idx: int):
        image_path = self.image_paths[idx]
        depth_path = os.path.join(self.depth_dir, os.path.basename(image_path).replace(".jpg", ".npy"))

        image = Image.open(image_path).convert("RGB")
        depth = np.load(depth_path)

        pixel_values, depth_values = self.transform(image, depth)
        return {
            "pixel_values": pixel_values,   # (3,H,W), [-1,1]
            "depth_values": depth_values,   # (3,H,W), [-1,1]  (DA v2 as 3ch for VAE path)
            "image_path": image_path,
            "depth_path": depth_path,
        }


def collate_fn_sa1b(examples: list[dict]):
    """
    Collate function for SA-1B dataset batches.
    Returns a dict of stacked tensors and lists of paths.
    """
    pixel_values = torch.stack([ex["pixel_values"] for ex in examples]).to(memory_format=torch.contiguous_format).float()
    depth_values = torch.stack([ex["depth_values"] for ex in examples]).to(memory_format=torch.contiguous_format).float()
    image_paths = [ex["image_path"] for ex in examples]
    depth_paths = [ex["depth_path"] for ex in examples]

    return {
        "pixel_values": pixel_values,  # (B,3,H,W)
        "depth_values": depth_values,  # (B,3,H,W)
        "image_paths": image_paths,
        "depth_paths": depth_paths,
    }


def get_sa1b_dataset(data_dir: str, random_flip: bool = True, resolution: int = 512):
    """
    HF Dataset-style loader for SA-1B, matching Hypersim resize logic.

    Args:
        data_dir: directory with raw images (e.g., '.../raw').
        random_flip: whether to apply random horizontal flip.
        resolution: target short-side resolution (same rule as Hypersim).

    Returns:
        dataset: HF Dataset with 'image' and 'depth' path columns（已按内部阈值过滤）
        preprocess_function: function to map PIL+NPY -> tensors with transforms
        collate_fn: PyTorch collate function
    """
    # Gather samples with matching depth and id >= _SA1B_MIN_ID
    image_paths, depth_paths = [], []
    depth_dir = data_dir.replace("raw", "da_depth")
    for fname in sorted(os.listdir(data_dir)):
        if not (fname.startswith("sa_") and fname.endswith(".jpg")):
            continue
        sid = _parse_sa1b_id_jpg(fname)
        if sid is None or sid < _SA1B_MIN_ID:
            continue

        ip = os.path.join(data_dir, fname)
        dp = os.path.join(depth_dir, fname.replace(".jpg", ".npy"))
        if os.path.exists(dp):
            image_paths.append(ip)
            depth_paths.append(dp)

    if len(image_paths) == 0:
        raise RuntimeError(f"No SA-1B samples with depth found under {data_dir} for id >= {_SA1B_MIN_ID}")

    dataset = Dataset_hf.from_dict({"image": image_paths, "depth": depth_paths})

    # Compute shared target size (H, W) using the first image (like Hypersim)
    new_h, new_w = _compute_target_hw_from_first_image(image_paths[0], resolution)
    size_hw = (new_h, new_w)

    # Build preprocess function
    image_column = "image"
    depth_column = "depth"

    def preprocess_sa1b(examples: dict):
        imgs = [Image.open(p).convert("RGB") for p in examples[image_column]]
        deps = [np.load(p) for p in examples[depth_column]]

        tfm = SA1BTransform(size_hw=size_hw, random_flip=random_flip)
        out_pixels, out_depths = [], []
        for img, dep in zip(imgs, deps):
            pv, dv = tfm(img, dep)
            out_pixels.append(pv)
            out_depths.append(dv)

        examples["pixel_values"] = out_pixels   # list of (3,H,W) tensors in [-1,1]
        examples["depth_values"] = out_depths   # list of (3,H,W) tensors in [-1,1]
        examples["image_path"] = examples[image_column]
        examples["depth_path"] = examples[depth_column]
        return examples

    return dataset, preprocess_sa1b, collate_fn_sa1b
