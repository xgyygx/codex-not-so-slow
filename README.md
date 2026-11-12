# 不让你的codex那么慢

给 Codex 长对话减负：旧图片按需读取，工具日志留档回看。

对话里积累了大量截图或工具输出，压缩后还是很慢？这个 Skill 会先检查上下文，找出占用，再按你的确认处理。

## 功能

- **旧图外置**：将最新兼容压缩检查点中的旧图保存到本地，在上下文中留下图片路径，需要时再读取。原始聊天记录、原图和较新的图片继续保留。
- **工具输出精简**：可选的「极致上下文优化」。后续工具过程先存文件，对话中保留摘要、错误、警告、关键数值和结论依据。
- **备份与恢复**：修改前备份，修改后校验；目标对话通过临时归档、恢复来重新加载。

这是面向 macOS Codex 桌面端的社区维护工具，目前处于实验阶段。处理范围是本地对话上下文；遇到重连，先结合日志检查网络和服务状态。

## 安装

把下面这段发给 Codex：

```text
请使用 $skill-installer 安装这个 Skill：
https://github.com/xgyygx/codex-not-so-slow/tree/main/skills/codex-session-image-repair
```

安装后开启新对话，使用 `$codex-session-image-repair` 调用。

<details>
<summary>手动安装</summary>

在终端运行。已有安装时，先比较版本并备份。

```bash
git clone https://github.com/xgyygx/codex-not-so-slow.git
DEST="${CODEX_HOME:-$HOME/.codex}/skills/codex-session-image-repair"
if [ -e "$DEST" ]; then
  printf '%s\n' 'Existing skill found: compare and back it up before updating.'
else
  mkdir -p "$(dirname "$DEST")"
  cp -R codex-not-so-slow/skills/codex-session-image-repair "$DEST"
fi
```

</details>

## 使用

在另一个独立对话中发送：

```text
使用 $codex-session-image-repair，检查「目标对话名」的上下文占用和重连日志，
先告诉我诊断结果和处理建议，等我确认后再修复。
```

Skill 会检查目标及其子对话的状态，报告图片占用、数据结构和可处理范围。确认方案后，再备份、处理和校验。每次操作以指定对话为范围；维护消息和验证消息另行确认。

### 极致上下文优化（可选）

适合工具输出较多的对话，默认关闭，按对话单独启用。它通过约定后续工具输出的记录方式，减少上下文增长。

启用前会原样展示以下提示，等你确认：

**模型后续默认不携带完整的非报错中间过程，可能漏掉其中有用的细节；完整过程要先保存到文件，复盘时再按明确指令读取。** 错误、警告、关键数值和影响结论的证据仍要留在上下文里。

需要查过程时，直接说：

```text
读取中间过程，复盘刚才的工具日志。
```

需要停止精简时，说「关闭极致优化」。已留档的过程仍可按需读取。

这里的过程记录指工具输入和输出。已有上下文的整理、原生压缩单独执行，详见[极致优化说明](skills/codex-session-image-repair/references/extreme-context-optimization.md)。

## 使用前须知

- **环境**：macOS、Python 3.9+、系统 `lsof`，以及宿主提供的对话查询、归档和恢复工具。支持的数据结构见[兼容性说明](skills/codex-session-image-repair/references/compatibility.md)。
- **维护时机**：目标对话及其子对话全部空闲后执行，维护期间保持空闲。
- **图片读取**：外置后，旧图的视觉细节需要重新读取图片才能获得。请长期保留图片目录及其路径。
- **私密文件**：备份、图片和工具日志存放在仓库及 Skill 目录之外的本地私有目录，上传代码时排除这些文件。
- **回滚条件**：修复后文件保持原样时可自动回滚；已有新消息时，保留两个版本，核对后恢复。

## 开发

采用 [Agent Skills 格式](https://agentskills.io/specification)：

```text
skills/codex-session-image-repair/
├── SKILL.md          # 执行流程
├── agents/           # Codex 界面配置
├── references/       # 兼容性与操作说明
├── scripts/          # 诊断、备份和修复脚本
└── tests/            # 合成数据测试
```

运行测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s skills/codex-session-image-repair/tests -v
```

反馈问题时，请附上客户端版本、复现步骤和脱敏后的错误日志。聊天记录、图片和备份留在本地。

## 许可证

[MIT](LICENSE) · Copyright (c) 2026 xgyygx
