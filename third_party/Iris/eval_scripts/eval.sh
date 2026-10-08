export CUDA=0

export BASE_TEST_DATA_DIR="datasets/eval/"

export CHECKPOINT_DIR="trained_models/Iris"
export OUTPUT_DIR="output/Depth_D_Eval_Iris"
export TASK_NAME="depth"

CUDA_VISIBLE_DEVICES=$CUDA python eval.py \
        --pretrained_model_name_or_path=$CHECKPOINT_DIR \
        --prediction_type="sample" \
        --seed=42 \
        --half_precision \
        --base_test_data_dir=$BASE_TEST_DATA_DIR \
        --task_name=$TASK_NAME \
        --output_dir=$OUTPUT_DIR \
        --disparity
        # --rng_state_path=$RNG_STATE_PATH \
        # --hidiffusion
        # The defualt `processing_res` is set in the configuration file of each dataset. 
