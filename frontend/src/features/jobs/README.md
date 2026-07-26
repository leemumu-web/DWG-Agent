# 任务管理

## 现有实现

独立任务页已并入生产流程；`useJobEvents.ts` 订阅 SSE 并回填 Query cache，`jobs.api.ts`、`results.api.ts` 与 `job.ts`、`result.ts` 为文件转换、Excel 整理、工作流和仪表盘定义任务传输合同。

`index.ts` 是 Job hooks、请求和类型的稳定出口；跨 feature 只从这里引用 Job 能力，避免重新出现独立任务页或绑定私有查询键。
`JobProgressBar.tsx` 统一解释确认里程碑、活动态和失败终止状态；
`getJobDiagnostics` 读取安全的阶段与耗时，前端不请求或展示服务器原始日志。

## 业务流

输入是持久化 Job/Step/Result、当前用户权限和 SSE 当前快照，输出由所属文件转换或生产流程页面展示任务状态、恢复动作和结果下载。断流时回到权威查询，重试后必须跟随新 attempt。

## 边界

本 feature 不拥有页面路由；具体 CAD/Excel 参数和业务结果展示归调用 feature。SSE 当前没有历史 replay。
