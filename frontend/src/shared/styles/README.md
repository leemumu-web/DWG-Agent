# 全局样式边界

## 现有实现

`index.css` 保存 reset、颜色/间距 token、应用壳、导航、通用页面标题、状态和响应式基础规则，由 `main.tsx` 唯一导入。

## 输入与输出

输入是 app/shared 使用的稳定 class，输出是桌面与窄屏都可用的全局视觉基线。

## 边界

`.conversion-*`、`.production-*`、`.data-console-*`、`.login-*` 等业务选择器归对应 feature；架构检查拒绝恢复旧 `src/styles.css`。
