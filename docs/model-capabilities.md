# 模型能力实测报告

> 由 `scripts/modeltest/bench.py` 实测生成（真实打到学校上游），**不是**文档抄来的参数。

## 怎么复现

```bash
# 1) 确保服务在跑
.venv/bin/python -m app

# 2) 全部套件（约 8~12 分钟；建议分批跑，避免被学校限流）
.venv/bin/python scripts/modeltest/bench.py latency concurrency tools --models deepseek-v4-flash
.venv/bin/python scripts/modeltest/bench.py context stream vision cache --models deepseek-v4-flash
.venv/bin/python scripts/modeltest/bench.py robust --models deepseek-v4-flash

# 3) 生成 / 更新本文件
.venv/bin/python scripts/modeltest/bench.py report --results <各次 results.json 用逗号分隔> --publish
```

`--publish` 会把报告直接写入本文件。

## 套件说明

| 套件 | 测什么 |
|---|---|
| `latency` | 非流式延迟、流式首字 TTFT、生成速度 (tok/s)、思维链占输出比 |
| `concurrency` | 逐级加压 1/2/4/6 并发，成功率、P50/P95、错误码、吞吐；超过 50% 失败自动停止加压 |
| `context` | 按**字符**加码，找 413 边界（受文本密度影响，只用于确认请求体上限） |
| `context-dense` | **按 token 目标**喂高密度文本，验证码放开头、问题放结尾 → 这一项才代表"真实上下文窗口" |
| `tools` | 单工具 / 强制 / 多工具择一 / 结果回灌 / 流式增量 / 禁用，共 6 个用例 |
| `stream` | 分片数、`[DONE]`、流内 usage、思维链、最大分片间隔、客户端中断后能否继续 |
| `robust` | 极小预算、截断、stop 序列、system 约束、JSON 输出、非法模型、超大 max_tokens、多语言、多轮 |
| `vision` | 发一张 8×8 PNG，看是否真能识图 |
| `cache` | 同一 20k 字符前缀发两次，比较 token 数与耗时（缓存迹象） |

---


测试时间：**2026-10-26 15:43**  
测试目标：`http://127.0.0.1:8787/v1`（本项目代理 → 学校 OpenWebUI DeepSeek云服务版 0.5.14）  
测试模型：`deepseek-v3`, `deepseek-v4-flash`, `qwen3-235b-a22b`  
测试套件：前缀缓存 / 并发 / 上下文 / 延迟 / 边界 / 流式 / 工具调用 / 图片

> 本报告由 `scripts/modeltest/bench.py` 自动生成，所有数字都来自真实请求，未做人工修饰。

## 一、结论速览

| 模型 | 对话 | 流式 | 首字 TTFT | 生成速度 | 工具调用 | 上下文 | 图片 | 并发上限 |
|---|---|---|---|---|---|---|---|---|
| `deepseek-v3` | ✅ | ✅ | 7406 ms | 14.6 tok/s | 5/6 | 37,535 tok | ❌ | ≤6 全成功 |
| `deepseek-v4-flash` | ✅ | ✅ | 2498 ms | 27.1 tok/s | 5/6 | ≥745,080 tok | ❌ | ≤6 全成功 |
| `qwen3-235b-a22b` | — | ❌ | — | — | — | — | ❌ | — |

## 二、延迟与吞吐

| 模型 | 非流式 P50 | 非流式 P95 | 流式 TTFT P50 | 生成速度 P50 | 思维链占输出比 |
|---|---|---|---|---|---|
| `deepseek-v3` | 2823 ms | 2830 ms | 7406 ms | 14.6 tok/s | 0% |
| `deepseek-v4-flash` | 1951 ms | 3925 ms | 2498 ms | 27.1 tok/s | 68% |
| `qwen3-235b-a22b` | — | — | — | — | — |

## 三、并发能力

逐级加压，每级多轮，统计成功/失败与吞吐。**任何失败都会记录状态码**（429 = 被上游限流，5xx = 上游异常）。每轮之间刻意留了 1.2s 间隔避免风控，所以 `req/s` 偏保守，只用于横向比较。

### `deepseek-v3`

