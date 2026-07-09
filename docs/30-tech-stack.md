# 技术事实

> 这是项目内容文件，不是规则文件。这里只写已稳定或已查明的技术事实，不写猜测和想象。

## 前端
- 当前项目采用前后端分离。
- 当前前端与后端分开维护，不把业务规则写进页面。
- 当前前端目录独立存在，启动方式是进入 `frontend/` 目录后，用本地静态服务启动：
  - `python3 -m http.server 5173`
- 当前前端已拆成两套静态入口：
  - `frontend/index.html` + `frontend/app/`：员工登录页与提醒中心；
  - `frontend/ops-admin/`：隐藏管理员后台。
- 当前员工侧前端新增的职责是：
  - 手机号 + 密码登录；
  - 查看提醒总览；
  - 查看未发货 / 未开票提醒；
  - 查看提醒详情与原始行来源；
  - `超60天没开票` 的客户排行、客户页和 `按客户看 / 按单子看` 切换。
- 当前管理员侧前端继续承担：
  - 文件上传入口；
  - 处理结果查看；
  - 预警结果查看；
  - 员工账号管理；
  - 客户提醒开关与最近操作记录查看。
- 当前员工侧前端已补最小 PWA 壳：
  - `manifest.webmanifest`
  - `sw.js`
  - 安装到桌面入口
- 当前前端不是业务大脑；业务识别、匹配、预警、消警逻辑都放在后端。

## 后端
- 当前后端为独立服务，不依附 Frappe 运行。
- 当前后端使用 Python 运行，启动方式已查明为：
  - 在 `backend/` 目录执行 `python3 -m uvicorn app.main:app --reload --port 8000`
- 当前后端启动时会先执行 `init_db()`，其中包含一次生命周期回填：
  - 会补齐 `uploaded_files / normalized_records` 的生命周期默认值；
  - 这一步必须同时识别 `special_case_retained`，不能只靠 `archived_at / deleted_at` 粗暴回填。
- 当前后端负责的主链是：
  - 上传
  - 识别 / 判型
  - 入库
  - 匹配
  - 预警 / 消警
- 当前后端已新增一条独立的 viewer 登录 / 查看链：
  - 员工登录 cookie 会话；
  - 员工提醒总览；
  - 员工提醒列表 / 详情 / 原始行来源；
  - `超60天没开票` 的客户聚合列表与客户详情；
  - 管理员侧员工账号管理；
  - 管理员侧客户提醒开关与日志。
- 当前后端采用开放接入层（Adapter Layer）思路，外部工具接入不写死到单一平台。
- 当前后端已接入 MariaDB 路线，不再以 SQLite 作为正式运行数据库。
- 当前后端已落地的关键能力包括：
  - 文件双保险判型
  - 高置信错分拦截
  - 预警生命周期 `open / resolved`
  - 补传资料后自动重算与自动消警

## 数据库
- 当前正式数据库路线是 MariaDB。
- 当前数据库连接由环境变量驱动，支持两种方式：
  - `DATABASE_URL`
  - `DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME`
- 当前后端数据库驱动已接入 `pymysql`。
- 当前 SQLAlchemy 层已按 MariaDB 路线调整，`JSON` 类型使用通用 `sqlalchemy.JSON`，不再绑定 sqlite 方言。
- 当前数据库连接池参数已纳入后端配置，已知包含：
  - `pool_pre_ping`
  - `pool_recycle`
  - `pool_size`
  - `max_overflow`
- 当前业务数据库骨架按 6 张核心表理解：
  - 订单主表
  - 订单明细表
  - 发货明细表
  - 付款通知单明细表
  - 正式发票明细表
  - 原始文件表
- 当前为员工提醒中心新增了 3 张辅助表：
  - `viewer_accounts`
  - `viewer_sessions`
  - `viewer_alert_reads`
- [2026-04-12 09:30 (Asia/Shanghai)] 第二段已再新增 2 张提醒静音辅助表：
  - `viewer_customer_alert_settings`
  - `viewer_customer_alert_setting_logs`
