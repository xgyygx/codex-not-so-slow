---
name: codex-session-image-repair
description: Diagnose and repair image-heavy local Codex chats that remain slow or repeatedly reconnect after compaction. Externalize old inline images only from the latest compatible compaction checkpoint, preserve original history and newer images, and reload a different idle chat through temporary archive/unarchive without quitting the desktop. Also supports explicitly requested, per-chat opt-in optimization of verbose tool output with backed-up process records and replay verification. Not for general network failures, ChatGPT web history, app binary patching, or automatic bulk repairs.
---

# Codex 图片上下文修复

默认只诊断。目标是减少旧图片在有效上下文中的重复携带，不是永久修改客户端请求策略，也不保证所有慢响应都由图片导致。适用于 **macOS 本机 Codex、`state_5.sqlite`、未压缩 JSONL、`compacted.payload.replacement_history`** 的已验证结构。其他结构停在诊断，不试探性改库。

## 可选：极致上下文优化（默认关闭）

仅当用户明确要求减少图片之外的工具输出/长历史负担时，读取 [references/extreme-context-optimization.md](references/extreme-context-optimization.md)。这是逐对话确认的实验约定，非客户端内置开关，不改模型/推理强度；必须原样展示 [固定确认文案](references/extreme-consent.txt) 并获同意才启用。图片修复授权不包含此模式。现有图片修复流程和脚本不因此改变。

## 先确认对象与证据

1. 用第一方 `list_threads` / `read_thread` 定位用户指定的对话，确认 ID、local host、运行状态与当前执行对话 ID。不要扫描输出整库聊天内容；同名对话先消歧。
2. 明确区分：有效 checkpoint 内图片字节、rollout 文件体积、token 用量、网络请求体、首 token 耗时、工具耗时。已有日志足够就复用。查看断流重试、HTTP fallback、429 等证据；不要把“响应头等待”直接当成上传耗时。
3. 仅对另一个空闲对话操作。当前对话及其祖先禁止作为目标；目标或后代有运行中任务、未确认提交/推送或待处理业务操作，先等待/澄清，避免归档终止工作。脚本以 spawn/attached parent 关系二次检查。
4. 设定持久、私有、**仓库和 Skill 目录之外**的 `DATA`，例如用户工作目录下 `outputs/session-repair/<thread-id>/<timestamp>`。备份和图片包含完整私有数据，不上传远端、不随 Skill 分发，不在验收后删除或移动。

```bash
# SCRIPT 为本 Skill 内 scripts/repair_context.py 的绝对路径。
# TARGET / CURRENT 为已核实的目标及当前对话 UUID；DATA 为绝对持久路径。
python3 "$SCRIPT" inspect --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
```

`inspect` 不写数据，返回图片数量、历史序列化字节及归档状态。没有 checkpoint/没有旧图片时不继续修复；先报告不适配的原因。不要自动 `/compact`，它可能耗时且改动上下文；需要时另行取得用户同意。远程图片、未知图片字段、压缩 rollout、数据库结构变化均停止。

## 获确认后执行

说明并取得用户确认：备份完整记录、把旧 checkpoint 图片换成本地引用、短暂归档目标及后代以释放运行时、然后恢复原归档状态。告知旧图片视觉细节将按需读取；原始历史、新图片、代码和模型配置不动。用户要求“只诊断”时不执行以下步骤。

1. `prepare`：导出图片并校验 SHA256，保存完整备份及原归档状态。使用同一组参数：
   ```bash
   python3 "$SCRIPT" prepare --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
2. 读取 `plan.json` 的 `archive_states`。在第一方线程工具中再次确认整个子树空闲。通过 `set_thread_archived` 归档目标（`hostId: local`、`source: codex`、`archived: true`），**不是删除**。该操作可能影响后代。没有此工具时停在准备结果，别模拟私有 IPC、杀进程或直接写 SQLite。
3. 归档释放后执行：
   ```bash
   python3 "$SCRIPT" apply --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
   脚本重新定位归档路径，三次检查 `lsof` 无打开句柄、子树全部归档、checkpoint 与准备快照相同、备份和图片哈希正确，再原子写入。按原字节跨度补 JSON 尾部空白，保持行号/偏移，不改 history 索引库。`applied.json` 在替换前落盘以供中断恢复；另以硬链接长期保全原 inode，检查设备/inode 身份及替换后变化，供并发追加时恢复。DATA 与 rollout 须在同一文件系统；跨盘链接失败则停止原文件写入。**空闲归档仍非跨进程强锁：用户在此期间不要打开或发送目标对话；一旦有并发迹象立即停。**
4. 必须执行恢复阶段，即使准备后或写入时失败也检查现场。正常完成或确认原文件未变时，按保存状态恢复：先用第一方工具 unarchive 目标，随后按父到子顺序校正后代；原来归档的子树重新归档。每次工具可能递归影响子树，最终对照所有 ID 的原始状态逐一核验，不能只看目标。
   - 如果 `applied.json` 为 `pending`，用其中前/后哈希判断写入是否发生；同时检查 `recovery_inode` 是否包含并发新记录。该链接可在旧写入句柄延迟追加时继续变化，应长期保留；有变化时保留双方并人工合并，不盲目重试 apply。
   - 若原文件既非备份也非已验证结果、图片缺失或状态出现新后代，暂停并报告，保留备份。仅在文件完整性确认后恢复运行时；不要为“恢复侧栏”加载未知损坏状态。
5. 恢复后验证磁盘：
   ```bash
   python3 "$SCRIPT" verify --thread "$TARGET" --current-thread "$CURRENT" --data-dir "$DATA"
   ```
   要求 `disk_verified` 且 `archive_states_restored: true`。这还不等于已测响应速度。用户可自行打开并发送测试；代发消息需要用户同意，先排除旧待办/排队消息，不覆盖模型或推理设置。用 `wait_threads` 观察；未经同意不启动原业务任务。
6. 报告图片数、有效历史字节前后、备份位置、归档状态、实际响应与重试指标。无前后同任务/同条件样本就别给“提升 N 倍”。未来新图片仍会累积，按症状重新诊断，不自动反复修复。

## 回滚及异常

用同样的空闲检查与临时归档流程释放目标，再运行 `rollback`（相同参数），随后恢复归档状态。只有 rollout **整文件仍等于应用后哈希**才自动回滚；有新消息时保留它们，转人工合并方案，不覆盖新记录。禁止删除备份、修改项目代码、替换模型/代理/认证、改客户端二进制或全局设置来掩盖故障。

兼容性与数据结构细节见 [references/compatibility.md](references/compatibility.md)，仅在需要判断适配/异常时读取。

## 自测

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SKILL_DIR/tests" -v
```

测试仅使用临时合成数据库/会话；不要拿任意真实对话当自动测试样本。