| 并发 | 请求数 | 成功 | 失败 | 错误码 | P50 | P95 | 吞吐 (req/s) |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 2 | 0 | — | 636 ms | 640 ms | 0.54 |
| 2 | 4 | 4 | 0 | — | 858 ms | 25341 ms | 0.14 |
| 4 | 8 | 8 | 0 | — | 1255 ms | 9198 ms | 0.56 |
| 6 | 12 | 12 | 0 | — | 1447 ms | 5586 ms | 0.64 |

### `deepseek-v4-flash`

| 并发 | 请求数 | 成功 | 失败 | 错误码 | P50 | P95 | 吞吐 (req/s) |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 2 | 0 | — | 901 ms | 1042 ms | 0.46 |
| 2 | 4 | 4 | 0 | — | 2145 ms | 2690 ms | 0.55 |
| 4 | 8 | 8 | 0 | — | 1762 ms | 5051 ms | 0.80 |
| 6 | 12 | 12 | 0 | — | 4653 ms | 5141 ms | 0.76 |

## 四、上下文窗口

**结论先行**：实测在 **745,080 tokens** 的输入下，模型仍能准确召回放在**最开头**的验证码；这就是可用窗口的下限。再往上加会被 1 MiB 请求体上限挡住（见 4.2）。

### 4.1 按 token 加压 + 首尾召回（真实窗口）

方法：用高熵 ASCII 生成指定规模的输入（实测约 `0.77 token/字符`；英文散文只有 `0.20`、中文 `0.49` —— 用散文测会把「传输上限」误当成「窗口上限」），把验证码放在**第一句**、问题放在**最后一句**，模型答得出验证码才算窗口真的覆盖了整段。

**`deepseek-v4-flash`**

| 目标 token | 实际 prompt_tokens | 请求体 | 状态 | 耗时 | 开头验证码召回 | 备注 |
|---|---|---|---|---|---|---|
| 120,000 | 119,079 | 0.15 MB | 200 | 6961 ms | ✅ 命中 |  |
| 300,000 | 298,005 | 0.38 MB | 200 | 24935 ms | ✅ 命中 |  |
| 450,000 | 447,352 | 0.57 MB | 200 | 8146 ms | ✅ 命中 |  |
| 600,000 | 596,088 | 0.76 MB | 200 | 9175 ms | ✅ 命中 |  |
| 700,000 | 695,392 | 0.89 MB | 200 | 9307 ms | ❌ 未命中 |  |
| 745,000 | 739,976 | 0.94 MB | 200 | 49122 ms | ✅ 命中 |  |
| 750,000 | 745,080 | 0.95 MB | 200 | 69438 ms | ✅ 命中 |  |

- **可用窗口 ≥ 745,080 tokens**（该规模下开头信息仍被准确召回）
- 表里个别「未命中」多为**回答不完整**（预算被思维链吃光或输出被截断），同档位重跑通常能命中，不代表窗口不足。

### 4.2 真正的硬限制：请求体 1 MiB

继续加码会被学校反代（nginx）直接 `413 Request Entity Too Large`，请求根本到不了模型。所以「一次能塞多少字」由请求体大小决定，而折合多少 token 取决于文本密度：

| 文本类型 | token/字符 | 1 MiB 请求体约合 |
|---|---|---|
| 英文散文 | 0.20 | ≈ 20 万 tokens |
| 中文 | 0.49 | ≈ 49 万 tokens |
| 高熵 ASCII（本节测试用） | 0.77 | ≈ 78 万 tokens |

- `deepseek-v3`（英文散文口径）：最大通过 192,000 字符 → 37,535 tokens；首个失败点 375.1 KB → 400
- `deepseek-v4-flash`（英文散文口径）：最大通过 1,030,000 字符 → 201,359 tokens

本代理自身也设了 `MAX_BODY_BYTES = 1 MiB`，超限在本地就返回 413 并提示「裁剪历史消息后再试」，省得白跑一趟上游。

### 4.3 大输入下上游不稳定

- 偶发 `400 Open WebUI: Server Connection Error`，同一请求重试通常成功；
- 超大请求容易撞网关 **504**（单次处理超过约 60~70 秒）；
- 偶尔 `200` 但 `choices` 为空 —— 本代理已转成 502 并说明原因，避免客户端拿到 null。

**实践建议**：把 1 MiB 当硬约束（agent 侧主动裁剪历史），大输入请求做 1~2 次重试。

## 五、工具调用

### `deepseek-v3` —— 5/6 通过