- 当前客户提醒开关表的粒度固定为：
  - `customer_key + alert_type`
  - `customer_key` 只做去首尾空格 / 连续空白折叠 / 小写化，不做集团合并或模糊归并。
- 当前数据库与规则层的口径是：
  - 数据库存真数据和状态
  - 规则写在后端，不写进数据库
- 当前已确认后续必须正式接入的订单状态字段包括：
  - 分录行号
  - 关闭状态
  - 行关闭状态
  - 出库状态
  - 行出库状态
  - 最近出库日期
  - 行已执行已出库数量
  - 行已开票数量
  - 行未开票数量
  - 行开票状态
- [2026-05-18 23:57 (Asia/Shanghai)] 已只读核查阿里云生产库 `sales_warning_v1`：
  - `normalized_records` 物理字段中没有 `order_outbound_status` 或 `line_outbound_status`；
  - 现有 `document_type=order` 记录共 47 条；
  - 现有 order 记录的 `payload_json.core/ext` 中没有 `出库状态 / 行出库状态` 对应字段；
  - `config_entries.field_mappings` 中没有 `order_outbound_status / line_outbound_status` 映射；
  - `config_entries.rule_parameters` 当前只有旧的 `ship_after_no_finance_days = 60` 等规则项，没有“必须整单发完后才提醒”开关。
- [2026-05-18 23:57 (Asia/Shanghai)] 基于当前表结构，本次 `出库状态 / 行出库状态` 升级优先按 `payload_json.core/ext` 与 `config_entries` 扩展处理，不默认新增物理列；如施工中发现必须新增物理列，需要另行说明原因。
- [2026-05-19 11:13 (Asia/Shanghai)] 本地 phase-8 施工采用 `payload_json.core/ext` 扩展，不新增 `normalized_records` 物理列：
  - `core.order_outbound_status`
  - `core.line_outbound_status`
  - `ext.order_outbound_status_raw`
  - `ext.line_outbound_status_raw`
  - 默认字段映射和既有 `config_entries.field_mappings.order` 补齐逻辑同步支持 `出库状态 / 行出库状态`。
- [2026-05-19 13:16 (Asia/Shanghai)] 本地 phase-9 已新增 `rule_parameters.ship_after_no_finance_require_order_fully_outbound`：
  - 默认值为 `false`；
  - `false` 时，`超60天没开票` 默认只要求产品行 `line_outbound_status = fully_outbound`；
  - `true` 时，额外要求整单 `order_outbound_status = fully_outbound`；
  - 本轮只改本地代码和本地配置默认/回填逻辑，未改阿里云和云端数据库。

## 部署 / 运行环境
- 当前项目先按独立主线运行，不先并进原材料仓库/Frappe 老系统。
- 当前开发与施工环境以 WSL / Linux 路径为准，已有项目路径示例为：
  - `/mnt/e/sandbox/hanyu-business-recognition/`
- 当前本地运行方式是：
  - 前端本地起静态服务
  - 后端本地起 Uvicorn 服务
  - MariaDB 单独运行
- 当前前端默认后端地址策略是：
  - 本地开发若页面跑在 `5173`，默认继续指向 `http://当前主机:8000/v1`
  - 云上若页面已通过同一入口对外，默认直接走同源 `/v1`
  - 目的不是隐藏接口，而是让本地开发和云上单入口都尽量避免手工改地址
- [2026-04-12 09:30 (Asia/Shanghai)] 当前 `viewer` 第二段已查明并稳定的接口补充有：
  - `GET /v1/viewer/uninvoiced/customers`
  - `GET /v1/viewer/uninvoiced/customer-detail`
  - `GET /v1/admin/viewer-reminder-settings`
  - `PUT /v1/admin/viewer-reminder-settings`
