# LiteLLM 统一网关使用说明

一个 Base URL + 一个 Key，接入已配置的云端模型（OpenAI 兼容接口）。下面的主机名 `llm.example.com` 只是占位符，与 `.env.example` 一致。部署后把客户端 Base URL 设为 **`https://llm.<domain>/v1`**。仓库里没有真实密钥。

## 公网 Base URL

- **Base URL**: `https://llm.<domain>/v1`（示例 `https://llm.example.com/v1`）
- **Health**: `https://llm.<domain>/health/liveliness` → `"I'm alive!"`
- **API Key**: VPS 上的 `LITELLM_MASTER_KEY`（`.env`，权限 600）。工作站上若仍保留本地文件，只在本机读取，不要把内容提交到 Git
- 默认架构：Caddy（80/443）→ `litellm:4000`。主机 443 已被占用时改用 Cloudflare Tunnel → `127.0.0.1:4000`，见 `docker-compose.tunnel.yml`
- 部署目录示例：VPS `/opt/litellm`
- 无 Ollama / 无 `qwen2.5-0.5b-local`

### 远程客户端示例

**curl — 列模型**
```bash
export LITELLM_BASE=https://llm.example.com/v1   # 换成 https://llm.<domain>/v1
# 从密码管理器或 VPS 的 .env 导出 LITELLM_MASTER_KEY，不要把值写进仓库
curl -sS "$LITELLM_BASE/models" -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.data[].id'
```

**curl — 聊天**
```bash
curl -sS "$LITELLM_BASE/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"free-auto","messages":[{"role":"user","content":"ping"}]}'
```

**Python (OpenAI SDK)**
```python
import os
from openai import OpenAI
client = OpenAI(
    base_url="https://llm.example.com/v1",  # https://llm.<domain>/v1
    api_key=os.environ["LITELLM_MASTER_KEY"],
)
print(client.models.list())
r = client.chat.completions.create(
    model="free-auto",
    messages=[{"role": "user", "content": "ping"}],
)
print(r.choices[0].message.content)
```

**OpenCode** — `~/.config/opencode/opencode.json` 中：
```json
{
  "provider": {
    "litellm": {
      "options": {
        "baseURL": "https://llm.example.com/v1"
      }
    }
  }
}
```
（Key 仍用原 `LITELLM_MASTER_KEY` / 配置里已有的密钥字段。）

常用模型别名：`free-auto` / `free-fast` / `free-cn`，以及各后端别名（groq / cerebras / gemini / siliconflow 等）。

---

## 本机（Cursor box）启动 / 停止
```bash
/workspace/litellm-start.sh     # 后台启动（nohup），监听 0.0.0.0:4000
/workspace/litellm-stop.sh      # 停止
tail -f /workspace/litellm.log  # 查看日志
```
- 配置文件：`/workspace/litellm-config.yaml`（只通过 `os.environ/XXX` 引用 Key，不含明文）
- 虚拟环境：`/workspace/litellm-venv`
- 启动脚本会从 `~/.bashrc` 加载各家 API Key，读取主密钥，并用 `CLOUDFLARE_ACCOUNT_ID` 拼出 Cloudflare 的 `CLOUDFLARE_API_BASE`
- 机器重启后不会自动启动，需要再运行一次 start 脚本

## Base URL 对照
| 环境 | Base URL |
|---|---|
| 本机 | `http://127.0.0.1:4000/v1` |
| **公网 VPS** | **`https://llm.example.com/v1`** |

## 主密钥（API Key）

VPS 上主密钥只写在 `/opt/litellm/.env` 的 `LITELLM_MASTER_KEY`（`chmod 600`）。工作站若另有本地文件，权限同样设为 600，并且不要进入 Git。

## 模型别名
| 别名 | 实际后端 |
|---|---|
| `free-auto` | 负载均衡：Groq gpt-oss-120b / Cerebras gpt-oss-120b / OpenRouter Nemotron / Gemini 3.5 Flash；全部失败时依次降级到 mistral-small → deepseek-v3 → glm-4.5-flash |
| `free-fast` | Groq + Cerebras 的 gpt-oss-120b（最快），失败时降级到 free-auto |
| `free-cn` | SiliconFlow DeepSeek-V3 + 智谱 GLM-4.5-Flash，失败时降级到 free-auto |
| `gpt-oss-120b-groq` | Groq openai/gpt-oss-120b |
| `gpt-oss-120b-cerebras` | Cerebras gpt-oss-120b |
| `gemini-3.5-flash` | Google Gemini 3.5 Flash |
| `nemotron-openrouter` | OpenRouter nvidia/nemotron-3.5-lightning:free |
| `gemma-4-31b-openrouter` | OpenRouter google/gemma-4-31b-it:free（经常被限流，限流时降级） |
| `nemotron-nvidia` | NVIDIA NIM nemotron-3.5-lightning-30b-a3b |
| `glm-4.5-flash` | 智谱 Z.ai GLM-4.5-Flash |
| `solar-mini` | Upstage solar-mini4 |

## 运维备忘（VPS）
- 443 空闲：`cd /opt/litellm && docker compose up -d`
- 443 被占用：`cd /opt/litellm && docker compose -f docker-compose.tunnel.yml up -d`，Tunnel 指到 `http://127.0.0.1:4000`
- 日志轮转：compose 已设 `json-file` `max-size=10m` / `max-file=3`
- 磁盘：纯代理 20 GB 足够；部署后执行 `docker image prune -f`，并清理 apt 缓存
- Cloudflare 代理时：DNS CNAME `llm` → tunnel；对该主机名关闭 Browser Integrity Check，避免 Python/SDK 收到 1010
- 本仓库只含 `.env.example`。提交前确认没有 `.env` 或 token
