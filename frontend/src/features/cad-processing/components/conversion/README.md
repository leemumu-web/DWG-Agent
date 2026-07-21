# 通用格式转换工作区

## 现有实现

`ConversionUploadPanel.tsx` 管理文件/文件夹选择、并发与格式提示；`ConversionFoldersPanel.tsx` 展示文件夹进度及批量删除/下载；`ConversionOverview.tsx` 汇总成功、失败、处理中和待提交；`conversionColumns.tsx` 生成源文件、Job、结果和动作列。

## 输入、输出与边界

输入由 `ConversionPage` 提供，包括方向配置、files/jobs 状态、权限和显式回调；输出是选择、提交、暂停/恢复、重试、预览、下载等用户意图。组件不直接发跨域请求，危险批量动作必须显示服务端影响范围和确认反馈。
