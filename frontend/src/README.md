# Frontend 模块设计与约束

`frontend/src` 是 React 18 + Vite + TypeScript + Ant Design 5 前端应用。它负责用户工作台、交易操作、AI 任务观察、数据管理和配置入口，不直接实现后端业务规则。

## 目录职责

- `App.tsx`：路由表和登录保护。
- `layouts/`：主布局、导航和页面框架。
- `pages/`：业务页面入口。
- `features/`：跨页面复用的业务组件。
- `api/`：后端 API 封装，统一复用 `apiClient`。
- `services/`：WebSocket、session 等前端服务。
- `store/`：客户端状态。
- `theme/`：主题 token 和模式切换。
- `i18n/`、`locales/`：语言配置和本地兜底文案。
- `utils/`：错误格式化、API 历史、日志等工具。

## 设计约束

- API 请求优先新增到 `api/*.ts` 并复用 `apiClient`；调用方按业务体处理返回值，不再取 `.data`。
- 认证 token 同时涉及 `localStorage.token` 和 `useSessionStore`，修改登录链路要一起检查。
- WebSocket 先换 ticket，不把 JWT 放入 WS URL。
- UI 默认使用 Ant Design 5、`theme.useToken()`、CSS variables 和现有组件；项目没有 Tailwind 配置。
- 文案优先使用 `react-i18next` 的 `t(...)`；翻译来源是后端 `/api/v1/general/i18n/{{lng}}`。
- 图表优先复用 `features/market/echartsCore.ts`。
- 新共享层前先查 `api`、`utils`、`services`、`theme`、`components`、`features`、`pages` 是否已有入口。
- 前端只表达交互状态和展示逻辑，交易规则、风控、复盘和 AI 决策必须以后端结果为准。

## 验证

- 前端改动运行：`cd frontend && npm run lint && npm run typecheck && npm run build`
