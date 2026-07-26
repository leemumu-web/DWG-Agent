# 生产稳定性与压力验证

这组工具只通过正式 HTTP 接口提交生产流程，并只读采集服务器资源。它不会直接造业务数据，也不会修改容器、MySQL 配置或队列。

## 测试样本

- Excel 必须是实际可导入的 `.xls` 或 `.xlsx`。
- DWG 必须位于同一个文件夹内；工具只选该文件夹第一层的 `.dwg`。
- 图纸按忽略大小写后的文件名稳定排序，再精确取 `--dwg-count` 张；数量不足直接失败。
- 同一轮各项目使用相同只读样本，报告记录原名、字节数和 SHA-256。
- 基线和参数对比必须使用相同报告中的样本哈希，不得中途替换图纸。

## 凭据

账号密码只从环境变量读取：

```bash
export DWG_LOAD_CREDENTIALS_JSON='{"operator01":"<密码>","operator02":"<密码>"}'
```

不要把密码写进命令行、脚本、报告或仓库。报告会递归遮盖密码、访问令牌、DSN 和对象存储密钥。

## 启动资源采样

在生产服务器的发布目录运行：

```bash
python3 scripts/production/resource_sampler.py \
  --output /tmp/production-load-resources.jsonl \
  --summary /tmp/production-load-resources-summary.json \
  --interval 1
```

采样器记录：

- 主机 CPU、负载、可用内存、交换区和块设备累计 I/O；
- 每个容器的 CPU、内存、网络、块 I/O、PID、健康状态、重启次数和 OOM；
- MySQL 当前连接、运行连接、历史连接峰值；
- Job 的排队、运行、成功、失败和取消数量。

按 `Ctrl+C` 后会正常结束，并生成峰值摘要。单次 Docker 或 MySQL 查询失败只写入该条采样的 `errors`，不会让整段证据丢失。

## 执行真实流程

从能访问生产地址、且能读取测试样本的机器运行：

```bash
python3 scripts/production/workflow_load.py \
  --base-url http://服务器地址 \
  --accounts admin01,admin02,operator01,operator02 \
  --excel /绝对路径/构件零件清单.xlsx \
  --dwg-dir /绝对路径/生产图纸 \
  --dwg-count 30 \
  --concurrency 1,2,4 \
  --stage-timeout 3600 \
  --request-timeout 1800 \
  --report /tmp/production-workflow-load.json \
  --release-label server-production-版本
```

每个项目严格执行：

`登录 → 建项目 → 建输入批次 → 上传 Excel → 上传 DWG 文件夹 → 转换 → 冻结 → 分类 → 拆板 → 下载`

任一 HTTP 错误、阶段失败、超时、丢图、重复计数或 ZIP 异常都会使进程返回非零。拆板数量按最终业务口径核验：

- `自动通过 + 人工复核 = 拆板输入`
- `失败数 ≤ 人工复核数`
- 失败图纸是人工复核的子集，不能再单独相加
- 正式下载包只能包含 `原长` 和 `余量增长后短文件`，两个目录 DXF 数量相等
- 每个正式结果目录数量必须等于自动通过数

## 渐进压力顺序

1. 先跑单项目，确认所有阶段和下载守恒。
2. 再跑 1、2、4 个并发项目。
3. 只有 4 并发无丢图、无 OOM、无容器重启、无数据库死锁，才测试 8 项目。
4. 拆板 worker 只比较并发 1、2、4，每档至少两轮；只有 4 明显更快且 p95、I/O、内存仍稳定，才试 6。
5. 参数比较期间不得同时改镜像、样本或其他 worker 配置。
6. 最优参数确认后执行至少 30 分钟混合负载，再做无活动 Job 条件下的受控重启。

## 判定与清理

以下任一情况都不能发布：

- 项目、图纸、分类、拆板或下载数量不守恒；
- 容器出现非预期重启、OOM、worker lost 或数据库死锁；
- 可用内存持续低于 20%，交换区持续换入换出；
- CPU 或磁盘 I/O 长时间饱和，且 p95 明显恶化；
- 任何项目只在前端“看似完成”，但 Job、账本或下载不可用。

测试项目统一使用报告中的 `LOAD-...` 前缀。验收后只按该前缀和报告中的工作流编号精确删除测试项目、文件、对象与任务记录；不得重建五金手册、用户、角色或系统配置。
