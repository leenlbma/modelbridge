# 兼容性矩阵

当前开源版本：v0.3.2。模拟接口、真实网关和 Claude 实际任务是三种不同证据。本次开源准备未调用真实模型，不提高现有兼容性评级。

| 服务 | 模拟 HTTP / 实际转换器 | 官方账号网关 | Claude 桌面端 Code 任务 |
| --- | --- | --- | --- |
| OpenAI / Responses | 文字、流式工具参数、结果回传有回归覆盖 | 待验收 | 待验收 |
| Anthropic / Messages | 同上，原生转发 | 待验收 | 待验收 |
| DeepSeek / Chat Completions | 同上 | 待验收 | 待验收 |
| OpenRouter / Chat Completions | 同上 | 待验收 | 待验收 |
| 自定义服务 | 三种通用接口有模拟覆盖 | 按服务与模型验证 | 按客户端版本验证 |

早期本地原型曾在一个非公开服务上完成 Code 文件读写，但不能代表当前版本或上述公共服务已通过验收。后续本地检查遇到过工具流错误和 HTTP 429，完整真实链路未通过；不会用部分成功代替整体验收。私人网关信息和原始本机证据不随源码发布。

## 如何报告

先运行小额真实网关检查，例如：

```sh
.venv/bin/python scripts/verify_live.py --provider openai --model YOUR_MODEL_ID --output evidence/live-check.json
```

Key 通过终端隐藏输入。报告不含 Key、请求头、提示词或模型回复，但包含模型 ID；提交前检查是否可公开。总限时 240 秒，其中连接验证 180 秒。检查失败时不要反复调用绕过限流。

网关通过后，再在 Claude 桌面端执行同一套任务：读取一个无敏感内容的测试文件、计算结果、写入新文件、读回确认。记录 ModelBridge、Claude、操作系统、服务与模型版本，以及是否实际使用流式工具。可使用 [兼容性报告模板](https://github.com/leenlbma/modelbridge/issues/new?template=compatibility.yml)。

Claude Code CLI、Design、Cowork、图片、附件、长上下文及模型专有参数未独立验证。HTTP 200 不代表流式工具调用完成；流内错误、缺少消息起始或结束事件均会判为失败。
