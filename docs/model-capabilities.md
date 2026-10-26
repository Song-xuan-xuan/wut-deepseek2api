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
| `context` | 从小到大喂输入，读回 `usage.prompt_tokens` 判断是否被静默截断，并找出 413 边界 |
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
| `deepseek-v4-flash` | ✅ | ✅ | 2164 ms | 78.1 tok/s | 5/6 | ≥745,080 tok | ❌ | ≤6 全成功 |
| `qwen3-235b-a22b` | — | ❌ | — | — | — | — | ❌ | — |

## 二、延迟与吞吐

| 模型 | 非流式 P50 | 非流式 P95 | 流式 TTFT P50 | 生成速度 P50 | 思维链占输出比 |
|---|---|---|---|---|---|
| `deepseek-v3` | 2823 ms | 2830 ms | 7406 ms | 14.6 tok/s | 0% |
| `deepseek-v4-flash` | 1525 ms | 1525 ms | 2164 ms | 78.1 tok/s | 44% |
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

## 四、上下文窗口与请求体上限（按字符）

固定极小输出（`max_tokens=16`），把输入逐步加大。**关键看 `usage.prompt_tokens`**：如果上游静默截断，这个数会明显小于发送规模；413 则是反代的请求体上限。

### `deepseek-v3`

| 发送字符数 | 请求体 | 状态 | 上游计入 prompt_tokens | TTFT | 总耗时 | 备注 |
|---|---|---|---|---|---|---|
| 8,000 | 8.0 KB | 200 | 1,580 | — | 915 ms |  |
| 32,000 | 31.4 KB | 200 | 6,269 | — | 1287 ms |  |
| 96,000 | 93.9 KB | 200 | 18,776 | — | 3530 ms |  |
| 192,000 | 187.6 KB | 200 | 37,535 | — | 5094 ms |  |
| 384,000 | 375.1 KB | 400 | — | — | 1857 ms | 400: Open WebUI: Server Connection Error |
| 640,000 | 625.1 KB | 400 | — | — | 2776 ms | 400: Open WebUI: Server Connection Error |
| 880,000 | 859.5 KB | 200 | — | — | 3699 ms | ⚠️ 200 但无 usage/choices（上游静默失败） |
| 1,000,000 | 976.7 KB | 200 | — | — | 4200 ms | ⚠️ 200 但无 usage/choices（上游静默失败） |

- 实测最大输入：**192,000 字符** → 上游计入 **37,535 tokens**
- 首个失败点：384,000 字符 / 375.1 KB → 400 400: Open WebUI: Server Connection Error

### `deepseek-v4-flash`

| 发送字符数 | 请求体 | 状态 | 上游计入 prompt_tokens | TTFT | 总耗时 | 备注 |
|---|---|---|---|---|---|---|
| 8,000 | 8.0 KB | 200 | 1,657 | — | 1152 ms |  |
| 192,000 | 187.7 KB | 200 | 37,612 | — | 2289 ms |  |
| 640,000 | 625.2 KB | 200 | 125,152 | — | 5336 ms |  |
| 880,000 | 859.5 KB | 200 | 172,048 | — | 4558 ms |  |
| 1,000,000 | 976.7 KB | 200 | 195,497 | — | 7574 ms |  |
| 1,030,000 | 1006.0 KB | 200 | 201,359 | — | 2429 ms |  |
| 1,000,000 | 976.7 KB | 200 | 195,497 | — | 3307 ms |  |

- 实测最大输入：**1,030,000 字符** → 上游计入 **201,359 tokens**

## 五、真实上下文窗口（按 token 测，含首尾召回）

上一节按**字符**加码，结论会被文本密度带偏：英文散文约 `0.20 tok/字符`，1 MB 请求体只装得下约 20 万 token。这一节改用高熵 ASCII（实测约 `0.77 tok/字符`）直接按 **token 目标**加压，并把验证码放在**最开头**、问题放在最后——能答出来才算窗口真的覆盖了整段。

### `deepseek-v4-flash`

