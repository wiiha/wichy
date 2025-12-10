#!/bin/zsh

LLAMA_SERVER_BIN="llama_server_stuff/llama-server"

# Define available models and their default settings
LLM_MODEL_GRANITE="llama_server_stuff/granite-4.0-h-tiny-Q4_K_M.gguf"
GRANITE_DEFAULTS=(--temp 0.0 --ctx-size 16384 --top-k 0 --top-p 1.0 --jinja)

LLM_MODEL_QWEN_THINKING="llama_server_stuff/Qwen3-VL-8B-Thinking-Q4_K_M.gguf"
QWEN3_VL_8B_THINKING_DEFAULTS=(
    --mmproj "llama_server_stuff/Qwen3-mmproj-F16.gguf"
    --jinja
    --top-p 0.95
    --top-k 20
    --temp 1.0
    --min-p 0.0
    --flash-attn on
    --presence-penalty 0.0
    --ctx-size 8192
)

LLM_MODEL_QWEN_INSTRUCT="llama_server_stuff/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
QWEN3_VL_8B_INSTRUCT_DEFAULTS=(
    # --mmproj "llama_server_stuff/Qwen3-mmproj-F16.gguf"
    --jinja
    --top-p 0.8
    --top-k 20
    --temp 0.7
    --min-p 0.0
    --flash-attn on
    --presence-penalty 1.5
    --ctx-size 16384
)

LLM_MODEL_GEMMA_3_12B="llama_server_stuff/gemma-3-4b-it-UD-Q4_K_XL.gguf"
GEMMA_3_12B=(
    --ctx-size 16384
    --temp 1.0
    --repeat-penalty 1.0
    --min-p 0.01
    --top-k 64
    --top-p 0.95
    --jinja
)

# Function to set model and defaults based on flag
set_model_and_defaults() {
    case "$1" in
        "granite")
            MODEL="$LLM_MODEL_GRANITE"
            DEFAULTS=("${GRANITE_DEFAULTS[@]}")
            ;;
        "qwen-thinking")
            MODEL="$LLM_MODEL_QWEN_THINKING"
            DEFAULTS=("${QWEN3_VL_8B_THINKING_DEFAULTS[@]}")
            ;;
        "qwen-instruct")
            MODEL="$LLM_MODEL_QWEN_INSTRUCT"
            DEFAULTS=("${QWEN3_VL_8B_INSTRUCT_DEFAULTS[@]}")
            ;;
        "gemma-3-12b")
            MODEL="$LLM_MODEL_GEMMA_3_12B"
            DEFAULTS=("${GEMMA_3_12B[@]}")
            ;;
        *)
            echo "Invalid model flag. Available options: granite, qwen-thinking, qwen-instruct, gemma-3-12b" >&2
            exit 1
            ;;
    esac
}

# Check if a model flag was provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <model-flag>"
    echo "Available models: granite, qwen-thinking, qwen-instruct, gemma-3-12b"
    exit 1
fi

# Set the model and its default settings
set_model_and_defaults "$1"

# Validate that the model file exists
if [ ! -f "$MODEL" ]; then
    echo "Error: Model file '$MODEL' not found." >&2
    exit 1
fi

# Validate that the llama server executable exists
if [ ! -x "$LLAMA_SERVER_BIN" ]; then
    echo "Error: Llama server executable '$LLAMA_SERVER_BIN' not found or not executable." >&2
    exit 1
fi

# Log what we're doing
echo "Starting llama server with model: $MODEL"
echo "With parameters: ${DEFAULTS[@]}"

# Execute the llama server with the specified model and defaults
exec "$LLAMA_SERVER_BIN" --model "$MODEL" "${DEFAULTS[@]}"