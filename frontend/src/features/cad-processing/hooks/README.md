# CAD 实时状态 Hooks

## 现有实现

`useConversionEvents.ts` 根据转换方向和源 file ID 打开 Job SSE，校验 attempt/job 身份后把权威状态补丁合并进 React Query cache，并在完成或组件卸载时关闭连接。

## 输入、输出与边界

输入是当前任务集合、文件关联和缓存 key，输出是接近实时的 pending、running、progress 和 result 更新。SSE 不保证历史 replay；断流、后台标签页和重连后的最终一致性由页面慢轮询/重新查询兜底。
