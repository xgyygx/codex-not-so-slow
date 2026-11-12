# 不让你的codex那么慢

一个保守、可回滚的 Codex Skill：诊断长对话中的旧图片上下文膨胀，并在明确确认后外置旧图；可另行开启逐对话的工具输出精简约定。

> **实验性维护工具，非 OpenAI 官方项目。** 不保证解决所有重连，不承诺零信息损失或固定加速倍数。网络、服务端负载和工具执行慢需要分别诊断。

## 能做什么

| 模式 | 默认行为 | 改动范围 |
| --- | --- | --- |
| 图片上下文修复 | 先诊断，确认后执行 | 仅兼容的最新压缩检查点中的旧图片，替换为本地按需读取引用 |
| 极致上下文优化 | 默认关闭，逐对话确认 | 完整可记录过程先留档，后续回传摘要及必要证据；不改模型或推理强度 |

图片模式保留原始聊天记录、原图、较新的图片、其他文本及完整备份；但旧图视觉细节从自动携带变为按需读取，**不等于模型每轮获得的信息完全相同**。备份与图片目录应长期保留，勿移动或删除。

## 适用范围

- macOS 本机 Codex 桌面环境，Python 3.9+，系统 `lsof`。
- 已核实的 `state_5.sqlite`、未压缩 JSONL，以及 `compacted.payload.replacement_history` 结构。
- 宿主需提供第一方对话查询与归档/恢复工具；缺少工具则停在诊断或准备阶段。
- 从另一个独立对话维护目标；目标及后代须空闲，维护期间不要打开或发送目标对话。
- 不是 ChatGPT 网页版历史清理器，不修改客户端二进制、SQLite 或全局模型设置。不自动批量修复。

## 安装

本仓库目前为私有，安装者需要仓库读取权限；登录网页不等于终端已具备 Git 访问权限。

在支持 Skill Installer 的 Codex 中发送：

```text
请使用 $skill-installer，从以下目录安装 Skill：
https://github.com/xgyygx/codex-not-so-slow/tree/main/skills/codex-session-image-repair
```

或先用已有 GitHub SSH 认证克隆，再复制到个人 Skills 目录（已有安装时先比较、备份，不直接覆盖）：

```bash
git clone git@github.com:xgyygx/codex-not-so-slow.git
DEST="${CODEX_HOME:-$HOME/.codex}/skills/codex-session-image-repair"
if [ -e "$DEST" ]; then
  printf '%s\n' 'Existing skill found: compare and back it up before updating.'
else
  mkdir -p "$(dirname "$DEST")"
  cp -R codex-not-so-slow/skills/codex-session-image-repair "$DEST"
fi
```

安装后开启新对话；若未发现 Skill，按当前客户端的加载方式重新加载。

## 使用

```text
使用 $codex-session-image-repair，先诊断「目标对话名」为什么慢或重连。
先报告证据，不操作其他对话，确认后再修复。
```

默认先诊断，绝不把一次许可扩大到其他对话。图片修复前会确认备份、外置图片和临时归档/恢复；发送维护消息或验证消息也需要明确许可。

### 可选：极致上下文优化

这是实验性工作约定，不是客户端原生开关或请求过滤器。启用前必须原样展示并取得用户确认：

**模型后续默认不携带完整的非报错中间过程，可能漏掉其中有用的细节；完整过程要先保存到文件，复盘时再按明确指令读取。** 错误、警告、关键数值和影响结论的证据仍要留在上下文里。

“中间过程”指可记录的工具输入/输出，不是模型内部思维链。历史中本已截断的结果不能凭空补全；重要证据不足时应主动回读。用户可说“读取中间过程”“复盘完整日志”或“关闭极致优化”。关闭约定不会自动展开已压缩的历史。

此约定只控制后续增长，不会自动移除既有上下文。原生压缩必须通过已验证入口，观察到新的压缩事件才算成功；普通消息 `/compact` 不保证执行原生压缩。

## 隐私与回滚

- 本仓库只包含通用 Skill、脚本和合成测试，不包含真实聊天、图片、数据库、账户配置、认证信息或业务项目内容。
- 运行时备份、工具记录和图片可能含完整私密数据，必须保存在仓库及 Skill 目录之外的私有持久目录，不上传 GitHub。
- 自动回滚仅在应用后的整文件未变化时执行；已有新消息时停止覆盖，保留两个版本供核查。
- 归档与文件句柄检查不是跨进程强锁；存在并发变化就暂停并保留现场。

## 目录与测试

遵循 [Agent Skills 格式](https://agentskills.io/specification)：以 `SKILL.md` 的 YAML `name` / `description` 为入口，按需加载 `references/`，确定性操作放在 `scripts/`，Codex 界面元数据放在 `agents/openai.yaml`。

```text
skills/codex-session-image-repair/
├── SKILL.md
├── agents/openai.yaml
├── references/
├── scripts/
└── tests/
```

仅运行合成测试，不连接真实聊天：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s skills/codex-session-image-repair/tests -v
```

测试通过验证实现的不变量，不证明实际响应速度。客户端结构变化时，应重新诊断适配，勿强行套用。

## 许可证

[MIT](LICENSE) · Copyright (c) 2026 xgyygx
