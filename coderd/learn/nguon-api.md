# Nguồn API

Coderd gọi `POST {MODEL_BASE_URL}/chat/completions`. Đổi nguồn bằng ba dòng trong `/opt/coderd/worker.env`, rồi `systemctl restart coderd-worker`.

Đang chạy: llamafile trên VPS, `MODEL_BASE_URL=http://127.0.0.1:11434/v1`, `MODEL_NAME=coderd`, không khóa. Model là Qwen2.5-Coder 0.5B.

## Chạy trên máy mình, không khóa

- Ollama: `http://127.0.0.1:11434/v1`
- LM Studio: `http://127.0.0.1:1234/v1`
- vLLM hoặc SGLang: `http://127.0.0.1:8000/v1`
- llama.cpp: `http://127.0.0.1:8080/v1`
- LiteLLM: `http://127.0.0.1:4000/v1`

Ollama dùng cùng cổng 11434 với llamafile. Trên VPS này chỉ chạy một trong hai.

## API đám mây, cần khóa của người dùng

Điền khóa vào `MODEL_API_KEY`. `MODEL_NAME` lấy ở catalog của từng nơi.

- OpenAI: `https://api.openai.com/v1`
- OpenRouter: `https://openrouter.ai/api/v1`
- Groq: `https://api.groq.com/openai/v1`
- Together: `https://api.together.xyz/v1`
- Fireworks: `https://api.fireworks.ai/inference/v1`
- DeepSeek: `https://api.deepseek.com/v1`
- Mistral: `https://api.mistral.ai/v1`
- Cerebras: `https://api.cerebras.ai/v1`
- SambaNova: `https://api.sambanova.ai/v1`
- xAI: `https://api.x.ai/v1`
- Gemini: `https://generativelanguage.googleapis.com/v1beta/openai`
- Cohere: `https://api.cohere.com/compatibility/v1`
- NVIDIA NIM: `https://integrate.api.nvidia.com/v1`
- Hugging Face: `https://router.huggingface.co/v1`
- GitHub Models: `https://models.github.ai/inference`
- DeepInfra: `https://api.deepinfra.com/v1/openai`
- Qwen DashScope: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Moonshot: `https://api.moonshot.ai/v1`
- MiniMax: `https://api.minimax.io/v1`
- Z.ai: `https://api.z.ai/api/paas/v4`
- SiliconFlow: `https://api.siliconflow.com/v1`
- Perplexity: `https://api.perplexity.ai`
- Azure OpenAI: `https://TEN.openai.azure.com/openai/v1`

Anthropic không dùng đường này. Coderd chưa gọi API Messages của Anthropic.