- 当前 `phase-7` 已把云上静态入口固定为：
  - `/` -> 员工登录页
  - `/app/` -> 提醒中心
  - `/ops-admin/` -> 隐藏管理员后台
- 当前后端上传目录已支持用环境变量 `STORAGE_DIR` 单独指定，不再只能跟着代码目录走。
- 当前顶部“批次汇总”和底部“审计列表”是两次独立请求：
  - 审计区已有单独刷新按钮
  - 批次汇总也已补单独刷新按钮，避免只能靠切换筛选强行重拉
- 当前自动归档执行链的运行位置是：
  - 编排任务 `orchestrate` 跑完匹配与预警后，会继续触发自动归档
  - 最近一次自动归档的时间、状态、归档数量，现已真实回写到 `operations_runtime_status.archive_run`
- 当前项目优先顺序不是先上云，而是先把本地真实文件整链跑透。
- 当前 `phase-6` 已查明的阿里云第一版落地口径是：
  - 复用现有 `Caddy` 做单入口；
  - 前端直接发静态文件；
  - 后端走 `systemd + uvicorn`；
  - 数据库走云上 `MariaDB`；
  - 第一版不额外引入 `nginx` 和 `node`。
- [2026-04-10 10:12 (Asia/Shanghai)] 当前 `phase-6` 云端基础运行环境已实际跑通：
  - ECS 用户为 `ecs-user`，本地 SSH 已可直连；
  - 云端已补 `2G swap`；
  - 云端 MariaDB 已安装并运行，业务库为 `sales_warning_v1`；
  - 云端代码目录为 `/srv/hanyu-app/current`，共享上传、备份、日志目录放在 `/srv/hanyu-app/shared/`；
  - 后端服务名为 `hanyu-backend.service`，运行方式为 `systemd + uvicorn app.main:app --host 127.0.0.1 --port 8000`；
  - 公网入口当前为 `http://121.40.122.51/`，由 Caddy 发前端静态文件，并把 `/v1/*` 与 `/health` 反向代理到后端；
  - 已补齐 OCR / OpenCV 在云端运行所需的 Ubuntu `libGL` 运行库。
- [2026-05-18 23:57 (Asia/Shanghai)] Mac 本机已新增专用 SSH key，可从当前机器登录 `ecs-user@121.40.122.51` 做只读核查；私钥只在本机，不写入项目文件、Notion 或 Git。
- [2026-05-18 23:57 (Asia/Shanghai)] 云端当前运行目录 `/srv/hanyu-app/current` 只读核查到的提交为 `27bf9fc`，后端 `.env` 位于云端私有文件，不进 Git。
- 当前系统支持两类入口：
  - 自己软件的人工作业入口
  - 外部工具投喂入口
- 当前系统结果先在自己软件里形成，再决定是否人工转发或由外部工具分发。

### Mac 本地位置总账与启动方式
- [2026-05-05 18:07 (Asia/Shanghai)] Mac 本地项目代码主文件夹：
  - `/Users/yue/Projects/hanyu-business-recognition/`
- [2026-05-05 18:07 (Asia/Shanghai)] 后端代码：
  - `/Users/yue/Projects/hanyu-business-recognition/backend/`
  - 职责：连接数据库、处理上传、识别、入库、提醒。
- [2026-05-05 18:07 (Asia/Shanghai)] 前端页面：
  - `/Users/yue/Projects/hanyu-business-recognition/frontend/`
  - 职责：浏览器里看到的员工入口和管理员后台。
- [2026-05-05 18:07 (Asia/Shanghai)] 本地启动脚本目录：
  - `/Users/yue/Projects/hanyu-business-recognition/scripts/`
  - 以后月总自己启动，优先用这里的脚本，不需要手敲很长命令。
- [2026-05-05 18:07 (Asia/Shanghai)] 项目 Python 工具箱：
  - `/Users/yue/Projects/hanyu-business-recognition/backend/.venv/`
  - 这里装的是本项目自己的 Python 依赖，不是乱装到电脑全局。
