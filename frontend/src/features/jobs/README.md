# 任务管理

## 现有实现

`JobsPage.tsx` 提供筛选、分页、详情、取消、重试和结果动作；`JobTimeline.tsx` 展示 attempt/step；`useJobEvents.ts` 订阅 SSE 并回填 Query cache；`jobs.api.ts`、`results.api.ts` 与 `job.ts`、`result.ts` 定义传输合同。

## 业务流

输入是持久化 Job/Step/Result、当前用户权限和 SSE 当前快照，输出是任务可观测、恢复及结果下载入口。断流时回到权威查询，重试后必须跟随新 attempt。

## 边界

具体 CAD/Excel 参数和业务结果展示归调用 feature；SSE 当前没有历史 replay。
