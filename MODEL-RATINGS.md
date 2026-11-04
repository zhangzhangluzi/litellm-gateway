# 模型与供应商评级

按「好不好用」做的主观粗评（免费档），主要看代码能力、中文、稳定性和额度。评级会随各家免费政策变化，仅供选型参考。
评分日期：2026-11-04。网关模型名与 `litellm-config.yaml` 一致。

| 评级 | 网关模型名 | 来源 | 大致水平 | 适合场景 |
|---|---|---|---|---|
| S | `deepseek-v3-siliconflow` | 硅基流动 | 接近强开源顶档，中文和代码都强 | 认真写代码、长回答 |
| A | `gemini-3.5-flash` | Google | 快、稳、综合强 | 日常问答、总结 |
| A | `gpt-oss-120b-groq` / `gpt-oss-120b-cerebras` | Groq / Cerebras | 大模型、极速；Cerebras 有赠金上限 | 要速度的推理和代码 |
| A- | `free-auto` | 多家轮换 | 质量随后端变化，胜在不容易撞额度 | 默认首选 |
| B+ | `nemotron-openrouter` / `nemotron-nvidia` | OpenRouter / NVIDIA | 开源里不错，免费额度偏紧 | 备用 |
| B | `glm-4.5-flash`、`free-cn` | 智谱 / 硅基池 | 中文友好，Flash 偏轻 | 国内访问、轻任务 |
| B | `qwen2.5-7b-siliconflow` | 硅基流动 | 7B，能聊但不适合当主力 | 短问答 |
| B- | `llama-3.3-70b-cloudflare` | Cloudflare | 70B 可用，Neurons 额度有限 | 备用 |
| C+ | `solar-mini`、`command-a` | Upstage / Cohere | 一般，偏赠金或试用 | 凑数 |
| C | `mistral-small`、`gemma-4-31b-openrouter` | Mistral / OpenRouter | 容易 429 或偏弱 | 少用 |

## 怎么选

- 默认用 `free-auto`。
- 要质量：`deepseek-v3-siliconflow` 或 `gemini-3.5-flash`。
- 要速度：Groq / Cerebras 的 `gpt-oss-120b-*`。
- 国内优先：`free-cn`。
- 没有哪一家是无限强；额度用完就换一个模型名再试。

## 供应商小结

| 供应商 | 评价 |
|---|---|
| 硅基流动 | 质量最好的免费来源，有国内线路 |
| Google AI Studio | 综合强、稳，有速率限制 |
| Groq / Cerebras | 速度最快，注意每日额度和赠金 |
| OpenRouter / NVIDIA | 免费模型多，但容易限流 |
| Cloudflare Workers AI | 备用，按 Neurons 计额度 |
| 智谱 / Upstage / Cohere / Mistral | 质量或额度一般，当补充 |
