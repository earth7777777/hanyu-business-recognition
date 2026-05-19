# 销售履约与开票预警主线 V1

本仓库实现了你冻结的 V1 基线：

- 独立前端、独立后端、独立数据库（MariaDB）
- 前后端分离，业务判断主链：`Normalize -> Match -> Alert`
- 外部编排采用可替换 Adapter Layer（默认 provider: `copaw`），不承载业务判断
- OCR 主链路：`rapidocr-onnxruntime`（PDF/图片）
- 组合键模板可配置，默认遵循“数量优先，金额辅助”
- 两条预警规则可参数化
- 四类输入：
  - 订单：PDF/Excel
  - 发货：Excel
  - 付款通知单：PDF/图片
  - 发票：PDF/图片
- 轻量 Config Center：字段映射、组合键模板、规则参数、龙虾连接参数

## 目录

- `backend/`: FastAPI 后端（API、数据库、任务编排、业务链路）
- `frontend/`: 独立静态前端（任务中心）

## MariaDB 建库（示例）

```sql
CREATE DATABASE sales_warning_v1 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'hanyu_app'@'%' IDENTIFIED BY 'StrongPassword';
GRANT ALL PRIVILEGES ON sales_warning_v1.* TO 'hanyu_app'@'%';
FLUSH PRIVILEGES;
```

## 后端启动

```bash
cd backend
python3 -m pip install --break-system-packages -r requirements.txt

# 推荐：单一连接串
export DATABASE_URL='mysql+pymysql://hanyu_app:StrongPassword@127.0.0.1:3306/sales_warning_v1?charset=utf8mb4'

# 或分项变量（DATABASE_URL 为空时生效）
# export DB_HOST=127.0.0.1
# export DB_PORT=3306
# export DB_USER=hanyu_app
# export DB_PASSWORD=StrongPassword
# export DB_NAME=sales_warning_v1

python3 -m uvicorn app.main:app --reload --port 8000
```

可选 OCR 参数：

```bash
# PDF 最多 OCR 页数（默认 10）
export OCR_MAX_PAGES=10
# PDF 渲染缩放（默认 2.0）
export OCR_PDF_SCALE=2.0
```

## 前端启动

```bash
cd frontend
python3 -m http.server 5173
```

访问 [http://127.0.0.1:5173](http://127.0.0.1:5173) ，默认后端地址为 `http://127.0.0.1:8000/v1`。
如果前端经云上反向代理统一对外，页面默认会直接走同源 `/v1`，不需要再手填 `:8000`。

## 核心 API（简表）

- `POST /v1/upload-jobs`
- `POST /v1/upload-jobs/{job_id}/files`
- `POST /v1/tasks/orchestrate`
- `POST /v1/tasks/lobster-feed`（兼容别名）
- `POST /v1/intake/jobs`（外部工具创建接入任务）
- `POST /v1/intake/jobs/{job_id}/files`（外部工具投喂文件）
- `POST /v1/intake/jobs/{job_id}/run`
- `GET /v1/intake/jobs/{job_id}/status`
- `GET /v1/results/jobs/{job_id}`
- `GET /v1/results/jobs/{job_id}/records`
- `GET /v1/results/jobs/{job_id}/alerts`
- `GET /v1/results/jobs/{job_id}/export`
- `GET /v1/tasks`, `GET /v1/tasks/{task_id}`, `GET /v1/tasks/{task_id}/result`
- `GET /v1/alerts`, `GET /v1/alerts/customer-summary`
- `POST /v1/exports`
- `GET/PUT /v1/config/{key}`

## 配置键

- `field_mappings`
- `match_template`
- `rule_parameters`
- `integration_hub`
- `orchestrator_profile`
- `orchestrator_policy`
- `lobster_connector`（兼容读取）

## 外部接入鉴权（首期）

`/v1/intake/*` 与 `/v1/results/*` 使用 API Key 方式：

- `X-Client-Id`
- `X-Client-Token`

在 `integration_hub.auth_clients` 中配置可访问的 provider、允许的单据类型等策略。

## 角色

通过请求头 `X-Role` 传递：

- `upload`
- `admin`

其中配置更新接口要求 `admin`。
