#### pretrain on the Hypersim dataset at 512x512 resolution
python main.py --cfg_file ppd/configs/train_pretrain.yaml pl_trainer.devices=8

# #### finetune on five mixed datasets at 1024x768 resolution
# python main.py --cfg_file ppd/configs/train_finetune.yaml pl_trainer.devices=8
