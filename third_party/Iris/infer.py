#!/usr/bin/env python
# coding=utf-8
import logging
import os
import argparse
from pathlib import Path
from PIL import Image
from contextlib import nullcontext

import numpy as np
import torch
from tqdm.auto import tqdm
from diffusers.utils import check_min_version

from pipeline import IrisPipeline
from utils.image_utils import colorize_depth_map
from utils.seed_all import seed_all

check_min_version("0.28.0.dev0")


def parse_args():
    """Set the arguments for two-stage regression inference."""
    parser = argparse.ArgumentParser(description="Run Iris inference.")

    # model settings
    parser.add_argument(
        "--pretrained_model_name_or_path",
        type=str,
        default=None,
        required=True,
        help="Pretrained model path from Hugging Face or local directory.",
    )
    parser.add_argument(
        "--task_name",
        type=str,
        default="depth",  # or "normal"
        help="Task name. Supported: 'depth' or 'normal'.",
    )
    parser.add_argument(
        "--disparity",
        action="store_true",
        help="If set, treat depth as disparity and use reversed colormap.",
    )
    parser.add_argument(
        "--enable_xformers_memory_efficient_attention",
        action="store_true",
        help="Whether or not to use xFormers memory efficient attention.",
    )
    parser.add_argument(
        "--timestep_stage1",
        type=int,
        default=499,
        help="Timestep for the first stage in the two-stage pipeline.",
    )
    parser.add_argument(
        "--timestep_stage2",
        type=int,
        default=999,
        help="Timestep for the second stage in the two-stage pipeline.",
    )

    # inference settings
    parser.add_argument("--seed", type=int, default=None, help="Random seed.")
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Output directory.",
    )
    parser.add_argument(
        "--input_dir",
        type=str,
        required=True,
        help="Input directory containing images.",
    )
    parser.add_argument(
        "--half_precision",
        action="store_true",
        help="Run with half-precision (float16). Might lead to slightly different results.",
    )
    parser.add_argument(
        "--processing_res",
        type=int,
        default=None,
        help="Maximum processing resolution. 0 for using input image resolution. Default: use pipeline default.",
    )
    parser.add_argument(
        "--output_processing_res",
        action="store_true",
        help="If set, output depth at resized operating resolution instead of input resolution.",
    )
    parser.add_argument(
        "--resample_method",
        choices=["bilinear", "bicubic", "nearest"],
        default="bilinear",
        help=(
            "Resampling method used to resize images and depth predictions. "
            "Can be one of `bilinear`, `bicubic` or `nearest`. Default: `bilinear`."
        ),
    )

    args = parser.parse_args()
    return args


