# Security

本仓库是部署脚手架，只允许出现变量名和空占位符。

- 不要提交 `.env`、API key、master key、数据库口令、Cloudflare token，或任何 `*.pem` / `*.key`。真实值只放在 VPS 的 `.env`，并 `chmod 600`。
- `.env.example` 中的值为空。生成主密钥用 `openssl rand -hex 32`，前面加上 `sk-`。
- 默认 compose 只发布 80/443。Tunnel compose 把 4000 绑在 `127.0.0.1`。不要把 4000 或 5432 映射到 `0.0.0.0`。
- `with-db` profile 不带默认 Postgres 口令。启用前设置 `POSTGRES_PASSWORD`。`LITELLM_SALT_KEY` 写入数据库后保持不变。
- 模型全部走云端 API。不要在 VPS 上加 Ollama 或把本地模型写回 `litellm-config.yaml`。
- 若密钥曾经进入 git 历史，立刻在对应厂商处轮换，并清理历史。Issue 里不要粘贴 `.env`。
