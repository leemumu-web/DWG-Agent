# DWG-Agent 前端交互探索记录

> 探索日期: 2026-07-02 ∼ 2026-07-03
> 方法: 通过 Nginx :8080 模拟真实用户操作流程
> 参考: DWG-Agent企业平台技术规范.md §5 (前端技术规范)

---

## 一、前端架构概览

### 技术栈
- React 19.2.7 + TypeScript 6.0.3 + Vite 8.1.3
- Ant Design 6.5.0 (zhCN locale)
- React Router 7.18.1 (BrowserRouter)
- TanStack Query 5.101.2
- Zustand 5.0.14 (仅auth.store)
- Axios 1.18.1

### 构建产物
- `index.html`: 490 bytes
- `index-DGxcZPzO.js`: 1.1 MB (React + Ant Design 全量打包)
- `index-C-iQYiOT.css`: 766 bytes
- `logo.png`: 77 KB
- `VITE_API_BASE_URL`: 空字符串 (Nginx反代模式, 使用相对路径)

---

## 二、用户操作流程模拟

### 流程1: 首次访问 → 登录 → 工作台

```
用户打开 http://localhost:8080
  → Nginx 返回 index.html (490 bytes, no-cache)
  → 浏览器加载 JS bundle (1.1MB, 7天缓存)
  → React 渲染 <App> → <ConfigProvider locale={zhCN}> → <QueryClientProvider> → <BrowserRouter>
  → 路由匹配: / → <Navigate to="/dashboard" />
  → <RequireAuth> 检查: accessToken=null → <Navigate to="/login" />
  → 用户看到登录表单 (username预填"admin", password预填"admin123456")
  → 用户点击[登录] → POST /api/v1/auth/sessions → 201
  → auth.store: setSession(token, user)
    → localStorage.setItem('dwg_access_token', token)
    → localStorage.setItem('dwg_user', JSON.stringify(user))
  → navigate('/dashboard')
  → DashboardPage: useAuthStore → 显示 "当前用户：系统管理员（admin）"
```

### 流程2: 浏览项目列表

```
用户点击侧边栏 [项目] → /projects
  → ProjectsPage mount
  → useQuery({queryKey:['projects'], queryFn: listProjects})
  → GET /api/v1/projects → 200
  → Ant Design <Table> 渲染: ID | 编号 | 名称 | 状态
  → 7个项目显示 (当前数据库状态)
  → 无分页控件 (page_size=total, 一次返回全部)
  → 无搜索/筛选功能
  → 无创建项目按钮/表单
  → 无项目详情链接 (缺失 /projects/:projectId 路由!)
```

### 流程3: 文件管理 + 上传

```
用户点击侧边栏 [文件] → /files
  → FilesPage mount
  → <FileUpload> 组件渲染 [上传 DWG] 按钮 (accept=".dwg")
  → useQuery({queryKey:['files'], queryFn: listFiles})
  → GET /api/v1/files → 200
  → <Table> 渲染: ID | 文件名 | 大小 | 状态 (30个文件)

用户点击 [上传 DWG] → 文件选择器打开 (filter: .dwg)
  → 选择文件 → antd Upload customRequest
  → uploadDwg(file) → FormData('upload', file)
  → POST /api/v1/files → 201
  → message.success('上传成功')
  → onUploaded() → query.refetch() → 表格刷新

DWG头部验证流程:
  - 扩展名检查: .dwg必须在允许列表
  - 文件头检查: 前6字节必须是 AC + 4位数字
  - 大小限制: 512MB
  - SHA256/MD5计算
  - ⚠️ BUG: 0字节文件绕过头部验证 (while循环不执行)
```

### 流程4: 任务创建与监控

