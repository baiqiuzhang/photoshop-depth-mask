
export CUDA=0

export CHECKPOINT_DIR="trained_models/Iris"
export OUTPUT_DIR="output/Depth_Infer"
export TASK_NAME="depth"

export TEST_IMAGES="assets/in-the-wild_example"

CUDA_VISIBLE_DEVICES=$CUDA python infer.py \
        --pretrained_model_name_or_path=$CHECKPOINT_DIR \
        --prediction_type="sample" \
        --seed=42 \
        --half_precision \
        --input_dir=$TEST_IMAGES \
        --task_name=$TASK_NAME \
        --output_dir=$OUTPUT_DIR \
        --disparity \
        --processing_res=0