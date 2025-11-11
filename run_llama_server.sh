#!/bin/zsh

LLAMA_SERVER_BIN="llama_server_stuff/llama-server"
LLM_MODEL_GRANITE="llama_server_stuff/granite-4.0-h-tiny-Q4_K_M.gguf"
GRANITE_DEFAULTS=(--temp 0.0 --ctx-size 16384 --top-k 0 --top-p 1.0 --jinja)

exec "$LLAMA_SERVER_BIN" --model "$LLM_MODEL_GRANITE" "${GRANITE_DEFAULTS[@]}"