```
用户点击侧边栏 [任务] → /jobs
  → JobsPage mount
  → [创建框架冒烟任务] 按钮
  → useQuery({queryKey:['jobs'], queryFn: listJobs, refetchInterval: 3000})
  → GET /api/v1/jobs → 200
  → <Table> 渲染: ID | 任务类型 | 管线 | 状态 | 进度

用户点击 [创建框架冒烟任务]
  → createFrameworkSmokeJob()
  → POST /api/v1/jobs {task_type:'framework_smoke_test', precision_level:'normal', params:{source:'frontend'}}
  → 202 Accepted
  → message.success('已创建框架冒烟任务')
  → query.refetch()

后台: BackgroundTasks → run_local_stub_job
  → status: queued → running → succeeded (同步完成, <1ms)
  → job_steps: dispatch_stub_worker + write_stub_result
  → analysis_results: 生成stub结果JSON

前端: 3秒轮询 → 状态自动更新 → 显示 succeeded 100%
```

### 流程5: 占位页面体验

```
用户点击 [图纸] → DrawingsPage
  → 无API调用
  → 显示: <Card>本页面已占位，后续接入对应 RESTful API 列表与表单。</Card>

用户点击 [复核] → ReviewsPage
  → 同占位

用户点击 [用户管理] → UsersPage
  → 同占位

用户点击 [审计日志] → AuditLogsPage
  → 同占位
```

### 流程6: 页面刷新恢复

```
用户按F5刷新 → 浏览器重新请求 /
  → SPA重新加载
  → auth.store初始化: 从localStorage读取dwg_access_token + dwg_user
  → RequireAuth检查: token存在 → 通过
  → 回到之前页面
  → TanStack Query自动重新请求数据
```

### 流程7: 登出

```
用户点击登出
  → clearSession()
  → localStorage.removeItem('dwg_access_token')
  → localStorage.removeItem('dwg_user')
  → DELETE /api/v1/auth/sessions/current → 204
  → Navigate to /login
  → ⚠️ JWT token仍有效! 服务端未作废
```

---

## 三、前端页面实现度分析

### 与 Spec §5.2 对比

| 路由 | Spec定义 | 实际状态 | 评分 |
|------|----------|----------|------|
| `/login` | 登录页 | ✅ 完整实现 (表单 + 预填 + 错误提示) | 100% |
| `/dashboard` | 工作台 | ✅ 基础 (显示用户名+占位信息) | 40% |
| `/projects` | 项目列表 | ✅ 基础 (表格只读, 无CRUD表单) | 30% |
| `/projects/:projectId` | 项目详情 | ❌ **缺失** | 0% |
| `/drawings/:drawingId` | 图纸详情 | ❌ **缺失** | 0% |
| `/files` | 文件管理 | ✅ 基础 (上传+表格) | 50% |
| `/jobs` | 任务列表 | ✅ 基础 (创建+表格+轮询) | 40% |
| `/jobs/:jobId` | 任务详情 | ❌ **缺失** | 0% |
| `/reviews` | 待复核列表 | ❌ 占位 (仅文字提示) | 5% |
| `/admin/users` | 用户管理 | ❌ 占位 | 5% |
| `/admin/roles` | 角色权限 | ❌ **缺失** (路由不存在) | 0% |
| `/admin/audit-logs` | 审计日志 | ❌ 占位 | 5% |
| `/profile` | 个人中心 | ❌ **缺失** (路由不存在) | 0% |
| **总体** | **13个路由** | **5完整+4占位+5缺失** | **~35%** |

---

## 四、前端组件实现度

| 组件 | 状态 | 说明 |
|------|------|------|
| FileUpload | ✅ 完整 | antd Upload, .dwg过滤, 成功/失败消息 |
| PermissionGuard | ⚠️ 部分 | RequireAuth检查token, PermissionGuard是no-op(不检查权限) |
| AgentSteps | ❌ Stub | `<div>AgentSteps placeholder</div>` |
| DrawingPreview | ❌ Stub | `<div>DrawingPreview placeholder</div>` |
| JobTimeline | ❌ Stub | `<div>JobTimeline placeholder</div>` |
| ResultPanel | ❌ Stub | `<div>ResultPanel placeholder</div>` |
| ReviewPanel | ❌ Stub | `<div>ReviewPanel placeholder</div>` |
| TaskInput | ❌ Stub | `<div>TaskInput placeholder</div>` |

---

## 五、前端API客户端实现度

