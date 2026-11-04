# LiteLLM 统一网关 — Ubuntu VPS 部署脚手架

用 Docker Compose 在 Ubuntu VPS 上跑 [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/deploy)，对外只提供一个 OpenAI 兼容入口：

**Base URL：`https://llm.<domain>/v1`**

`<domain>` 换成你自己的域名（`.env` 里的 `DOMAIN`，示例 `llm.example.com`）。客户端 API Key 使用 `LITELLM_MASTER_KEY`。模型走已配置的云端 API；本脚手架不包含 Ollama，配置里也没有 `qwen2.5-0.5b-local`。

仓库只提交占位符。真实密钥写在 VPS 上的 `.env`（`chmod 600`），该文件已被 `.gitignore` 排除。

## 模型与供应商评级

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

### 怎么选

- 默认用 `free-auto`。
- 要质量：`deepseek-v3-siliconflow` 或 `gemini-3.5-flash`。
- 要速度：Groq / Cerebras 的 `gpt-oss-120b-*`。
- 国内优先：`free-cn`。
- 没有哪一家是无限强；额度用完就换一个模型名再试。

### 供应商小结

| 供应商 | 评价 |
|---|---|
| 硅基流动 | 质量最好的免费来源，有国内线路 |
| Google AI Studio | 综合强、稳，有速率限制 |
| Groq / Cerebras | 速度最快，注意每日额度和赠金 |
| OpenRouter / NVIDIA | 免费模型多，但容易限流 |
| Cloudflare Workers AI | 备用，按 Neurons 计额度 |
| 智谱 / Upstage / Cohere / Mistral | 质量或额度一般，当补充 |

## 两条部署路径

| 场景 | 命令 | 公网入口 |
|------|------|----------|
| 80/443 空闲（默认） | `docker compose up -d` | Caddy 自动 HTTPS，反代到容器内 `litellm:4000` |
| 主机 443（或 80）已被占用 | `docker compose -f docker-compose.tunnel.yml up -d` | Cloudflare Tunnel 指到 `http://127.0.0.1:4000` |

两条路径的客户端地址相同，都是 `https://llm.<domain>/v1`。LiteLLM 的 4000 不对 `0.0.0.0` 发布。

官方参考：

- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://docs.litellm.ai/docs/proxy/deploy
- https://docs.litellm.ai/docs/proxy/prod

## 规格、磁盘与日志

| 项 | 建议 |
|----|------|
| 规格 | 纯代理 **1 vCPU / 1 GB RAM** 通常够用；启用 Postgres / Admin UI 时用 **2 GB** |
| 磁盘 | **20 GB** 足够。代理不在本机推理，不要为 Ollama 预留模型盘 |
| 容器日志 | 两个 compose 都使用 `json-file`，`max-size: 10m`，`max-file: 3`（单服务约 30 MB 封顶） |
| 镜像 | 钉死 `ghcr.io/berriai/litellm:v1.106.6` 与 `caddy:2.9-alpine`，不用 `:latest` |
| 部署后清理 | `docker image prune -f`，以及 `sudo apt clean` |

机房：兼顾国内 SiliconFlow 与海外 Groq / OpenRouter 时，优先香港，其次新加坡或东京。

## 前置条件

1. Ubuntu 22.04 或 24.04，可以 SSH。
2. 默认（Caddy）路径：`llm.<domain>` 的 A/AAAA 已经指向这台 VPS，80/443 可从公网访问。
3. Tunnel 路径：Cloudflare 上已有 Tunnel，并把主机名 `llm.<domain>` 转到本机 `http://127.0.0.1:4000`。此时不要再启动脚手架里的 Caddy，避免抢 80/443。
4. 各家 API Key 放在密码管理器里，部署时写入 `.env`。配置文件只通过 `os.environ/...` 引用。

## 默认部署（Docker + Caddy）

```bash
# 1) 防火墙只放行 SSH、80、443
sudo apt update && sudo apt upgrade -y
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# 2) 安装 Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# 重新登录后再继续，或：newgrp docker

# 3) 把本仓库放到 VPS，例如 /opt/litellm
sudo mkdir -p /opt/litellm
sudo chown "$USER:$USER" /opt/litellm
git clone https://github.com/zhangzhangluzi/litellm-gateway.git /opt/litellm
cd /opt/litellm

# 4) 填写密钥（只存在这台机器上）
cp .env.example .env
chmod 600 .env
nano .env
# 必填：DOMAIN、LITELLM_MASTER_KEY，以及你实际要用的 PROVIDER_* / CLOUDFLARE_*
# 生成主密钥：
#   echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 32)"

# 5) 启动
docker compose up -d
docker compose ps
docker compose logs -f litellm
```

Compose 已设置 `restart: unless-stopped`。让 Docker 开机自启：

```bash
sudo systemctl enable --now docker
```

可选的 systemd unit（用 systemctl 管整栈）：

```ini
# /etc/systemd/system/litellm-stack.service
[Unit]
Description=LiteLLM + Caddy stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/litellm
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-stack
```

