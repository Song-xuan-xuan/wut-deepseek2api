# 模型与协议实测报告

测试时间：**2026-10-26 15:17**
测试对象：`chatcloud.whut.edu.cn`（学校 OpenWebUI **0.5.14**，"DeepSeek云服务版"）
测试方式：全部经由本项目代理 `http://127.0.0.1:8787/v1` 发起，真实打到学校上游。
账号状态：已通过使用申请；个人 API Key 关闭（`enable_api_key: false`），使用浏览器 JWT。

---

## 1. 模型清单

`GET /v1/models` 返回 3 个模型，`owned_by` 全部为 `openai`（学校侧只配了一个 OpenAI 兼容连接）。

| 模型 id | 对话 | 流式 | 首字延迟 | 思维链 | 工具调用 | `max_tokens` 接受上限 | 备注 |
|---|---|---|---|---|---|---|---|
| `deepseek-v4-flash` | 200 | 支持 | **≈1.96 s** | 有（`reasoning_content`） | 支持 | 131072 通过 | 主力模型，思维链会占用输出预算 |
| `deepseek-v3` | 200 | 支持 | ≈4.16 s | 无 | 支持 | 131072 通过 | 稳定，但首字明显更慢 |
| `qwen3-235b-a22b` | 400 | — | — | — | — | — | 上游返回 `429 Open WebUI: Server Connection Error`，学校侧不可用 |

> `max_tokens` 探测只打到参数校验层，不会真的生成长文；学校当前对 131072 未报错，
> 本项目默认仍钳制到 `max_tokens_cap = 32768`，避免客户端传入天文数字。

## 2. 延迟与吞吐（实测）

同一时段连续采样（不走缓存、真实请求）：

| 指标 | `deepseek-v4-flash` |
|---|---|
| 非流式耗时 P50 / P95 | 1493 ms / 2065 ms |
| 流式首字 TTFT P50 | 1849 ms |
| 生成速度 P50 | 81.4 tok/s |
| 思维链占输出比例 | 30% |

> 想改模型参数、调 prompt 后复测，直接重跑压测即可；上表只代表测试当日、校园网内的表现。

## 3. 协议支持

三个入口都通过（同一份上游）：

| 协议 | 端点 | 结果 | 说明 |
|---|---|---|---|
| OpenAI Chat Completions | `POST /v1/chat/completions` | ✅ 200 | 原生支持 `tools` / `tool_calls`，流式为增量 delta |
| Anthropic Messages | `POST /v1/messages` | ✅ 200 | 返回 `thinking` + `text` 内容块，`stop_reason: end_turn` |
| OpenAI Responses | `POST /v1/responses` | ✅ 200 | 返回 `output` 数组（含 `reasoning` 项） |

## 4. 上下文与请求体上限（实测）

**先说清楚测的是什么**：之前表格里的 `max_tokens` 探测只验证了**输出预算参数**的上界
（`max_tokens: 131072` 不被拒），那**不是**上下文窗口。上下文窗口是另一件事，单独测如下。

**方法**：固定极小输出（`max_tokens: 8~16`），把输入逐步加大到目标规模，然后：
1. 看 HTTP 状态码；
2. **读回响应里的 `usage.prompt_tokens`** —— 这是上游真实计入的输入量。
   如果上游静默截断，`prompt_tokens` 会明显小于我们发出去的规模。

| 发送的输入 | 请求体大小 | 结果 | 上游计入的 `prompt_tokens` |
|---|---|---|---|
| 约 64k 字符 | — | 200 | 12,731 |
| 约 192k 字符 | — | 200 | 38,015 |
| 约 384k 字符 | — | 200 | 75,942 |
| 约 512k 字符 | — | 200 | 101,224 |
| 约 800k 字符 | — | 200 | 158,113 |
| 900k 字符 | 879 KB | 200 | 177,866 |
| 1,000k 字符 | 976 KB | 200 | 197,620 |
| 1,100k 字符 | 1,074 KB | **413** | — |

**结论**：

