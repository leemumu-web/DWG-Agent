# 共享界面组件

## 现有实现

`AppErrorBoundary.tsx` 捕获页面显示异常并提供中文恢复入口，不把异常对象写入工人可见界面；`ConnectivityBanner.tsx` 监听 online/offline；`ApiErrorAlert.tsx` 把安全解析后的 API 错误、处理建议和重试动作组合为常驻操作员提示；`ExcelInputFailurePanel.tsx` 与 `ExcelInputFailurePanel.css` 只渲染经过白名单解析、有界截断的表格输入问题；`TransferProgressBar.tsx` 统一展示批量上传/下载的字节数、总量与完成状态；`BrandLogo.tsx` 按蓝/深/浅底渲染 `/brand/logo-*.png` 品牌标识；`ui.tsx` 提供页面标题、状态和时间格式化等通用原语；`index.ts` 统一导出。

## 输入与输出

输入是浏览器状态或无领域含义的展示值，输出是全站一致的故障、状态和排版反馈。

## 边界

文件上传、生产批次、扫描处置等业务组件必须留在自己的 feature。
