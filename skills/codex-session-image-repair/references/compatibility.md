# 适配边界与证据

## 已验证的数据变换

- 首记录为 `session_meta`，`payload.id` 等于目标 ID。
- 仅最后一条 `type: compacted` 的 `payload.replacement_history` 内，`type: message, role: user` 的 `content` 中 `input_image.image_url` 支持 png/jpeg/gif/webp 的 base64 data URL。
- 整个 `input_image` 项替换为 `input_text` 本地绝对路径提示。恢复这些项后，应与原 checkpoint 深度相等：其他用户文字、工具结果、opaque/encrypted summary、模型元数据不变。
- 原 transcript 图片仍在之前的 JSONL 记录中。checkpoint 之后的记录（包含新图片）按字节保留。prepare 后仅接受原快照作为完整前缀的追加；产生新 checkpoint 会被拒绝。
- JSON 行尾补空格使字节长度、所有行号及后续偏移保持不变。因此 rollout 文件大小不下降是预期行为；不要据此判断有效请求体未改善。
- 为避免最后检查与替换之间新增记录的所有副本丢失，替换前额外创建原 inode 的私有硬链接，核对设备/inode 与读取快照一致并 fsync 目录，记录路径后再写入。替换后检查该 inode 的内容与句柄；并发变化时停止并保留两个版本。rollback 同样保全待替换 inode。DATA 与 rollout 不同文件系统时停止。不可把硬链接当成不可变备份：旧 fd 可在最后检查后才追加，链接须长期保留，后续 verify/rollback 继续检查它。
- 没有跨进程排他锁：脚本锁只防止同一数据目录重复执行，真正的前提是第一方归档释放、子树空闲、lsof 无句柄、用户暂不打开目标。脚本多次检测并发，但不宣称消除所有竞态。
- 仅脚本处理目标 rollout，后代只参与归档状态恢复，不修改后代内容。
- `state_5.sqlite` 只读查询 `threads(id, rollout_path, archived)`、`thread_spawn_edges(parent_thread_id, child_thread_id)`；若存在，亦查询 `threads.attached_thread_parent_id`。不写 SQLite、WAL 或历史分页索引。
- 标准库 + macOS `/usr/sbin/lsof`；缺失/报错/无权限均停止写入。针对 symlink、身份不匹配、不支持结构、图片/备份哈希变化均保守停止。

## 恢复日志

`plan.json`：准备快照、checkpoint hash、图片清单、完整备份、原归档 flags。
`applied.json`：替换前写入 `pending`，含应用时整文件前后 hash、独立完整备份和 `recovery_inode`；校验后为 `applied_verified`。rollback 先记录 `rollback_pending` 与 `rollback_recovery_inode`，校验后标记 `rolled_back_verified`。独立 hash 备份保持不变，inode 链接用于保留可能迟到的写入。

进程中断后，保持目标释放状态，按实际文件哈希判断：

- 等于 before：文件未应用；保留该日志，恢复原归档状态，重新诊断后使用新数据目录。
- 等于 after：执行 verify；图片、备份、原 inode 均匹配，且追加 JSONL 完整有效后恢复原归档状态。
- `recovery_inode` 或 `rollback_recovery_inode` 有并发追加：保留该文件和当前 rollout，人工对账合并；自动流程不把新消息覆盖回去，也不声称已恢复到目标。
- after 前缀后已追加新记录：verify 可证明原修复前缀，自动 rollback 会停止以保留新增记录。
- 两者均不匹配：保存现场并报告，别继续覆盖。

`verify` 不发送测试消息，也不证明网络速度。日志只记录观察到的协议、重试次数及时间，不输出密钥、完整图片或聊天内容。

## 验证与限制

仓库中的测试只使用合成数据库、会话及图片，覆盖数据变换、备份、校验、并发变化与回滚保护。测试通过不代表适配所有客户端版本，也不代表实际响应加速。

本 Skill 依赖本机实际结构，不是 OpenAI 官方修复工具。每次以安装版本的实际字段、日志和句柄检查为准；结构不匹配时停在诊断。有效历史序列化字节、磁盘文件大小和真实网络请求体是不同指标，不相互替代。
