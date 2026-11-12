---
name: codex-session-image-repair
description: Diagnose and repair image-heavy local Codex chats that remain slow or reconnect after compaction. Externalize old checkpoint images with backups and reload an idle chat. Supports per-chat opt-in tool-output reduction. Not for general network failures or ChatGPT web history.
---

# Codex 图片上下文修复

默认只诊断，确认后处理指定对话。适用于 macOS 本机 Codex 的 `state_5.sqlite`、未压缩 JSONL 和 `compacted.payload.replacement_history`；结构不兼容时停在诊断。图片外置处理已有 checkpoint，新图片仍会累积。

## 共用约束

- 用第一方 `list_threads` / `read_thread` 确认目标 ID、local host、状态及当前对话 ID；同名先消歧，避免输出整库内容。
- 只维护另一个空闲对话，排除当前对话及祖先。目标或后代正在运行、有未确认提交/推送或待处理业务操作时，先等待或澄清；维护期间保持整个子树空闲，勿打开或发送目标对话。
- 备份目录 `DATA` 使用仓库和 Skill 目录之外的私有持久绝对路径，与 rollout 位于同一文件系统。长期保留备份、图片及恢复链接，不移动、删除或上传远端。
- 修改范围限于已确认的上下文维护；项目代码、数据库、模型/推理设置、代理、认证和客户端配置保持原样。代发维护/验证消息、原生压缩分别取得许可，原业务待办保持暂停。

## 诊断

区分 checkpoint 图片字节、有效历史序列化字节、rollout 文件大小、token 用量和真实网络请求体。复用已有日志，分别检查断流重试、HTTP fallback、429、首 token 和工具耗时；“响应头等待”按原指标报告。

```bash
# SCRIPT：本 Skill 的 scripts/repair_context.py 绝对路径。
# TARGET / CURRENT：已核实的目标 / 当前对话 UUID；DATA：上述持久目录。
python3 "$SCRIPT" inspect --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
```

`inspect` 只读，返回图片数量、历史字节及归档状态。没有 checkpoint/旧图片、远程图片、未知图片字段或压缩 rollout 时，报告原因并停止图片修复。适配疑问查阅 [compatibility.md](references/compatibility.md)。

## 图片修复

先向用户说明并确认：完整备份、旧 checkpoint 图片改为本地按需读取引用、临时归档目标及后代、恢复原归档状态。原始历史、新图片及其他文本保留；旧图视觉细节通过回读图片获得。

1. **准备**：导出图片、校验 SHA256、备份记录与归档状态。
   ```bash
   python3 "$SCRIPT" prepare --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
2. **释放目标**：读取 `plan.json` 的 `archive_states`，再次通过第一方工具确认整个子树空闲。使用 `set_thread_archived` 归档目标（`hostId: local`、`source: codex`、`archived: true`）。工具可能递归影响后代；缺少工具时停在准备阶段，勿用私有 IPC、杀进程或写 SQLite 代替。
3. **写入**：先读取 [兼容性与恢复日志](references/compatibility.md)，再执行：
   ```bash
   python3 "$SCRIPT" apply --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
   脚本检查归档状态、`lsof` 句柄、checkpoint 和哈希，再按原字节跨度原子替换。保留 `applied.json` 与 `recovery_inode`；出现并发迹象立即暂停并保存现场，归档和句柄检查仅是写入前提，并非排他锁。
4. **恢复状态**：成功或失败都检查现场。文件完整性确认后，先恢复目标，再按父到子顺序校正后代；原来归档的子树重新归档，最终逐一核对所有 ID。
   - `pending`、新后代、哈希异常、图片缺失或恢复 inode 有追加时，按兼容性文档的恢复分支处理；保留双方文件，避免盲目重试 `apply` 或加载未知状态。
5. **校验**：
   ```bash
   python3 "$SCRIPT" verify --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
   要求 `disk_verified` 且 `archive_states_restored: true`。获准代发测试时，先排除旧待办/排队消息，省略 model/thinking 设置，用 `wait_threads` 观察结果。
6. **交付**：报告图片数、有效历史字节前后、备份位置及归档状态。速度和重试仅报告实测数据；倍数比较需同任务、同条件样本。后续按症状重新诊断，勿自动循环修复。

## 回滚

沿用空闲检查与临时归档流程，运行 `rollback`（与上述命令相同参数），检查完整性后恢复归档状态。整文件等于应用后哈希时才自动回滚；已有新消息则保留双方，人工核对合并。中断与并发恢复见 [compatibility.md](references/compatibility.md)。

## 可选：极致上下文优化

默认关闭。仅当用户明确要求减少工具输出或长历史负担时，读取 [extreme-context-optimization.md](references/extreme-context-optimization.md)，按其中确认、留档、回读和验收流程执行。启用提示使用 [extreme-consent.txt](references/extreme-consent.txt) 原文；图片修复许可仅覆盖图片模式。

## 自测

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SKILL_DIR/tests" -v
```

使用临时合成数据库和会话测试，真实对话另行确认。