- [2026-05-05 18:07 (Asia/Shanghai)] 后端依赖清单：
  - `/Users/yue/Projects/hanyu-business-recognition/backend/requirements.txt`
- [2026-05-05 18:07 (Asia/Shanghai)] 本地后端配置文件：
  - `/Users/yue/Projects/hanyu-business-recognition/backend/.env`
  - 这里保存本地数据库连接信息；不要把里面的内容复制到聊天、Notion 或 Git。
- [2026-05-05 18:07 (Asia/Shanghai)] Python 3.12 程序位置：
  - `/opt/homebrew/bin/python3.12`
  - 这是电脑级工具。
- [2026-05-05 18:07 (Asia/Shanghai)] MariaDB 程序位置：
  - `/opt/homebrew/opt/mariadb/`
  - 这是电脑级数据库程序。
- [2026-05-05 18:07 (Asia/Shanghai)] MariaDB 本地数据目录：
  - `/opt/homebrew/var/mysql`
  - 这是数据库真正存数据的地方；不要手动移动，不要放进 Git，不要放进网盘同步。
- [2026-05-05 18:07 (Asia/Shanghai)] Mac 本地业务数据库名：
  - `sales_warning_v1`
- [2026-05-05 18:07 (Asia/Shanghai)] 数据库备份文件夹：
  - `/Users/yue/DatabaseBackups/hanyu-business-recognition/`
  - 这里只放 `.sql` 备份文件，不放项目代码。
- [2026-05-05 18:07 (Asia/Shanghai)] 本次阿里云数据库备份文件：
  - `/Users/yue/DatabaseBackups/hanyu-business-recognition/sales_warning_v1_aliyun_20260505_1547.sql`
- [2026-05-05 18:07 (Asia/Shanghai)] 本次校验文件：
  - `/Users/yue/DatabaseBackups/hanyu-business-recognition/sales_warning_v1_aliyun_20260505_1547.sql.sha256`

### Mac 本地启动脚本
- [2026-05-05 18:07 (Asia/Shanghai)] 启动数据库：
  - `/Users/yue/Projects/hanyu-business-recognition/scripts/start-db-local.sh`
- [2026-05-05 18:07 (Asia/Shanghai)] 启动后端：
  - `/Users/yue/Projects/hanyu-business-recognition/scripts/start-backend-local.sh`
- [2026-05-05 18:07 (Asia/Shanghai)] 启动前端：
  - `/Users/yue/Projects/hanyu-business-recognition/scripts/start-frontend-local.sh`
- [2026-05-05 18:07 (Asia/Shanghai)] 停止数据库：
  - `/Users/yue/Projects/hanyu-business-recognition/scripts/stop-db-local.sh`

### 月总自己启动步骤
- [2026-05-05 18:07 (Asia/Shanghai)] 打开 Mac 的“终端”。建议开 3 个终端窗口，分别启动数据库、后端、前端。
- [2026-05-05 18:07 (Asia/Shanghai)] 第 1 个窗口：进入项目目录。
  ```bash
  cd /Users/yue/Projects/hanyu-business-recognition
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 第 1 个窗口：启动数据库。
  ```bash
  ./scripts/start-db-local.sh
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 数据库窗口启动后不要关。它停在那里是正常的。
- [2026-05-05 18:07 (Asia/Shanghai)] 第 2 个窗口：进入项目目录。
  ```bash
  cd /Users/yue/Projects/hanyu-business-recognition
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 第 2 个窗口：启动后端。
  ```bash
  ./scripts/start-backend-local.sh
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 后端窗口启动后不要关。看到 `Uvicorn running on http://127.0.0.1:8000`，表示后端起来了。
- [2026-05-05 18:07 (Asia/Shanghai)] 第 3 个窗口：进入项目目录。
  ```bash
  cd /Users/yue/Projects/hanyu-business-recognition
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 第 3 个窗口：启动前端。
  ```bash
  ./scripts/start-frontend-local.sh
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 前端窗口启动后不要关。它负责把页面发给浏览器。
- [2026-05-05 18:07 (Asia/Shanghai)] 三个窗口都启动后，员工登录页：
  - `http://127.0.0.1:5173/`
