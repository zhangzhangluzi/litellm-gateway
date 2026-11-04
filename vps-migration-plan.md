# LiteLLM 统一免费网关 — 迁到云 VPS 方案

> 基于本机 `/workspace` 现有配置（2026-11-03）。本机 Cloudflare trycloudflare 隧道不可用，公网入口改为 VPS。  
> **不要**在 VPS 上重新启用 Ollama / `qwen2.5-0.5b-local`。

---

## 1. 推荐技术栈

| 组件 | 选择 | 说明 |
|------|------|------|
| 运行时 | Docker Compose | 与官方 quickstart 一致，易备份/回滚 |
| 网关镜像 | `ghcr.io/berriai/litellm:v1.106.6`（钉死版本） | 官方镜像，亦有镜像 `docker.litellm.ai/berriai` |
| 配置 | 挂载 `litellm-config.yaml` | 与本机一致；密钥仅 `os.environ/...` |
| HTTPS | **Caddy 2**（首选）或 Nginx + certbot | 只暴露 80/443；4000 仅容器内网 |
| 密钥 | `.env`（`chmod 600`） | 从本机 `~/.bashrc` 与 `.litellm_master_key` 迁移**名字对应**的值 |
| 可选 DB | Postgres 16（compose profile `with-db`） | 虚拟密钥/用量统计；个人自用可不开 |
| 进程守护 | `restart: unless-stopped` + `systemctl enable docker` | 可选再包一层 systemd unit |

脚手架已写好：`/workspace/vps-deploy/`（可直接 scp 到 VPS `/opt/litellm`）。

官方文档（均已核对非 404）：
- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://docs.litellm.ai/docs/proxy/deploy
- https://docs.litellm.ai/docs/proxy/prod
- 官方 compose：https://github.com/BerriAI/litellm/blob/main/docker/docker-compose.quickstart.yml

---

## 2. 可借鉴的真实项目 / 教程（5 个）

| # | 标题 | URL | 为何有用 |
|---|------|-----|----------|
| 1 | BerriAI/litellm 官方仓库 + quickstart compose | https://github.com/BerriAI/litellm （compose：https://raw.githubusercontent.com/BerriAI/litellm/main/docker/docker-compose.quickstart.yml） | 官方镜像、MASTER/SALT key、Postgres 两件套；生产应以版本 tag 钉死 |
| 2 | VDS.Guru：LiteLLM on VPS with Docker + Postgres + Caddy（2026-10，Ubuntu 24.04） | https://vds.guru/blog/post/litellm-proxy-self-hosted-one-api-gateway-for-all-llms/ | 与本方案几乎同构：仅 Caddy 暴露 80/443、ufw、`.env`、健康检查、虚拟密钥示例 |
| 3 | ToolUpdate.jp：LiteLLM Proxy + Caddy + Docker 自托管指南 | https://toolupdate.jp/litellm-proxy-docker/ | 强调钉死镜像 tag、不暴露 4000、防火墙与生产检查项，可作对照清单 |
| 4 | hwdsl2/self-hosted-ai-stack（及 litellm-server 镜像文档） | https://github.com/hwdsl2/self-hosted-ai-stack · https://hub.docker.com/r/hwdsl2/litellm-server | 成套自托管 AI 栈；文档明确「公网必须反代 + 把 4000 绑到 127.0.0.1」 |
| 5 | aaronmorris-dev/litellm-langfuse-caddy | https://github.com/aaronmorris-dev/litellm-langfuse-caddy | LiteLLM + Caddy + 可观测性（Langfuse）组合，后续要加调用追踪可参考 |

> 注：检索到的 `mrwadams/llm-gateway` 在 2026-11-03 返回 **404**，已弃用，勿再引用。

### 备选网关（简要，不必换）

| 项目 | URL | 何时才考虑换 |
|------|-----|--------------|
| One API | https://github.com/songquanpeng/one-api | 中文社区成熟，偏「渠道/额度」运营；对「YAML 多供应商 fallback」不如 LiteLLM 直接 |
| New API（One API 继任向） | https://github.com/QuantumNous/new-api | 控制台/配额/订阅更强；AGPL；多用户计费场景 |
| Bifrost（Maxim，Go） | https://github.com/maximhq/bifrost | 超低延迟、单二进制；生态较新、供应商广度不如 LiteLLM |

对「多厂商免费 Key + 已有 litellm-config」：**继续 LiteLLM 最省事**，迁移成本最低。OpenRouter 自托管不适用（它是托管聚合，不是自建网关）。