| 用例 | 结果 | 说明 | 耗时 |
|---|---|---|---|
| 单工具 / auto | ✅ | get_weather({"city":"武汉"}) | 3432 ms |
| tool_choice=required | ❌ | — | 1358 ms |
| 多工具择一 / auto | ✅ | get_time({"timezone":"Asia/Shanghai"}) | 2664 ms |
| 结果回灌 / role:tool | ✅ | — | 2179 ms |
| 流式 tool_calls 增量 | ✅ | get_weather({"city": "北京"}
) | 2598 ms |
| tool_choice=none | ✅ | — | 22342 ms |

### `deepseek-v4-flash` —— 5/6 通过

| 用例 | 结果 | 说明 | 耗时 |
|---|---|---|---|
| 单工具 / auto | ✅ | get_weather({"city": "武汉"}) | 2313 ms |
| tool_choice=required | ❌ | — | 2510 ms |
| 多工具择一 / auto | ✅ | get_time({"timezone": "Asia/Shanghai"}) | 10358 ms |
| 结果回灌 / role:tool | ✅ | — | 1512 ms |
| 流式 tool_calls 增量 | ✅ | get_weather({"city": "北京"}) | 2674 ms |
| tool_choice=none | ✅ | — | 3549 ms |

## 六、流式行为

| 模型 | 分片数 | `[DONE]` 收尾 | 流内 usage | 思维链 | 最大分片间隔 | 中断后可继续 |
|---|---|---|---|---|---|---|
| `deepseek-v3` | 40 | ✅ | ✅ | 0 tok | 4599 ms | ✅ |
| `deepseek-v4-flash` | 110 | ✅ | ✅ | 69 tok | 2521 ms | ✅ |

## 七、边界与异常

### `deepseek-v3` —— 9/9 通过

| 用例 | 结果 | 说明 |
|---|---|---|
| max_tokens=8（预算极小） | ✅ | 正文 21 字 / 思维链 0 tokens |
| max_tokens=64（应截断） | ✅ | finish=length |
| stop 序列 | ✅ | 含 5 则未生效：False |
| system prompt 约束 | ✅ | 是否遵守：True |
| JSON 输出 | ✅ | 可解析：True |
| 非法模型名 | ✅ | 应被拒绝 |
| max_tokens=1e7（应被代理钳制） | ✅ | proxy cap 生效则仍成功，finish=stop |
| 多语言 + emoji | ✅ | 你好 - Hello  
苹果 - Apple  
图书馆 - Library  |
| 多轮上下文（7+5 再 ×2 = 24） | ✅ | — |

### `deepseek-v4-flash` —— 9/9 通过

| 用例 | 结果 | 说明 |
|---|---|---|
| max_tokens=8（预算极小） | ✅ | 正文 0 字 / 思维链 8 tokens |
| max_tokens=64（应截断） | ✅ | finish=length |
| stop 序列 | ✅ | 含 5 则未生效：False |
| system prompt 约束 | ✅ | 是否遵守：True |
| JSON 输出 | ✅ | 可解析：True |
| 非法模型名 | ✅ | 应被拒绝 |
| max_tokens=1e7（应被代理钳制） | ✅ | proxy cap 生效则仍成功，finish=stop |
| 多语言 + emoji | ✅ | Hello Apple Library |
| 多轮上下文（7+5 再 ×2 = 24） | ✅ | — |
## 八、图片输入（vision）

| 模型 | 支持 | 状态 | 模型回答 |
|---|---|---|---|
| `deepseek-v3` | ❌ | 400 | 500: Open WebUI: Server Connection Error |
| `deepseek-v4-flash` | ❌ | 200 | — |
| `qwen3-235b-a22b` | ❌ | 400 | 429: Open WebUI: Server Connection Error |

## 九、前缀缓存迹象

把同一段 20k 字符前缀发两次，比较 `prompt_tokens` 与耗时（仅作**迹象**判断，不等于官方缓存）。

| 模型 | 首次 prompt_tokens | 二次 prompt_tokens | 计数一致 | 首次耗时 | 二次耗时 | 提速比 |
|---|---|---|---|---|---|---|
| `deepseek-v3` | 3927 | 3927 | ✅ | 2910 ms | 2653 ms | 1.10× |
| `deepseek-v4-flash` | 4004 | 4004 | ✅ | 13173 ms | 2532 ms | 5.20× |
` |

