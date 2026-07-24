# 共享界面组件

## 现有实现

`AppErrorBoundary.tsx` 捕获 React render 异常并提供恢复入口；`ConnectivityBanner.tsx` 监听 online/offline；`ExcelInputFailurePanel.tsx` 与 `ExcelInputFailurePanel.css` 只渲染经过白名单解析、有界截断的表格输入问题；`ui.tsx` 提供页面标题、状态/时间格式化、错误反馈与复制 request ID 等通用原语；`index.ts` 统一导出。

## 输入与输出

输入是浏览器状态或无领域含义的展示值，输出是全站一致的故障、状态和排版反馈。

## 边界

文件上传、生产批次、扫描处置等业务组件必须留在自己的 feature。
