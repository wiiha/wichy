#!/bin/zsh

LLAMA_SERVER_BIN="llama_server_stuff/llama-server"

LLM_MODEL_GRANITE="llama_server_stuff/granite-4.0-h-tiny-Q4_K_M.gguf"
GRANITE_DEFAULTS=(--temp 0.0 --ctx-size 16384 --top-k 0 --top-p 1.0 --jinja)

LLM_MODEL_QWEN_THINKING="llama_server_stuff/Qwen3-VL-8B-Thinking-Q4_K_M.gguf"
QWEN3_VL_8B_THINKING_DEFAULTS=(
    --mmproj "llama_server_stuff/Qwen3-mmproj-F16.gguf" \
    --jinja \
    --top-p 0.95 \
    --top-k 20 \
    --temp 1.0 \
    --min-p 0.0 \
    --flash-attn on \
    --presence-penalty 0.0 \
    --ctx-size 8192
)

LLM_MODEL_QWEN_INSTRUCT="llama_server_stuff/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
QWEN3_VL_8B_INSTRUCT_DEFAULTS=(
    # --mmproj "llama_server_stuff/Qwen3-mmproj-F16.gguf" \
    --jinja \
    --top-p 0.8 \
    --top-k 20 \
    --temp 0.7 \
    --min-p 0.0 \
    --flash-attn on \
    --presence-penalty 1.5 \
    --ctx-size 16384
)

LLM_MODEL_GEMMA_3_12B="llama_server_stuff/gemma-3-4b-it-UD-Q4_K_XL.gguf"
GEMMA_3_12B=(
    --ctx-size 16384 \
    --temp 1.0 \
    --repeat-penalty 1.0 \
    --min-p 0.01 \
    --top-k 64 \
    --top-p 0.95
    --jinja \
)

exec "$LLAMA_SERVER_BIN" --model "$LLM_MODEL_QWEN_INSTRUCT" "${QWEN3_VL_8B_INSTRUCT_DEFAULTS[@]}"