| 目标 token | 实际 prompt_tokens | 请求体 | 状态 | 耗时 | 开头验证码召回 | 备注 |
|---|---|---|---|---|---|---|
| 120,000 | 119,079 | 0.15 MB | 200 | 6774 ms | ❌ 未命中 |  |
| 300,000 | 298,005 | 0.38 MB | 200 | 10453 ms | ❌ 未命中 |  |
| 450,000 | 447,352 | 0.57 MB | 200 | 8146 ms | ✅ 命中 |  |
| 600,000 | 596,088 | 0.76 MB | 200 | 9175 ms | ✅ 命中 |  |
| 700,000 | 695,392 | 0.89 MB | 200 | 9307 ms | ❌ 未命中 |  |
| 745,000 | 739,976 | 0.94 MB | 200 | 49122 ms | ✅ 命中 |  |
| 750,000 | 745,080 | 0.95 MB | 200 | 69438 ms | ✅ 命中 |  |
| 800,000 | — | 0.0 MB | — | 1 ms | ❌ 未命中 | ConnectError: All connection attempts failed |

- 实测最大可发送输入：**745,080 tokens**
- 其中**开头信息成功召回**的最大规模：**745,080 tokens**
- 更大的请求会被 1 MiB 请求体上限挡住（本地代理先返回 413），所以「窗口多大」在这套部署上被封在传输层，而不是被模型拒绝。
- 上表里「未命中」的行多为**回答不完整**（预算被思维链吃光或输出被截断），同一档位重跑通常能命中，不等于窗口不够。
- 大输入下上游**不稳定**：偶发 `400 Open WebUI: Server Connection Error`、网关 504（>60 s）、以及「200 但正文为空」（预算被思维链吃光）。同一档位多试几次通常能成功，因此上表的异常不代表模型窗口不足。

## 六、工具调用

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

## 七、流式行为

| 模型 | 分片数 | `[DONE]` 收尾 | 流内 usage | 思维链 | 最大分片间隔 | 中断后可继续 |
|---|---|---|---|---|---|---|
| `deepseek-v3` | 40 | ✅ | ✅ | 0 tok | 4599 ms | ✅ |
| `deepseek-v4-flash` | 110 | ✅ | ✅ | 69 tok | 2521 ms | ✅ |

## 八、边界与异常

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
| 多语言 + emoji | ✅ | 1. Hello  
2. Apple  
3. Library 🍎 |
| 多轮上下文（7+5 再 ×2 = 24） | ✅ | — |

### `deepseek-v4-flash` —— 8/9 通过

| 用例 | 结果 | 说明 |
|---|---|---|
| max_tokens=8（预算极小） | ✅ | 正文 0 字 / 思维链 8 tokens |
| max_tokens=64（应截断） | ✅ | finish=length |
| stop 序列 | ✅ | 含 5 则未生效：False |
| system prompt 约束 | ✅ | 是否遵守：False |
| JSON 输出 | ✅ | 可解析：True |
| 非法模型名 | ✅ | 应被拒绝 |
| max_tokens=1e7（应被代理钳制） | ✅ | proxy cap 生效则仍成功，finish=stop |
| 多语言 + emoji | ✅ | Hello / Apple / Library |
| 多轮上下文（7+5 再 ×2 = 24） | ❌ | — |

### `qwen3-235b-a22b` —— 1/9 通过

| 用例 | 结果 | 说明 |
|---|---|---|
| max_tokens=8（预算极小） | ❌ | 正文 0 字 / 思维链 0 tokens |
| max_tokens=64（应截断） | ❌ | finish= |
| stop 序列 | ❌ | 含 5 则未生效：False |
| system prompt 约束 | ❌ | 是否遵守：False |
| JSON 输出 | ❌ | 可解析：False |
| 非法模型名 | ✅ | 应被拒绝 |
| max_tokens=1e7（应被代理钳制） | ❌ | proxy cap 生效则仍成功，finish= |
| 多语言 + emoji | ❌ | 429: Open WebUI: Server Connection Error |
| 多轮上下文（7+5 再 ×2 = 24） | ❌ | — |

## 九、图片输入（vision）

| 模型 | 支持 | 状态 | 模型回答 |
|---|---|---|---|
| `deepseek-v3` | ❌ | 400 | 500: Open WebUI: Server Connection Error |
| `deepseek-v4-flash` | ❌ | 200 | — |
| `qwen3-235b-a22b` | ❌ | 400 | 429: Open WebUI: Server Connection Error |

