# 跨层合同测试

## 现有覆盖

`test_frontend_contract.py` 读取 React 源码，核对页面按钮、HTTP 方法/路径、认证/SSE/download 和迁移后的 owner 文件；`test_excel_preview_source.py` 锁定 Excel 只使用服务端快速预览、不恢复 LuckyExcel 静态脚本；`test_docs_consistency.py` 调用文档检查器；`test_stage1_boundaries.py` 锁定已发布 Stage 1 API/状态合同。

## 证据边界

输入是前后端源码和生成文档，输出是跨层引用/命名没有因搬迁失效的静态证据；真实浏览器交互由 Playwright 负责。