- [2026-05-05 18:07 (Asia/Shanghai)] 管理员后台：
  - `http://127.0.0.1:5173/ops-admin/`
- [2026-05-05 18:07 (Asia/Shanghai)] 后端健康检查：
  - `http://127.0.0.1:8000/health`
  - 如果看到 `{"ok":true}`，说明后端活着。

### Mac 本地停止步骤
- [2026-05-05 18:07 (Asia/Shanghai)] 前端窗口：按 `Control + C` 停止。
- [2026-05-05 18:07 (Asia/Shanghai)] 后端窗口：按 `Control + C` 停止。
- [2026-05-05 18:07 (Asia/Shanghai)] 数据库停止：另开一个终端窗口，进入项目目录。
  ```bash
  cd /Users/yue/Projects/hanyu-business-recognition
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 然后运行停止数据库脚本。
  ```bash
  ./scripts/stop-db-local.sh
  ```
- [2026-05-05 18:07 (Asia/Shanghai)] 如果只是关机或重启电脑，下次重新按“月总自己启动步骤”启动即可。

### Mac 本地启动失败时先看哪里
- [2026-05-05 18:07 (Asia/Shanghai)] 员工页打不开：先看第 3 个前端窗口还在不在。
- [2026-05-05 18:07 (Asia/Shanghai)] 页面显示 `Failed to fetch`：先打开 `http://127.0.0.1:8000/health` 看后端是不是活着。
- [2026-05-05 18:07 (Asia/Shanghai)] `health` 打不开：去看第 2 个后端窗口有没有报错或已经停了。
- [2026-05-05 18:07 (Asia/Shanghai)] 后端报数据库连接错误：先看第 1 个数据库窗口是不是已经启动。
- [2026-05-05 18:07 (Asia/Shanghai)] 如果出现 Xcode 许可、Homebrew 服务、MariaDB 长期服务相关问题，不要自己乱改，交给 Codex 处理。

## 第三方服务
- 当前项目不把金蝶抓取当成前置必需；真实业务文件可直接作为输入源。
- 当前外部平台接入采用开放适配层，不把系统写死到 CoPaw、Lobster 或其他单一平台。
- 当前 CoPaw / Lobster 只被当成可接入 provider 方向，不是系统本体。
- [2026-07-10 02:13 (Asia/Shanghai)] 当前代码中已查明 `Lobster / 龙虾` 相关入口仍存在，但属于旧兼容/编排触发层，不是文件上传入口：
  - 后端有 `/v1/tasks/lobster-feed`；
  - 后端有 `backend/app/services/lobster_connector.py`，且注释说明新代码应使用 `services.orchestration`；
  - 前端管理员后台现有 `龙虾异步任务（对接层）` 区域只有 `启动 lobster-feed` 和 `刷新任务`；
  - 该区域通过已有 `job_id` 启动编排任务，不提供手稿 Excel 文件选择，也不承接页面 B 手稿数据。
- [2026-07-10 02:13 (Asia/Shanghai)] `phase-12` 的 `手稿提醒导入（仅页面B）` 仍是待施工入口；当前技术事实只是确认它不应复用龙虾旧入口逻辑。
- 当前识别工具路线已确认如下：
  - Excel：直接解析
  - 文字型 PDF：直接抽文本 / 表格
  - 扫描 PDF / 图片：走 OCR 路线
  - 固定样式正式发票：走模板抽取路线
- 当前已确认的识别工具方向包括：
  - `PaddleOCR / PP-Structure` 用于扫描件与复杂表格路线
  - `invoice2data` 用于固定样式发票路线
- 当前这些识别工具只负责抽字段；业务入库、匹配、预警、消警仍由咱们自己的后端控制。