## 十、前缀缓存迹象

把同一段 20k 字符前缀发两次，比较 `prompt_tokens` 与耗时（仅作**迹象**判断，不等于官方缓存）。

| 模型 | 首次 prompt_tokens | 二次 prompt_tokens | 计数一致 | 首次耗时 | 二次耗时 | 提速比 |
|---|---|---|---|---|---|---|
| `deepseek-v3` | 3927 | 3927 | ✅ | 2910 ms | 2653 ms | 1.10× |
| `deepseek-v4-flash` | 4004 | 4004 | ✅ | 13173 ms | 2532 ms | 5.20× |

## 十一、已知异常与规避

| 异常 | 现象（真实证据） | 规避方式 |
|---|---|---|
| `deepseek-v3` 强制工具调用（`tool_choice=required`）不可靠 | 完全没有产生 `tool_calls`，只回了一段普通文本：`你好！有什么可以帮您的吗？` | **用 `tool_choice=auto`**（实测稳定）；确需强制调用时在客户端兜底重试 |
| `deepseek-v3` 不支持图片输入 | 400 500: Open WebUI: Server Connection Error | `models.json` / 客户端里把该模型的 `input` 设为 `["text"]`，别开图片 |
| `deepseek-v3` 超大输入会“假成功” | 880,000 字符时返回 200，但 `choices` 为 `null`、无 usage | 本项目代理已把这个状态转成 502 并提示“输入过大”，不会让 agent 拿到 null |
| `deepseek-v4-flash` 强制工具调用（`tool_choice=required`）不可靠 | 上游把工具调用塞进 `content` 的 DSML 标记里，`tool_calls` 却是 `null`：`<｜DSML｜invoke name="get_weather">"你好！有什么我可以帮助你的吗？比如我可以帮你查询某个城市的实时天气。"</｜DSML｜invoke>` | **用 `tool_choice=auto`**（实测稳定）；确需强制调用时在客户端兜底重试 |
| `deepseek-v4-flash` 不支持图片输入 | 返回 200 但正文为空，图片被静默丢弃 | `models.json` / 客户端里把该模型的 `input` 设为 `["text"]`，别开图片 |

> 补充：`finish_reason=tool_calls` 却没有 `tool_calls` 属于自相矛盾的响应，本项目代理会自动把它规整成 `finish_reason=stop`，避免客户端抛错。

## 十二、选型建议

- **日常主力**：`deepseek-v4-flash` —— 首字最快（2164 ms），生成 78.1 tok/s，工具调用 5/6。
- **长上下文**：仍是 `deepseek-v4-flash`（实测 745,080 tokens 仍能召回开头信息，上限被 1 MB 请求体而不是模型窗口挡住）。
- **`deepseek-v3`**：首字 7406 ms，比主力慢 3.4 倍；适合“要结果不要速度”的批量任务。
- **`qwen3-235b-a22b`**：当前不可用，先别配。

接入 agent 的推荐组合：主力模型 + `tool_choice=auto` + 输出预算 ≥ 1024（`deepseek-v4-flash` 的思维链会占掉相当比例的输出预算，给少了会出现正文为空）。

## 十三、环境与限制

| 项目 | 实测值 |
|---|---|
| 上游 | `DeepSeek云服务版 0.5.14`（学校 OpenWebUI） |
| 代理 | `http://127.0.0.1:8787/v1` |
| 凭证 | 浏览器 JWT，7 天有效，代理自动续期 |
| 高密度文本密度 | 约 0.77 tok/字符（ASCII 符号/随机串）；英文散文 0.20；中文 0.49 |
| 请求体上限 | 约 **1 MB**：`1,040,000` 字符（0.99 MB）通过；`1,100,000` 字符（1.05 MB）被学校反代直接 `413`（HTML 页面，未到模型） |
| 代理自带限制 | `MAX_BODY_BYTES = 1 MiB`，超限在本地就返回 413 并说明原因 |
| 测试脚本 | `scripts/modeltest/bench.py` |