| 模块 | 状态 | 说明 |
|------|------|------|
| client.ts | ✅ 完整 | Axios实例, Bearer token注入, ApiEnvelope/PageEnvelope类型 |
| auth.api.ts | ✅ 完整 | login(), getMe() |
| projects.api.ts | ✅ 基础 | listProjects() 仅查询 |
| files.api.ts | ✅ 完整 | listFiles(), uploadDwg() |
| jobs.api.ts | ✅ 完整 | listJobs(), createFrameworkSmokeJob() |
| agent-runs.api.ts | ❌ Stub | 仅 `export { apiClient }` |
| drawings.api.ts | ❌ Stub | 仅 `export { apiClient }` |
| results.api.ts | ❌ Stub | 仅 `export { apiClient }` |
| reviews.api.ts | ❌ Stub | 仅 `export { apiClient }` |
| roles.api.ts | ❌ Stub | 仅 `export { apiClient }` |
| users.api.ts | ❌ Stub | 仅 `export { apiClient }` |

---

## 六、前端发现的问题

### F1. Nginx /admin 与SPA路由冲突
- **现象:** 直接访问 `http://localhost:8080/admin/users` 返回 Nginx 404，不是SPA
- **原因:** Nginx `location ~ ^/(admin|...)` 在 SPA fallback 之前匹配
- **影响:** 用户无法通过URL直接访问或刷新管理页面，只能通过侧边栏导航
- **修复:** 将/admin路径从Nginx拦截列表中移除，或改用更精确的pattern

### F2. PermissionGuard 不检查权限
- **代码:** `<PermissionGuard>{children}</PermissionGuard>` — 无任何检查逻辑
- **影响:** 所有用户可看到所有菜单项，前端权限控制完全失效
- **依赖:** 仅靠后端API权限控制（符合spec"最终权限由后端决定"）

### F3. Token存localStorage
- **风险:** XSS可窃取token
- **Spec要求:** HttpOnly Cookie + refresh token
- **当前:** localStorage明文存储，无自动刷新

### F4. 登录表单预填密码
- **代码:** `initialValues={{ username: 'admin', password: 'admin123456' }}`
- **风险:** 开发便利但生产环境应移除

### F5. 无Token自动刷新
- **行为:** 30分钟过期后用户需手动重新登录
- **缺失:** refresh token端点虽存在但返回501

### F6. JS Bundle过大
- **大小:** 1.1MB (gzip后估计~350KB)
- **原因:** Ant Design 6 + React 19 全量打包，无代码分割
- **影响:** 首屏加载慢，尤其在移动网络

### F7. 无错误边界
- **缺失:** 无ErrorBoundary组件
- **影响:** JS运行时错误导致白屏

### F8. 无加载状态
- **缺失:** TanStack Query的isLoading/isError状态未在UI中体现
- **用户看到:** 数据到达前表格为空

---

## 七、前端安全测试

| 测试项 | 结果 |
|--------|------|
| XSS存储 (<script>标签存入real_name) | ⚠️ 原始存储, React自动转义显示安全 |
| CSRF (无CSRF token) | ⚠️ 依赖CORS + JWT Bearer |
| localStorage token窃取 | ❌ XSS可读 |
| 登录限流 (Nginx 2r/s burst 3) | ✅ 第5次返回429 |
| 敏感路径 (/admin) 拦截 | ✅ Nginx返回404 |
| 隐藏文件 (/.env) 访问 | ✅ Nginx返回403 |
| CORS预检 (OPTIONS) | ✅ 正确返回allow headers |

---

## 八、结论

DWG-Agent 前端在 Stage 1 完成了最小可用骨架：登录、项目列表、文件上传、任务创建与监控。但距离 spec §5.2 定义的完整前端还有很大差距。

**核心缺失:**
1. 5个spec定义路由未实现(项目详情/图纸详情/任务详情/角色管理/个人中心)
2. 4个占位页面需接入真实API
3. 6个核心组件为stub(AgentSteps/DrawingPreview/JobTimeline/ResultPanel/ReviewPanel/TaskInput)
4. 6个API客户端模块为stub
5. PermissionGuard无实际权限检查
6. Token管理不符合安全规范

**前端交互主链路完整性:** 登录→工作台→项目列表→文件上传→任务创建→状态轮询 ✅