def main():
    logging.basicConfig(level=logging.INFO)
    logging.info("Run two-stage regression inference...")

    args = parse_args()

    # -------------------- Preparation --------------------
    # Random seed
    if args.seed is not None:
        seed_all(args.seed)

    # Output directories
    os.makedirs(args.output_dir, exist_ok=True)
    logging.info(f"Output dir = {args.output_dir}")

    # Directories for stage-1 / stage-2 predictions and comparison visualization
    output_dir_color_stage1 = os.path.join(args.output_dir, f"{args.task_name}_vis_stage1")
    output_dir_npy_stage1 = os.path.join(args.output_dir, f"{args.task_name}_stage1")
    output_dir_color_stage2 = os.path.join(args.output_dir, f"{args.task_name}_vis_stage2")
    output_dir_npy_stage2 = os.path.join(args.output_dir, f"{args.task_name}_stage2")
    output_dir_color_comparison = os.path.join(args.output_dir, f"{args.task_name}_vis_comparison")

    for d in [
        output_dir_color_stage1,
        output_dir_npy_stage1,
        output_dir_color_stage2,
        output_dir_npy_stage2,
        output_dir_color_comparison,
    ]:
        os.makedirs(d, exist_ok=True)

    # Precision
    if args.half_precision:
        dtype = torch.float16
        logging.info(f"Running with half precision ({dtype}).")
    else:
        dtype = torch.float32

    # Processing resolution
    processing_res = args.processing_res
    match_input_res = not args.output_processing_res
    if processing_res == 0 and match_input_res is False:
        logging.warning(
            "Processing at native resolution without resizing output might NOT lead to exactly "
            "the same resolution, due to the padding and pooling properties of convolution layers."
        )
    resample_method = args.resample_method

    # -------------------- Device --------------------
    if torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
        logging.warning("CUDA is not available. Running on CPU will be slow.")
    logging.info(f"Device = {device}")

    # -------------------- Data --------------------
    root_dir = Path(args.input_dir)

    # Collect all PNG/JPG images recursively
    test_images = sorted(
        list(root_dir.rglob("*.png")) + list(root_dir.rglob("*.jpg"))
    )
    total_images = len(test_images)

    if total_images == 0:
        logging.warning(f"No images found under {args.input_dir}. Exiting.")
        return

    print(f"==> Found {total_images} images in total. Processing all of them.")

    # -------------------- Model --------------------
    pipeline = IrisPipeline.from_pretrained(
        args.pretrained_model_name_or_path,
        torch_dtype=dtype,
    )
    logging.info(
        f"Successfully loaded IrisPipeline from {args.pretrained_model_name_or_path}."
    )
    logging.info(
        f"processing_res = {processing_res or pipeline.default_processing_resolution}"
    )

    pipeline = pipeline.to(device)
    pipeline.set_progress_bar_config(disable=True)

    if args.enable_xformers_memory_efficient_attention:
        try:
            pipeline.enable_xformers_memory_efficient_attention()
        except Exception as e:
            logging.warning(f"Could not enable xFormers: {e}")
            logging.warning("Continuing without xFormers...")

    # Random generator (for reproducibility if seed is set)
    if args.seed is None:
        generator = None
    else:
        generator = torch.Generator(device=device).manual_seed(args.seed)

    # -------------------- Inference and saving --------------------
    timesteps = [args.timestep_stage1, args.timestep_stage2]

    with torch.no_grad():
        for i in tqdm(range(len(test_images))):
            if torch.backends.mps.is_available():
                autocast_ctx = nullcontext()
            else:
                autocast_ctx = torch.autocast(pipeline.device.type)

            with autocast_ctx:
                # Preprocess input image
                img_path = test_images[i]
                test_image = Image.open(img_path).convert("RGB")
                test_image_np = np.array(test_image).astype(np.float32)
                test_image_tensor = torch.tensor(test_image_np).permute(2, 0, 1).unsqueeze(0)  # (1,3,H,W)
                test_image_tensor = test_image_tensor / 127.5 - 1.0  # [0,255] -> [-1,1]
                test_image_tensor = test_image_tensor.to(device)

                # Task embedding (default: depth -> [1, 0])
                task_emb = torch.tensor([1, 0]).float().unsqueeze(0).repeat(1, 1).to(device)
                task_emb = torch.cat([torch.sin(task_emb), torch.cos(task_emb)], dim=-1).repeat(1, 1)

                # Two-stage pipeline forward; return both final and intermediate outputs
                output_final, output_mid = pipeline(
                    rgb_in=test_image_tensor,
                    prompt="",
                    num_inference_steps=1,
                    generator=generator,
                    output_type="np",
                    timesteps=timesteps,
                    task_emb=task_emb,
                    processing_res=processing_res,
                    match_input_res=match_input_res,
                    resample_method=resample_method,
                    return_intermediates=True,
                    return_dict=True,
                )

                pred_stage2 = output_final.images[0]  # final stage
                pred_stage1 = output_mid.images[0]    # first stage

                # ---------------- Post-processing and saving ----------------
                save_file_name = os.path.basename(img_path)[:-4]

                if args.task_name == "depth":
                    # Stage 1
                    output_npy_stage1 = pred_stage1.mean(axis=-1)
                    output_color_stage1 = colorize_depth_map(
                        output_npy_stage1, reverse_color=args.disparity
                    )
                    # Stage 2
                    output_npy_stage2 = pred_stage2.mean(axis=-1)
                    output_color_stage2 = colorize_depth_map(
                        output_npy_stage2, reverse_color=args.disparity
                    )
                else:
                    # Normal prediction: direct RGB visualization
                    output_npy_stage1 = pred_stage1
                    output_color_stage1 = Image.fromarray(
                        (output_npy_stage1 * 255).astype(np.uint8)
                    )
                    output_npy_stage2 = pred_stage2
                    output_color_stage2 = Image.fromarray(
                        (output_npy_stage2 * 255).astype(np.uint8)
                    )

                # Save stage-1 results
                output_color_stage1.save(
                    os.path.join(output_dir_color_stage1, f"{save_file_name}.png")
                )
                np.save(
                    os.path.join(output_dir_npy_stage1, f"{save_file_name}.npy"),
                    output_npy_stage1,
                )

                # Save stage-2 results
                output_color_stage2.save(
                    os.path.join(output_dir_color_stage2, f"{save_file_name}.png")
                )
                np.save(
                    os.path.join(output_dir_npy_stage2, f"{save_file_name}.npy"),
                    output_npy_stage2,
                )

                # Create side-by-side comparison image (stage1 on the left, stage2 on the right)
                width1, height1 = output_color_stage1.size
                width2, height2 = output_color_stage2.size
                max_height = max(height1, height2)

                if height1 != max_height:
                    output_color_stage1 = output_color_stage1.resize((width1, max_height))
                if height2 != max_height:
                    output_color_stage2 = output_color_stage2.resize((width2, max_height))

                comparison_image = Image.new("RGB", (width1 + width2, max_height))
                comparison_image.paste(output_color_stage1, (0, 0))
                comparison_image.paste(output_color_stage2, (width1, 0))
                comparison_image.save(
                    os.path.join(output_dir_color_comparison, f"{save_file_name}.png")
                )

            # Optionally free CUDA cache to reduce memory fragmentation
            torch.cuda.empty_cache()

    print("==> Inference is done.")
    print(
        f"==> First stage results:  {output_dir_color_stage1}, {output_dir_npy_stage1}"
    )
    print(
        f"==> Second stage results: {output_dir_color_stage2}, {output_dir_npy_stage2}"
    )
    print(f"==> Comparison results:   {output_dir_color_comparison}")


if __name__ == "__main__":
    main()