## 443 被占用时（Cloudflare Tunnel）

主机上已有反代或别的服务占用 80/443 时，用 tunnel compose。它只把 4000 绑在回环地址：

```bash
cd /opt/litellm
cp .env.example .env
chmod 600 .env
nano .env   # DOMAIN 仍写成 llm.<domain>，供文档和客户端对齐；本文件不启动 Caddy
docker compose -f docker-compose.tunnel.yml up -d
```

在 Cloudflare Zero Trust 里把 public hostname `llm.<domain>` 指到 `http://127.0.0.1:4000`。若流量经 Cloudflare 代理，关闭该主机名的 Browser Integrity Check，避免 Python / SDK 收到 1010。

客户端 Base URL 仍是 `https://llm.<domain>/v1`。

也可以不建 Tunnel，把现有反代 `reverse_proxy` 到 `127.0.0.1:4000`。Nginx 片段见 `nginx-snippet.conf`（务必 `proxy_buffering off`）。

## 健康检查

`.env` 里的 `DOMAIN` 写成完整主机名（`llm.example.com`），不带协议，也不带 `/v1`。客户端 Base URL 是 `https://$DOMAIN/v1`。

```bash
set -a
source .env
set +a
curl -sS "https://$DOMAIN/health/liveliness"
curl -sS "https://$DOMAIN/v1/models" -H "Authorization: Bearer $LITELLM_MASTER_KEY"
# 列表里应有 free-auto / free-fast / free-cn，且没有 qwen2.5-0.5b-local

curl -sS "https://$DOMAIN/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"free-auto","messages":[{"role":"user","content":"ping"}]}'
```

## 客户端

| 客户端 | 改法 |
|--------|------|
| OpenAI SDK / curl | Base URL = `https://llm.<domain>/v1`，Key = `LITELLM_MASTER_KEY` |
| OpenCode | `provider.litellm.options.baseURL` = `https://llm.<domain>/v1` |
| Aider | `OPENAI_API_BASE=https://llm.<domain>/v1`，`OPENAI_API_KEY` 为同一把主密钥 |
| Cursor | Override OpenAI Base URL 填 `https://llm.<domain>/v1`（云端访问不到 `127.0.0.1`） |

模型名保持不变：`free-auto`、`free-fast`、`free-cn`，以及各供应商别名。别名对照见 `litellm-usage.md`。

## 安全

- `.env` 权限 `600`，只留在 VPS 上。提交前确认工作区没有 `.env`、密钥文件或 token。
- 防火墙放行 22/80/443。Docker 发布的端口会绕过 ufw，所以 compose 里不要写 `4000:4000` 这种对所有网卡的映射。
- 主密钥只发给自己的客户端。多人使用时启用 `with-db` profile，改发 virtual key。
- 可选 IP 白名单写在 `vps-migration-plan.md`。
- 升级时先改 compose 里的镜像 tag，再 `docker compose pull && docker compose up -d`。

更短的清单见 `SECURITY.md`。

## 可选：Postgres（虚拟密钥 / 用量）

个人自用、只靠配置文件时可以不启用。需要数据库时，在 `.env` 里填写 `LITELLM_SALT_KEY` 和 `POSTGRES_PASSWORD`（compose 没有默认口令），并在 `litellm` 服务的 `environment` 中增加：

```yaml
DATABASE_URL: postgresql://litellm:${POSTGRES_PASSWORD}@db:5432/litellm
STORE_MODEL_IN_DB: "True"
```

然后：

```bash
docker compose --profile with-db up -d
```

`LITELLM_SALT_KEY` 一旦用来加密库内凭据，就不要再改。

## 目录

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | LiteLLM + Caddy；可选 `with-db` |
| `docker-compose.tunnel.yml` | 443 被占用时，只在 `127.0.0.1:4000` 起 LiteLLM |
| `Caddyfile` | 自动 HTTPS，流式响应 `flush_interval -1` |
| `nginx-snippet.conf` | Nginx 备选片段 |
| `litellm-config.yaml` | 模型与路由；密钥仅为 `os.environ/...`；无 Ollama |
| `.env.example` | 变量名与空占位符 |
| `litellm-usage.md` | Base URL、别名与调用示例 |
| `vps-migration-plan.md` | 从工作站迁到 VPS 的步骤与检查清单 |
| `SECURITY.md` | 发布与运行时不要泄漏密钥 |

## 常见问题

- **证书申请失败**：DNS 是否已经指向本机，80/443 是否可达，`DOMAIN` 是否为完整主机名。
- **Cloudflare Workers AI 404**：`.env` 需要 `CLOUDFLARE_ACCOUNT_ID`。Compose 会把它拼进 `CLOUDFLARE_API_BASE`。
- **流式输出卡住**：Caddy 已设 `flush_interval -1`。改用 Nginx 时保持 `proxy_buffering off`。
- **磁盘涨得快**：确认日志驱动仍是 10m × 3，然后 `docker image prune -f`。不要在这台 VPS 上安装 Ollama 或拉取本地模型。