---

## 3. VPS 区域

| 区域 | 到客户端 | 对 SiliconFlow | 对 Groq / OpenRouter / Cerebras | 建议 |
|------|----------|----------------|----------------------------------|------|
| **香港** | 通常最优（约 30–60 ms 量级，优质线路更好） | 友好 | 可接受 | **首选** |
| **新加坡** | 略远 | 一般可用 | 这些 API 稳定性常不错 | 次选 |
| **东京** | 中等 | 一般可用 | 到美西链路常见 | 均衡备选 |
| 美西 | 晚高峰易抖 | 绕路 | 到 Groq 等可能更近 | 不优先 |

纯代理几乎不吃 CPU： **1 vCPU / 1 GB** 通常够用；若开 Postgres + Admin UI，建议 **2 GB**。月费视商家约数美元～十余美元量级。

---

## 4. 从本机拷贝什么

| 本机路径 | VPS 目标 | 注意 |
|----------|----------|------|
| `/workspace/litellm-config.yaml` | `/opt/litellm/litellm-config.yaml` | 脚手架已复制；确认无 ollama |
| `/workspace/.litellm_master_key` | 写入 VPS `.env` 的 `LITELLM_MASTER_KEY=` | **不要**把文件明文提交到 Git |
| `~/.bashrc` 中的 `export XXX_API_KEY=...` | 写入 VPS `.env` 同名变量 | 只拷贝值到 VPS，本机可保留本地调试 |
| （可选）`litellm-usage.md` | 文档参考 | 客户端 Base URL 改为 https |
| **不要拷** | `litellm-venv/`、Ollama、cloudflared 作为公网方案 | VPS 用官方镜像即可 |

脚手架打包目录：`/workspace/vps-deploy/`（含 compose / Caddyfile / `.env.example` / README）。

---

## 5. `.env` 布局（仅变量名，无真实密钥）

见 `/workspace/vps-deploy/.env.example`：

```bash
DOMAIN=llm.example.com
LITELLM_MASTER_KEY=
LITELLM_SALT_KEY=          # 仅 with-db 时需要；设定后勿随意更换
POSTGRES_PASSWORD=         # 仅 with-db

GROQ_API_KEY=
OPENROUTER_API_KEY=
GEMINI_API_KEY=
ZAI_API_KEY=
UPSTAGE_API_KEY=
COHERE_API_KEY=
AI_GATEWAY_API_KEY=
CEREBRAS_API_KEY=
NVIDIA_API_KEY=
MISTRAL_API_KEY=
SILICONFLOW_API_KEY=
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ACCOUNT_ID=
# CLOUDFLARE_API_BASE 由 docker-compose 根据 ACCOUNT_ID 自动拼接
```

生成主密钥示例：`echo "sk-$(openssl rand -hex 32)"`。为减少客户端改动，可直接沿用本机 master key。

---

## 6. 分步迁移

### A. 准备 VPS

1. 购买 Ubuntu 22.04/24.04，区域优先香港。
2. DNS：`llm.你的域名` A/AAAA → VPS IP（先解析再启 Caddy）。
3. 安装 Docker、开防火墙（仅 22/80/443）：

```bash
sudo apt update && sudo apt upgrade -y
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable
curl -fsSL https://get.docker.com | sudo sh
```

### B. 上传并配置

```bash
# 在本机（示例）
scp -r /workspace/vps-deploy root@VPS_IP:/opt/litellm
ssh root@VPS_IP
cd /opt/litellm
cp .env.example .env && chmod 600 .env
# 编辑 DOMAIN、LITELLM_MASTER_KEY、各家 KEY、CLOUDFLARE_*
nano .env
```

### C. 启动与健康检查

```bash
docker compose up -d
docker compose ps
docker compose logs -f litellm
# 另开终端：
source .env
curl -sS "https://$DOMAIN/health/liveliness"
curl -sS "https://$DOMAIN/v1/models" -H "Authorization: Bearer $LITELLM_MASTER_KEY"
# 确认无 qwen2.5-0.5b-local，并有 free-auto / free-fast / free-cn
```

### D. 防火墙与端口原则

- **允许**：22、80、443  
- **禁止对外**：4000、5432、以及任何 Docker 误映射的内部端口  
- 注意：Docker 发布的端口会绕过 ufw——因此 compose **不要**写 `4000:4000`，只让 Caddy 进公网（脚手架已如此）

