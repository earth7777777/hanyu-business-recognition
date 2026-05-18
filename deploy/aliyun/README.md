# 阿里云第一版落地模板

本目录只放 `phase-6` 第一版上云需要的固定模板，避免后续靠聊天记忆重拼。

## 目录口径

- 运行目录：`/srv/hanyu-app/current`
- 上传目录：`/srv/hanyu-app/shared/uploads`
- 备份目录：`/srv/hanyu-app/shared/backups`
- 日志目录：`/srv/hanyu-app/shared/logs`

## 模板说明

- `Caddyfile.http.example`
  - 第一版先按公网 IP 的 HTTP 入口落地
  - 主网址 `/` 进入员工登录页，`/app/` 进入提醒中心
  - 隐藏后台固定放在 `/ops-admin/`
  - 前端静态文件和后端 `/v1` 统一走同一入口
- `Caddyfile.https.example`
  - 域名和 HTTPS 收口后，直接切到这份模板
  - 继续保留“主网址员工入口 + `/ops-admin/` 隐藏后台”双入口结构
- `hanyu-backend.service.example`
  - 后端用 `systemd + uvicorn` 常驻运行
  - 使用 `/srv/hanyu-app/current/backend/.env` 作为环境变量文件

## 本地开发与云上入口的区别

- 本地 `frontend` 跑在 `5173` 时，页面默认仍连 `:8000/v1`
- 云上经 `Caddy` 对外时，页面默认直接走同源 `/v1`
- 第一阶段员工入口需要同源 cookie：
  - 本地开发建议直接从 `frontend/` 起静态页；
  - 云上则通过 `Caddy + 同源 /v1` 直接使用。
