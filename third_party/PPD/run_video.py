import argparse
import cv2
import glob
import matplotlib
import numpy as np
import os
import torch
import torch.nn.functional as F
from ppd.utils.set_seed import set_seed
from ppd.models.ppvd import PixelPerfectVideoDepth
from ppd.utils.video_utils import read_video_frames, save_video
    

if __name__ == '__main__':
    set_seed(666) # set random seed
    parser = argparse.ArgumentParser(description='Pixel-Perfect Video Depth')
    parser.add_argument('--video_path', type=str, default='assets/examples/video/0001.mp4')
    parser.add_argument('--input_size', type=int, default=[512, 512])
    parser.add_argument('--outdir', type=str, default='depth_video_vis')
    parser.add_argument('--semantics_model', type=str, default='Pi3', choices=['Pi3'])
    parser.add_argument('--sampling_steps', type=int, default=4)
    parser.add_argument('--grayscale', action='store_true', help='do not apply colorful palette')
    parser.add_argument('--save_npz', action='store_true', help='save depths as npz')

    args = parser.parse_args()

    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu')

    semantics_pth = 'checkpoints/pi3.safetensors'
    model_pth = 'checkpoints/ppvd.pth'

    model = PixelPerfectVideoDepth(semantics_model=args.semantics_model, semantics_pth=semantics_pth, sampling_steps=args.sampling_steps)
    model.load_state_dict(torch.load(model_pth, map_location='cpu'), strict=False)

    model = model.to(DEVICE).eval()

    frames, fps = read_video_frames(args.video_path)
    depths = model.infer_video(frames)

    video_name = os.path.basename(args.video_path)
    if not os.path.exists(args.outdir):
        os.makedirs(args.outdir)

    processed_video_path = os.path.join(args.outdir, os.path.splitext(video_name)[0]+'_src.mp4')
    depth_vis_path = os.path.join(args.outdir, os.path.splitext(video_name)[0]+'_vis.mp4')
    save_video(frames, processed_video_path, fps=fps)
    save_video(depths, depth_vis_path, fps=fps, is_depths=True, grayscale=args.grayscale)

    if args.save_npz:
        depth_npz_path = os.path.join(args.outdir, os.path.splitext(video_name)[0]+'_depths.npz')
        np.savez_compressed(depth_npz_path, depths=depths)