### E. 重启策略

- 容器：`restart: unless-stopped`
- 主机：`sudo systemctl enable --now docker`
- 可选：见 `vps-deploy/README.md` 中的 `litellm-stack.service`

### F. 客户端切换（Cutover）

1. **OpenCode**：改 `~/.config/opencode/opencode.json` →  
   `provider.litellm.options.baseURL` = `https://域名/v1`（`apiKey` 仍可用 `{env:LITELLM_MASTER_KEY}`）
2. **Aider**：`export OPENAI_API_BASE=https://域名/v1` + `OPENAI_API_KEY=$LITELLM_MASTER_KEY`
3. **Cursor / Cherry Studio 等**：Override Base URL 填 `https://域名/v1`，Key 填 master key
4. 本机可暂时保留 `litellm-start.sh` 作离线备份；确认公网稳定后再停本机进程

---

## 7. 安全要点

1. **Master key**：等同 root；只给自己的客户端。多人/多应用时开 Postgres，发 **virtual key**（预算/RPM）。
2. **永不暴露 `.env`**：权限 600；不进 Git；备份加密存放。
3. **Rate limit**：LiteLLM 可对 virtual key 设 `rpm_limit` / `max_budget`；单机 master-key 模式可在 Caddy 层限制或仅自用。
4. **可选 IP 白名单**（Caddy 示例）：

```caddyfile
{$DOMAIN} {
    @denied not remote_ip 1.2.3.4/32 5.6.7.8/32
    respond @denied 403
    reverse_proxy litellm:4000 { flush_interval -1 }
}
```

5. 镜像钉版本；升级前先改 tag 再 `compose up -d`，并看 release notes。
6. 定期轮换：怀疑泄露立即换 `LITELLM_MASTER_KEY` 并更新所有客户端；`LITELLM_SALT_KEY` 在写入 DB 后**不能随便改**。

---

## 8. Cutover 前检查清单

- [ ] DNS 已指向 VPS，`dig +short 域名` 正确
- [ ] `.env` 已填 DOMAIN、MASTER_KEY、各 PROVIDER、CLOUDFLARE_ACCOUNT_ID
- [ ] `litellm-config.yaml` 无 ollama / qwen2.5-0.5b-local
- [ ] ufw 仅 22/80/443；compose 未发布 4000
- [ ] `docker compose up -d` 后 `health/liveliness` 正常
- [ ] `/v1/models` 返回 16 个预期别名（与本机一致）
- [ ] 用 `free-auto` / `free-cn` 各打一发 chat 成功
- [ ] 已备份 `.env` 到密码管理器

## 9. Cutover 后检查清单

- [ ] OpenCode / Aider / Cursor 已改 Base URL，旧本地 URL 无残留
- [ ] 公网 HTTPS 证书有效（浏览器锁头 / `curl -vI`）
- [ ] 重启 VPS 后栈自动恢复（`docker compose ps`）
- [ ] 本机网关可停：`/workspace/litellm-stop.sh`（确认无依赖本地 4000 的任务）
- [ ] （可选）设置日志轮转 / 磁盘监控；考虑开启 virtual keys

---

## 10. 费用粗估

| 项目 | 粗算 |
|------|------|
| VPS 1C1G（HK/SG/JP） | 常见约 \$3–\$8 / 月（视商家与线路） |
| VPS 2C2G（若开 DB/UI） | 常见约 \$6–\$15 / 月 |
| 域名 | 已有可忽略；新购按注册商 |
| LLM 调用 | 继续用各厂免费/现有额度；网关本身无加价 |
| 证书 | Caddy + Let’s Encrypt 免费 |

代理几乎不推理，**瓶颈在出站到各厂商 API 的网络**，故机房选择比 CPU 更重要。

---

## 11. 与本机差异对照

| 项 | 本机 box | VPS 目标 |
|----|----------|----------|
| 进程 | venv + nohup 脚本 | Docker 官方镜像 |
| 入口 | `0.0.0.0:4000`（无可靠公网） | `https://域名` → Caddy → :4000 |
| 密钥 | `~/.bashrc` + `.litellm_master_key` | `.env` |
| 本地模型 | 已移除，保持移除 | 不装 Ollama |
| 隧道 | cloudflared trycloudflare 失败 | 不再依赖 CF 隧道 |

下一步：按 `/workspace/vps-deploy/README.md` 在新 VPS 上执行即可。