1. **上下文不是瓶颈**：输入到 ~198k tokens 时 `prompt_tokens` 仍在正常增长，没有被静默截断的迹象
   （对比 64k→1M 字符，计数基本线性）。
2. **真正的瓶颈是请求体大小**：超过约 **1 MB** 时学校反代直接返回 `413 Payload Too Large`，
   请求根本到不了模型。
3. 因此项目里 `contextWindow` 默认写 **131072** 属于保守值，可以按需调大；
   建议同时记住「单次请求的 messages 总文本不要超过 ~1 MB」这条硬限制。
4. 想自己复测，最短方式是发一条超长输入 + `max_tokens: 8`，然后看 `usage.prompt_tokens`：

```bash
python3 - <<'EOF'
import httpx
c = httpx.Client(base_url="http://127.0.0.1:8787/v1", timeout=300, trust_env=False)
H = {"Authorization": "Bearer sk-whut-local"}
unit = "The quick brown fox jumps over the lazy dog. "
text = unit * 20_000                      # 约 0.8 MB
r = c.post("/chat/completions", headers=H, json={
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": text + "\n只回复：收到"}],
    "max_tokens": 8,
})
print(r.status_code, (r.json().get("usage") or {}).get("prompt_tokens"))
EOF
```

## 5. 关键行为观察

| 项目 | 观察结果 |
|---|---|
| 流式结束 | 上游会发 `data: [DONE]`；缺失时本代理会补发 |
| 流内 usage | `deepseek-v4-flash` 有（含 `reasoning_tokens`），`deepseek-v3` 视情况而定 |
| 工具调用（流式） | `tool_calls` 以增量片段下发，需按 `index` 拼装 `arguments` |
| `role: tool` 回灌 | 正常，能基于工具结果继续作答 |
| 多轮 `reasoning_content` | 不需要回传，直接丢弃历史思维链即可 |
| 空正文现象 | `max_tokens` 太小时会被思维链吃光，正文为空 → 建议 ≥ 2048 |
| 聊天记录污染 | 通过 API 调用**不会**写入网页端聊天历史（实测前后条数不变） |
| 凭据有效期 | JWT 有效期 7 天（`exp` 实测），本代理自动续期 |

## 6. 复现命令

```bash
# 健康检查（上游版本 / 凭证剩余天数 / 模型列表）
curl --noproxy '*' -s 127.0.0.1:8787/health | python3 -m json.tool

# 模型列表
curl --noproxy '*' -s 127.0.0.1:8787/v1/models -H "Authorization: Bearer sk-whut-local"

# OpenAI 协议
curl --noproxy '*' -s 127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-whut-local" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}],"max_tokens":512}'

# Anthropic 协议
curl --noproxy '*' -s 127.0.0.1:8787/v1/messages \
  -H "x-api-key: sk-whut-local" -H "anthropic-version: 2023-06-01" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","max_tokens":256,"messages":[{"role":"user","content":"你好"}]}'

# Responses 协议
curl --noproxy '*' -s 127.0.0.1:8787/v1/responses \
  -H "Authorization: Bearer sk-whut-local" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","max_output_tokens":256,"input":"你好"}'

# 工具调用
curl --noproxy '*' -s 127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-whut-local" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","max_tokens":2048,
       "messages":[{"role":"user","content":"武汉天气？必须调用工具"}],
       "tools":[{"type":"function","function":{"name":"get_weather","description":"查天气",
         "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]}'
```

## 7. 结论

- **推荐主力**：`deepseek-v4-flash` —— 首字最快、带思维链、工具调用稳定。
- **备用**：`deepseek-v3` —— 无思维链、首字慢，但输出更"直给"。
- `qwen3-235b-a22b` 暂不可用，等学校侧修复。
- 三个协议入口均可用，因此 pi / Claude Code / Codex CLI / Cline / Cherry 都能接。
- 上下文：输入实测到 ~198k tokens 仍正常，瓶颈是 **1 MB 请求体**（超了上游 413）。
- 环境事实会随学校升级变化，本报告仅代表测试当日状态。
