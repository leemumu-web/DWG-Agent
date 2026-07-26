# DXF 分类固定进程池设计

## 背景

服务器 r28 首轮双账号、每账号 40 张图纸的生产流程通过。第二轮在 DWG→DXF 80/80 成功后，两个分类 Job 长时间停留在 `queued`。

现场证据表明：

- MySQL broker 中两条 `dxf_classification` 消息已经被 worker 取走；
- worker 主进程和容器健康检查正常，无 MySQL 锁等待或连接耗尽；
- 新增 prefork 子进程均阻塞在 Billiard ACK 队列的共享写锁，业务任务尚未开始；
- 首轮结束后的 Celery autoscale 缩容发生在同一 worker 生命周期内，随后扩容出的进程无法取得已经遗留的信号量。

因此问题不是分类算法、存储或数据库性能，而是分类 worker 动态增删 prefork 子进程后的池级死锁。现有进程存活型健康检查无法发现这种状态。

## 目标

- 分类 worker 在多个连续生产批次之间保持可执行，不因空闲缩容后再次扩容而死锁。
- 继续允许不同项目并行分类，但并发容量在进程启动时固定。
- 本地脚本、Compose、状态检查、示例配置和文档使用同一套配置语义。
- 不改变拆板算法、任务幂等合同或拆板并发上限。

## 方案

`dxf_classification` 保持独立 Celery 队列和单个受管 worker 节点。取消 `--autoscale=MAX,MIN`，统一使用：

```text
--concurrency=${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}
```

配置只保留 `DXF_CLASSIFICATION_WORKER_CONCURRENCY` 一个正整数。本地默认值为 3；具有 16 个逻辑 CPU、62 GiB 内存的生产服务器显式设置为 4。固定池在启动时一次性建立全部子进程，运行中不再因空闲而缩容，也不会在下一批任务到来时重新扩容。

`DWG_WORKER_CONCURRENCY` 继续向运行状态报告固定容量；删除仅服务于旧 autoscale 展示的 `DWG_WORKER_AUTOSCALE`。状态检查要求进程命令包含准确的 `--concurrency=N`，不再接受旧的 `--autoscale` 参数。

## 未采用的方案

1. 令 autoscale 的最小值等于最大值：仍引入 autoscaler 线程和两变量配置，语义不必要且容易被后续误改。
2. 发现 queued 超时后自动重启：只能在故障发生后恢复，期间消息和 Job 状态需要额外补偿，不能消除根因。
3. 改用线程池：分类包含 CPU 和第三方 DXF 解析逻辑，线程安全及吞吐边界没有足够证据。

## 验证

1. 测试先要求脚本和 Compose 使用固定并发，并确认旧实现失败。
2. 修改启动和状态脚本后运行基础设施测试、Compose 渲染检查和完整 quick gate。
3. 重新构建受保护镜像并部署服务器。
4. 使用两个不同账号连续执行两轮、每账号 40 张 DWG 的完整转换、分类、拆板和下载；每轮均要求 2/2 项目成功、每个正式包包含 80 个 DXF。
5. 采样期间要求所有容器重启数为 0、OOM 为 false、交换区不被使用，并确认拆板工作目录在任务结束后清空。

