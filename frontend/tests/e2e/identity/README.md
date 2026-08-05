# 身份与品牌 E2E

## 现有场景

`branding.spec.ts` 覆盖两处品牌标识证据：登录页使用企业联合品牌 logo（桌面 `logo-on-blue.png`、移动端 `logo-on-light.png`，favicon 指向 `logo-on-light.png`），主导航使用紧凑品牌 logo（`logo-on-dark.png`），并校验移动端不出现水平滚动。测试通过拦截 `/api/v1/auth/tokens/refresh` 以离线方式提供登录与已登录状态，不依赖真实会话。

## 输入与证据边界

输入是 `/brand/logo-*.png` 静态资源路径与身份刷新接口的模拟响应，输出是浏览器可见的品牌标识与 favicon 断言证据。测试不得依赖真实令牌或后端会话，也不得把页面可见性当作身份认证的验收；品牌资源缺失或路径漂移时，本 partition 的失败即生产品牌契约失效。
