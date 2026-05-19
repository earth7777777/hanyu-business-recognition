const el = (id) => document.getElementById(id);
const ROLE_STORAGE_KEY = "hanyu_role";
const BACKEND_BASE_FALLBACK = "http://127.0.0.1:8000/v1";
const LEGACY_LOCAL_BACKEND_BASES = new Set([
  "http://127.0.0.1:8000/v1",
  "http://localhost:8000/v1",
]);
const DISPLAY_TIME_ZONE = "Asia/Shanghai";
let latestArchivePreviewToken = "";
const TIME_FIELD_KEYS = new Set([
  "created_at",
  "updated_at",
  "uploaded_at",
  "deleted_at",
  "restored_at",
  "archived_at",
  "last_upload_at",
  "latest_task_updated_at",
]);
const DUPLICATE_RISK_LABELS = {
  none: "无重复",
  same_job: "同批次重复",
  global: "跨批次重复",
  not_checked: "未检测",
};
const JOB_STATUS_LABELS = {
  created: "已创建",
  files_uploaded: "已上传",
  queued: "排队中",
  running: "处理中",
  succeeded: "成功",
  failed: "失败",
  unknown: "未知",
  no_files: "无文件",
};
const DOCUMENT_TYPE_LABELS = {
  order: "合同订单",
  shipment: "发货单",
  payment_notice: "付款通知单",
  invoice: "发票",
};
const EXCEL_MAINLINE_HINT = "当前主输入源：金蝶导出 Excel（订单/发货优先）。";
const UPLOAD_DOC_CAPABILITIES = {
  order: {
    recommendedText: "Excel（兼容 PDF）",
    allowedText: "Excel / PDF",
    accept: ".csv,.xls,.xlsx,.pdf",
    allowedExts: [".csv", ".xls", ".xlsx", ".pdf"],
  },
  shipment: {
    recommendedText: "Excel",
    allowedText: "Excel",
    accept: ".csv,.xls,.xlsx",
    allowedExts: [".csv", ".xls", ".xlsx"],
  },
  payment_notice: {
    recommendedText: "PDF / 图片",
    allowedText: "PDF / PNG / JPG / JPEG / BMP / WEBP / TIF / TIFF",
    accept: ".pdf,.png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff",
    allowedExts: [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"],
  },
  invoice: {
    recommendedText: "PDF / 图片",
    allowedText: "PDF / PNG / JPG / JPEG / BMP / WEBP / TIF / TIFF",
    accept: ".pdf,.png,.jpg,.jpeg,.bmp,.webp,.tif,.tiff",
    allowedExts: [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"],
  },
};
const STATUS_LABELS = {
  ...JOB_STATUS_LABELS,
  parsing: "解析中",
  parsed: "已解析",
  open: "未解除",
  resolved: "已解除",
  success: "成功",
};
const PREVIEW_KIND_LABELS = {
  tabular: "表格",
  text: "文本",
  binary: "二进制",
};
const CHANGE_TYPE_LABELS = {
  new: "新增",
  duplicate: "重复",
  update: "更新",
};
const VERSION_STATUS_LABELS = {
  current: "当前有效",
  inactive_old_version: "失效旧版",
  duplicate_shadow: "重复影子",
  review_pending: "待复核冻结",
  restored_history: "恢复后历史",
  review_released: "复核后保留",
  special_case_retained: "特殊情况保留",
};
const REVIEW_STATUS_LABELS = {
  pending_review: "待复核中",
  reviewed_not_duplicate: "已判非重复",
  none: "",
};
const EFFECTIVE_STATUS_LABELS = {
  current_effective: "当前有效",
  retained_not_effective: "仅保留，不生效",
  inactive_old_version: "失效旧版",
};
const IDENTITY_MODE_LABELS = {
  strict_line_no: "新规则",
  legacy_fallback: "旧主键兜底",
  legacy_bridge: "新旧桥接",
};
const LIFECYCLE_STATE_LABELS = {
  active: "当前数据",
  review_queue: "待复核区",
  archived: "已归档",
  special_case: "特殊情况区",
  recycle_bin: "回收站",
};
const DELETE_ORIGIN_LABELS = {
  manual_file: "人工删文件",
  manual_record: "人工删记录",
  manual_archived: "已归档删除",
  manual_special_case: "特殊情况删除",
};
const BATCH_VIEW_LABELS = {
  all: "全部批次",
  non_empty: "仅非空壳批次",
};
const JOB_LIFECYCLE_VIEW_LABELS = {
  current: "当前数据",
  review_queue: "待复核区",
  archived: "已归档",
  special_case: "特殊情况区",
  recycle_bin: "回收站",
};
const JOB_BUSINESS_VIEW_LABELS = {
  all: "全部业务",
  unshipped: "未发货",
  uninvoiced: "未开票",
};
const BATCH_ANOMALY_LABELS = {
  missing_identity_columns: "缺认人列",
  missing_status_columns: "缺认状态列",
  review_required_blank_values: "认人字段空",
  parse_failed: "解析失败",
  review_queue: "待复核",
};
const OBJECT_TYPE_LABELS = {
  file: "文件",
  record: "记录",
};
const BACKEND_ERROR_DETAIL_LABELS = {
  "Missing X-Role header. Use 'upload' or 'admin'.": "缺少角色信息，请先在页面上方选择“上传员”或“管理员”。",
  "Admin role required for this action.": "这个操作只有管理员能做，请先把上方角色切到“管理员”。",
  "Unsupported batch_view. Use all or non_empty.": "批次视角参数不对，请重新选择“全部批次”或“仅非空壳批次”。",
  "Unsupported lifecycle_view. Use all, current, review_queue, archived, special_case or recycle_bin.":
    "区域视角参数不对，请重新选择当前数据、待复核区、已归档、特殊情况区或回收站。",
  "Unsupported business_view. Use all, unshipped or uninvoiced.": "业务视角参数不对，请重新选择全部业务、未发货或未开票。",
  "job_id is required": "缺少批次 ID，请先选中或输入批次。",
  "kind must be alerts or customer-summary": "导出类型不对，目前只支持预警明细或客户汇总。",
  "body.value must be an object": "提交内容格式不对，请重试。",
  "request_id is required": "缺少请求编号，请重试。",
  "metadata must be an object when provided": "附加信息格式不对，必须是对象。",
  "Upload job not found": "没找到这条批次数据。",
  "Uploaded file not found": "没找到这份文件。",
  "Normalized record not found": "没找到这条记录。",
  "Stored file content not found on disk.": "服务器上没找到这份文件内容。",
  "Record is not in review queue.": "这条记录现在不在待复核区，不能做这个操作。",
  "Only order records can enter special-case zone.": "只有订单记录才能放进特殊情况区。",
  "Only current-data or review-queue order records can enter special-case zone.":
    "只有当前数据区或待复核区里的订单记录，才能放进特殊情况区。",
  "Record is not in special-case zone.": "这条记录现在不在特殊情况区。",
  "Only special-case records originating from review queue can return there.":
    "只有原本从待复核区进去的特殊情况记录，才能放回待复核区。",
  "File is not in special-case zone.": "这份文件现在不在特殊情况区。",
  "Only special-case files originating from review queue can return there.":
    "只有原本从待复核区进去的特殊情况文件，才能放回待复核区。",
  "Record is not in a deletable state.": "这条记录现在不允许放入回收站。",
  "File is not in a deletable state.": "这份文件现在不允许放入回收站。",
  "Record is not in recycle bin.": "这条记录现在不在回收站。",
  "File is not in recycle bin.": "这份文件现在不在回收站。",
  "Only recycle-bin records or completed current-effective order records can be archived.":
    "只有回收站里的记录，或者已经发齐且开齐的当前有效订单记录，才允许归档。",
  "Current-data archive is record-based. Please archive completed order records instead.":
    "当前数据区的归档是按记录走的，请改为归档已完成的订单记录。",
  "Only recycle-bin files can be archived.": "只有回收站里的文件才允许归档。",
  "Only recycle-bin records can be hard-deleted.": "只有回收站里的记录才允许彻底删除。",
  "Only recycle-bin files can be hard-deleted.": "只有回收站里的文件才允许彻底删除。",
  "Hard delete requires a fresh hard-delete preview.": "真删前必须先点一次“预览硬删除”。",
  "Archive manual mode is disabled. Switch to manual mode first.": "当前不是手动挡，请先切到“手动挡”。",
  "Archive execution requires a fresh archive preview.": "执行手动归档前，请先点一次“查看手动归档候选”。",
  "Archive preview expired or candidates changed. Please preview again.": "刚才的试运行结果已经过期或候选发生变化，请重新试运行。",
  "Restore drill requires a dedicated restore-drill database URL. Configure restore_drill_database_url first.":
    "恢复演练需要单独的专用数据库连接，请先配置 restore_drill_database_url。",
  "Restore drill database URL must not reuse the primary DATABASE_URL.":
    "恢复演练不能复用当前正式业务数据库连接，请改用单独的恢复演练连接。",
  "Invalid external client credentials or provider access.": "外部调用身份无效或没有供应方权限。",
  "Missing X-Client-Id or X-Client-Token header.": "缺少外部调用身份信息。",
  "Task not found": "没找到这条任务。",
  "reason is required": "请先填写原因。",
};
const FIELD_LABELS = {
  id: "ID",
  job_id: "批次ID",
  file_id: "文件ID",
  task_id: "任务ID",
  latest_task_id: "最近任务ID",
  source_file_id: "来源文件ID",
  source_job_id: "来源批次ID",
  filename: "原始文件名",
  source_filename: "来源文件名",
  created_at: "创建时间",
  updated_at: "更新时间",
  uploaded_at: "上传时间",
  deleted_at: "删除时间",
  last_upload_at: "最近上传时间",
  latest_task_updated_at: "最近任务更新时间",
  status: "状态",
  task_status: "任务状态",
  latest_task_status: "最近任务状态",
  file_status_summary: "任务状态汇总",
  alert_status: "预警状态",
  parse_status: "解析状态",
  parse_error: "解析错误",
  latest_task_error: "最近任务错误",
  document_type: "单据类型",
  doc_type: "单据类型",
  file_count: "文件数",
  active_file_count: "当前文件数",
  recycle_bin_file_count: "回收站文件数",
  archived_file_count: "归档文件数",
  total_file_count: "文件总数",
  parsed_count: "解析条数",
  parse_failed_count: "解析失败数",
  anomaly_count: "异常数",
  anomaly_codes: "异常原因",
  has_anomaly: "异常批次",
  file_size: "文件大小",
  duplicate_risk: "重复风险",
  source_type: "来源类型",
  normalized_record_count: "归一化记录数",
  total_record_count: "总记录数",
  active_record_count: "当前记录数",
  effective_scan_count: "实际扫描数",
  skip_scan_count: "跳过扫描数",
  recycle_bin_record_count: "回收站记录数",
  archived_record_count: "归档记录数",
  current_effective_record_count: "当前有效记录数",
  review_queue_record_count: "待复核记录数",
  review_released_record_count: "复核放回数",
  auto_deleted_duplicate_count: "自动清理重复数",
  order_unshipped_qty: "未发货数量",
  match_group_count: "匹配组数",
  group_link_count: "关联记录数",
  alert_total: "预警总数",
  open_alert_count: "未解除预警数",
  resolved_alert_count: "已解除预警数",
  preview_kind: "预览类型",
  file_hash_sha256: "文件指纹（SHA-256）",
  is_deleted: "删除状态",
  is_archived: "归档状态",
  lifecycle_state: "生命周期",
  delete_reason: "删除原因",
  delete_origin: "删除来源",
  deleted_by: "删除人",
  restored_by: "恢复人",
  restore_reason: "恢复原因",
  archived_by: "归档人",
  archive_reason: "归档原因",
  special_case_reason: "特殊情况原因",
  special_case_note: "备注",
  is_empty_shell: "空壳批次",
  provider: "服务提供方",
  external_task_id: "外部任务ID",
  customer: "客户",
  source_row: "源行号",
  customer_order_no: "单据编号",
  entry_line_no: "分录行号",
  biz_date: "单据日期",
  item_name: "商品名称",
  item_code: "商品编码",
  quantity: "数量",
  due_date: "预计交货日期",
  latest_outbound_date: "最近出库日期",
  order_outbound_status: "整单出库状态",
  line_outbound_status: "本产品出库状态",
  executed_shipped_qty: "行已执行已出库数量",
  invoiced_qty: "行已开票数量",
  uninvoiced_qty: "行未开票数量",
  change_type: "治理判定",
  review_status: "复核状态",
  effective_status: "生效状态",
  status_tag: "状态标签",
  identity_mode: "身份模式",
  version_status: "版本状态",
  is_current_effective: "当前有效",
  duplicate_of_record_id: "重复来源记录ID",
  superseded_by_record_id: "被哪条新记录顶替",
  supersedes_record_id: "顶替了哪条旧记录",
  governance_reason: "治理说明",
  object_type: "对象类型",
  object_id: "对象ID",
  scan_state: "core.scan_state",
  scan_reason: "扫描说明",
};
const FILTER_KEY_LABELS = {
  job_id: "批次ID关键词",
  task_status: "任务状态",
  alert_status: "预警状态",
  doc_type: "单据类型",
  parse_status: "解析状态",
  date_from: "创建起始日",
  date_to: "创建截止日",
  batch_view: "批次视角",
};
const OUTBOUND_STATUS_LABELS = {
  fully_outbound: "已全部出库",
  partially_outbound: "部分出库",
  not_outbound: "未出库",
};
const JOB_STATUS_ORDER = [
  "failed",
  "running",
  "queued",
  "files_uploaded",
  "created",
  "succeeded",
  "unknown",
  "no_files",
];
const state = {
  audit: {
    selectedJobId: "",
    selectedFileId: "",
    detailFileId: "",
    selectedObjectType: "record",
    selectedObjectId: "",
    selectedObjectLabel: "",
    selectedObjectSpecialCaseSource: "",
    previewObjectUrl: "",
    hardDeletePreviewToken: "",
    hardDeletePreviewObjectType: "",
    hardDeletePreviewObjectId: "",
    includeDeleted: false,
    lifecycleView: "current",
    lifecycleObjectType: "all",
    archiveQueryKeyword: "",
    batchView: "all",
  },
  customerOverview: {
    keyword: "",
    selectedCustomer: "",
  },
  topBatchView: "all",
  topLifecycleView: "current",
  topBusinessView: "all",
};

function duplicateRiskLabel(code) {
  const key = String(code || "not_checked").trim().toLowerCase();
  return DUPLICATE_RISK_LABELS[key] || DUPLICATE_RISK_LABELS.not_checked;
}

function changeTypeLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return CHANGE_TYPE_LABELS[key] || code || "";
}

function versionStatusLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return VERSION_STATUS_LABELS[key] || code || "";
}

function lifecycleStateLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return LIFECYCLE_STATE_LABELS[key] || code || "";
}

function reviewStatusLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return REVIEW_STATUS_LABELS[key] || code || "";
}

function effectiveStatusLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return EFFECTIVE_STATUS_LABELS[key] || code || "";
}

function identityModeLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return IDENTITY_MODE_LABELS[key] || code || "";
}

function normalizeArchiveLookupKeyword(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function matchesArchivedLookup(item, keyword) {
  const normalizedKeyword = normalizeArchiveLookupKeyword(keyword);
  if (!normalizedKeyword) return true;
  const haystack = [
    item.object_id,
    item.file_id,
    item.filename,
    item.job_id,
    item.document_type,
    formatDisplayValue("document_type", item.document_type ?? ""),
    item.customer_order_no,
    item.entry_line_no,
    item.object_type,
    objectTypeLabel(item.object_type),
    item.lifecycle_state,
    lifecycleStateLabel(item.lifecycle_state),
    item.governance_reason,
    item.special_case_reason,
    item.special_case_note,
  ]
    .map((part) => String(part || "").trim().toLowerCase())
    .join(" ");
  return normalizedKeyword
    .split(/\s+/)
    .filter(Boolean)
    .every((part) => haystack.includes(part));
}

function updateArchiveLookupControls() {
  const shell = el("auditArchiveLookupTools");
  const input = el("auditArchiveLookupKeyword");
  const isArchived = (state.audit.lifecycleView || "current") === "archived";
  if (shell) shell.style.display = isArchived ? "grid" : "none";
  if (input) {
    input.disabled = !isArchived;
    input.value = state.audit.archiveQueryKeyword || "";
  }
}

function deleteOriginLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return DELETE_ORIGIN_LABELS[key] || code || "";
}

function recordStatusTag(reviewStatus, effectiveStatus, versionStatus, lifecycleState) {
  const reviewKey = String(reviewStatus || "").trim().toLowerCase();
  const effectiveKey = String(effectiveStatus || "").trim().toLowerCase();
  const versionKey = String(versionStatus || "").trim().toLowerCase();
  const lifecycleKey = String(lifecycleState || "").trim().toLowerCase();
  if (lifecycleKey === "special_case" || versionKey === "special_case_retained") return "特殊情况";
  if (reviewKey === "pending_review") return "待复核中";
  if (reviewKey === "reviewed_not_duplicate" && effectiveKey === "retained_not_effective") return "复核放回（仅保留）";
  if (effectiveKey === "current_effective") return "当前有效";
  if (effectiveKey === "inactive_old_version") return "失效旧版";
  if (effectiveKey === "retained_not_effective") return "仅保留，不生效";
  return "";
}

function objectTypeLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return OBJECT_TYPE_LABELS[key] || code || "";
}

function batchViewLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return BATCH_VIEW_LABELS[key] || code || "";
}

function jobLifecycleViewLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return JOB_LIFECYCLE_VIEW_LABELS[key] || code || "";
}

function jobBusinessViewLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return JOB_BUSINESS_VIEW_LABELS[key] || code || "";
}

function batchAnomalyLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return BATCH_ANOMALY_LABELS[key] || code || "";
}

function batchAnomalySummary(codes) {
  if (!Array.isArray(codes) || !codes.length) return "";
  return codes.map((code) => batchAnomalyLabel(code)).join(" / ");
}

function documentTypeLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return DOCUMENT_TYPE_LABELS[key] || code;
}

function uploadDocCapability(docType) {
  const key = String(docType || "").trim().toLowerCase();
  return UPLOAD_DOC_CAPABILITIES[key] || null;
}

function getFileExtension(filename) {
  const name = String(filename || "").trim().toLowerCase();
  const index = name.lastIndexOf(".");
  if (index < 0) return "";
  return name.slice(index);
}

function syncUploadAcceptByDocType(docType) {
  const input = el("fileInput");
  if (!input) return;
  const capability = uploadDocCapability(docType);
  input.setAttribute("accept", capability ? capability.accept : "");
}

function renderUploadDocTypeHint(docType) {
  const hintEl = el("uploadDocTypeHint");
  if (!hintEl) return;
  const capability = uploadDocCapability(docType);
  if (!capability) {
    hintEl.textContent = `${EXCEL_MAINLINE_HINT}请选择单据类型后查看推荐格式与允许格式。`;
    return;
  }
  hintEl.textContent = `${EXCEL_MAINLINE_HINT}当前单据类型：${documentTypeLabel(docType)}；推荐格式：${capability.recommendedText}；允许格式：${capability.allowedText}。`;
}

function validateUploadFilesByDocType(files, docType) {
  const capability = uploadDocCapability(docType);
  if (!capability) {
    return { ok: true, message: "" };
  }
  const allowed = new Set(capability.allowedExts);
  const invalid = files
    .map((f) => ({ name: f.name, ext: getFileExtension(f.name) }))
    .filter((x) => !allowed.has(x.ext));
  if (!invalid.length) {
    return { ok: true, message: "" };
  }
  const badNames = invalid.map((x) => x.name).join("、");
  return {
    ok: false,
    message: `文件类型不匹配：${badNames}。当前单据类型「${documentTypeLabel(docType)}」允许格式：${capability.allowedText}。`,
  };
}

function bindUploadDocTypeBehavior() {
  const docTypeEl = el("docType");
  if (!docTypeEl) return;
  const apply = () => {
    const docType = (docTypeEl.value || "").trim().toLowerCase();
    syncUploadAcceptByDocType(docType);
    renderUploadDocTypeHint(docType);
  };
  docTypeEl.addEventListener("change", apply);
  apply();
}

function statusLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return STATUS_LABELS[key] || code;
}

function previewKindLabel(code) {
  const key = String(code || "").trim().toLowerCase();
  return PREVIEW_KIND_LABELS[key] || code;
}

function duplicateSourceTypeLabel(code) {
  return String(code || "").toLowerCase() === "same_job" ? "同批次重复" : "跨批次重复";
}

function deletedStatusLabel(flag) {
  return flag ? "已软删除" : "正常";
}

function formatJobStatus(statusSummary, fallbackStatus) {
  const raw = String(statusSummary || fallbackStatus || "no_files").trim();
  if (!raw) return JOB_STATUS_LABELS.no_files;

  const parts = raw.split("|").map((x) => x.trim()).filter(Boolean);
  if (!parts.length) return JOB_STATUS_LABELS.no_files;

  if (parts.length === 1) {
    const parsed = parseStatusPart(parts[0]);
    if (!parsed) return raw;
    const label = JOB_STATUS_LABELS[parsed.key] || parsed.key;
    return parsed.count === null ? label : `${label}(${parsed.count})`;
  }

  const grouped = {};
  const seen = [];
  for (const part of parts) {
    const parsed = parseStatusPart(part);
    if (!parsed) continue;
    if (!(parsed.key in grouped)) {
      grouped[parsed.key] = 0;
      seen.push(parsed.key);
    }
    grouped[parsed.key] += parsed.count === null ? 1 : parsed.count;
  }

  const order = [
    ...JOB_STATUS_ORDER.filter((k) => k in grouped),
    ...seen.filter((k) => !JOB_STATUS_ORDER.includes(k)),
  ];
  if (!order.length) return raw;

  return order
    .map((key) => `${JOB_STATUS_LABELS[key] || key}(${grouped[key]})`)
    .join(" | ");
}

function parseStatusPart(text) {
  const match = String(text || "").trim().match(/^([a-z_]+)(?:\((\d+)\))?$/i);
  if (!match) return null;
  return {
    key: match[1].toLowerCase(),
    count: match[2] ? Number.parseInt(match[2], 10) : null,
  };
}

function normalizeBackendBaseValue(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

function buildAutoBackendBase() {
  if (typeof window === "undefined") return "";
  const protocol = window.location?.protocol || "http:";
  const hostname = window.location?.hostname || "";
  const host = window.location?.host || "";
  const port = String(window.location?.port || "");
  if (!hostname || protocol === "file:") return "";
  // Keep local 5173 -> 8000 development unchanged; cloud entry should use the same origin.
  if (port === "5173") {
    return `${protocol}//${hostname}:8000/v1`;
  }
  return `${protocol}//${host || hostname}/v1`;
}

function initBackendBaseInput() {
  const input = el("backendBase");
  if (!input) return;
  const current = normalizeBackendBaseValue(input.value);
  const autoBase = buildAutoBackendBase();
  if (!autoBase) return;
  if (!current || LEGACY_LOCAL_BACKEND_BASES.has(current)) {
    input.value = autoBase;
  }
}

function getBase() {
  return normalizeBackendBaseValue(el("backendBase")?.value) || buildAutoBackendBase() || BACKEND_BASE_FALLBACK;
}

function getRole() {
  return el("roleSelect").value;
}

function restoreRoleSelection() {
  const roleSelect = el("roleSelect");
  if (!roleSelect) return;
  if (typeof localStorage === "undefined") return;
  try {
    const saved = (localStorage.getItem(ROLE_STORAGE_KEY) || "").trim().toLowerCase();
    if (saved === "admin" || saved === "upload") {
      roleSelect.value = saved;
    }
  } catch (_) {}
}

function persistRoleSelection() {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(ROLE_STORAGE_KEY, getRole());
  } catch (_) {}
}

function renderAuditRoleHint() {
  const node = el("auditRoleHint");
  if (!node) return;
  if (getRole() === "admin") {
    node.textContent = "当前审计角色：管理员（可查看审计列表）";
    return;
  }
  node.textContent = "当前审计角色：上传员（仅管理员可查看审计列表）";
}

function fmtTime(value) {
  if (value === null || value === undefined || value === "") return "";
  const date = parseTimeValue(value);
  if (!date) return String(value);
  return formatInBeijing(date);
}

function parseTimeValue(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }

  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  const raw = String(value || "").trim();
  if (!raw) return null;
  const hasTimePart = /(\d{2}:\d{2}:\d{2})/.test(raw);
  if (!hasTimePart) return null;

  // JS Date 仅稳定支持毫秒，截断超出 3 位的小数秒（Python 常见 6 位微秒）。
  let normalized = raw.replace(/(\.\d{3})\d+(?=(Z|[+-]\d{2}:?\d{2})?$)/, "$1");
  const hasTimezone = /(Z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  if (!hasTimezone) {
    if (normalized.includes(" ")) normalized = normalized.replace(" ", "T");
    normalized = `${normalized}Z`;
  }

  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatInBeijing(date) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: DISPLAY_TIME_ZONE,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const map = {};
  parts.forEach((part) => {
    map[part.type] = part.value;
  });
  return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute}:${map.second}`;
}

function isTimeField(key, value) {
  if (value === null || value === undefined || value === "") return false;
  const k = String(key || "").toLowerCase();
  if (TIME_FIELD_KEYS.has(k)) return true;
  if (/_at$/i.test(k)) return true;
  if (/(?:^|_)(?:time|datetime|timestamp)$/.test(k)) return true;
  return false;
}

function tryParseJson(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function formatValidationErrorDetail(detail) {
  if (!Array.isArray(detail) || !detail.length) return "";
  const first = detail[0];
  if (!first || typeof first !== "object") return "";
  const loc = Array.isArray(first.loc) ? first.loc.filter((part) => part !== "body") : [];
  const field = loc.length ? translateFieldLabel(String(loc[loc.length - 1] || "").trim()) : "提交内容";
  const message = String(first.msg || "").trim();
  if (!message) return `${field}格式不对，请检查后重试。`;
  if (/field required/i.test(message)) return `${field}不能为空。`;
  return `${field}格式不对，请检查后重试。`;
}

function fallbackApiErrorMessage(status) {
  if (status === 400) return "请求内容不对，请检查当前输入。";
  if (status === 401) return "身份信息无效，请先确认角色或登录状态。";
  if (status === 403) return "当前角色没有权限做这个操作。";
  if (status === 404) return "没找到对应的数据或文件。";
  if (status === 409) return "当前状态不允许这样操作。";
  if (status === 422) return "提交内容不完整或格式不对。";
  if (status >= 500) return "后台处理失败，请稍后再试。";
  return `请求失败（状态码 ${status}）。`;
}

function translateBackendErrorDetail(detail) {
  const normalized = String(detail || "").trim();
  if (!normalized) return "";
  if (BACKEND_ERROR_DETAIL_LABELS[normalized]) return BACKEND_ERROR_DETAIL_LABELS[normalized];
  if (/^tabular preview failed:/i.test(normalized)) {
    return "表格预览失败，请检查文件内容或格式。";
  }
  if (/^text preview failed:/i.test(normalized)) {
    return "文本预览失败，请检查文件内容或格式。";
  }
  if (/^Restore drill create database failed:/i.test(normalized)) {
    return "恢复演练临时建库失败，通常是专用恢复账号没有建库权限，或连接信息不对。";
  }
  return "";
}

function extractApiErrorDetail(payload, rawText) {
  if (payload && typeof payload === "object") {
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail.trim();
    const validation = formatValidationErrorDetail(payload.detail);
    if (validation) return validation;
  }
  return String(rawText || "").trim();
}

function localizeApiErrorMessage(status, rawText) {
  const payload = tryParseJson(rawText);
  const detail = extractApiErrorDetail(payload, rawText);
  const translated = translateBackendErrorDetail(detail);
  if (translated) {
    if (/[\u4e00-\u9fff]/.test(detail)) return detail;
    return `${detail}（${translated}）`;
  }
  if (/[\u4e00-\u9fff]/.test(detail)) return detail;
  if (detail) return `${detail}（${fallbackApiErrorMessage(status)}）`;
  return fallbackApiErrorMessage(status);
}

async function api(path, options = {}) {
  const headers = {
    "X-Role": getRole(),
    ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };

  const res = await fetch(`${getBase()}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const text = await res.text();
    const error = new Error(localizeApiErrorMessage(res.status, text));
    error.status = res.status;
    error.rawText = text;
    error.detail = extractApiErrorDetail(tryParseJson(text), text);
    throw error;
  }

  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function toTable(items) {
  if (!items || !items.length) return "<p>暂无数据</p>";
  const cols = Object.keys(items[0]);
  const head = `<tr>${cols.map((c) => `<th>${translateFieldLabel(c)}</th>`).join("")}</tr>`;
  const body = items
    .map((row) => {
      const tds = cols
        .map((c) => {
          const value = row[c];
          if (value === null || value === undefined) return "<td></td>";
          if (typeof value === "object") return `<td>${JSON.stringify(value)}</td>`;
          if (isTimeField(c, value)) return `<td>${fmtTime(value)}</td>`;
          return `<td>${formatDisplayValue(c, value)}</td>`;
        })
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");
  return `<table>${head}${body}</table>`;
}

function renderAlertsTable(items) {
  if (!items || !items.length) return "<p>暂无数据</p>";
  const cols = Object.keys(items[0]);
  const keyClass = (key) => `alert-col-${String(key || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "-")}`;
  const head = `<tr>${cols
    .map((c) => {
      const extraClass = c === "message" ? " alert-message-col" : c === "payload" ? " alert-payload-col" : "";
      const cls = `${keyClass(c)}${extraClass}`;
      const classAttr = ` class="${cls}"`;
      return `<th${classAttr}>${escapeHtml(translateFieldLabel(c))}</th>`;
    })
    .join("")}</tr>`;

  const body = items
    .map((row) => {
      const tds = cols
        .map((c) => {
          const value = row[c];
          const cls = keyClass(c);
          if (c === "payload") {
            if (value === null || value === undefined) return `<td class="alert-payload-cell ${cls}"></td>`;
            const payloadText = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
            return `<td class="alert-payload-cell ${cls}"><details><summary>查看payload</summary><pre>${escapeHtml(payloadText)}</pre></details></td>`;
          }
          if (c === "message") {
            const text = value === null || value === undefined ? "" : String(formatDisplayValue(c, value));
            const safe = escapeHtml(text);
            return `<td class="alert-message-cell ${cls}" title="${safe}">${safe}</td>`;
          }
          if (value === null || value === undefined) return `<td class="${cls}"></td>`;
          if (typeof value === "object") {
            const safe = escapeHtml(JSON.stringify(value));
            return `<td class="${cls}" title="${safe}">${safe}</td>`;
          }
          if (isTimeField(c, value)) {
            const safe = escapeHtml(fmtTime(value));
            return `<td class="${cls}" title="${safe}">${safe}</td>`;
          }
          const safe = escapeHtml(String(formatDisplayValue(c, value)));
          return `<td class="${cls}" title="${safe}">${safe}</td>`;
        })
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");
  return `<table class="alerts-table">${head}${body}</table>`;
}

function renderAuditJobsMessage(message, level = "info") {
  const meta = el("auditJobsMeta");
  const root = el("auditJobsList");
  if (meta) meta.innerHTML = "";
  if (!root) return;
  const color = level === "error" ? "#b91c1c" : "#5f7282";
  root.innerHTML = `<p class="hint" style="color:${color};">${message}</p>`;
}

function renderJobListMessage(message, level = "info") {
  const meta = el("jobListMeta");
  const root = el("jobList");
  if (meta) meta.innerHTML = "";
  if (!root) return;
  const color = level === "error" ? "#b91c1c" : "#5f7282";
  root.innerHTML = `<p class="hint" style="color:${color};">${message}</p>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildAuditFilterSummary(activeFilters) {
  if (!activeFilters.length) return "无（全部）";
  return activeFilters
    .map(({ key, value }) => `${filterKeyLabel(key)}=${filterValueLabel(key, value)}`)
    .join("；");
}

function renderAuditDiagHeader(filterSummary, total, itemsCount) {
  return `<p class="hint" style="margin:0 0 8px;">生效筛选：${escapeHtml(filterSummary)}<br/>本次返回：total=${total}，items=${itemsCount}</p>`;
}

function sumAutoDeletedDuplicateCount(items) {
  if (!Array.isArray(items) || !items.length) return 0;
  return items.reduce((sum, item) => {
    const value = Number.parseInt(`${item?.auto_deleted_duplicate_count ?? 0}`, 10);
    return sum + (Number.isNaN(value) ? 0 : value);
  }, 0);
}

function renderKeyValueTable(rows) {
  if (!Array.isArray(rows) || !rows.length) return "<p>暂无数据</p>";
  const body = rows
    .map(
      (row) => `<tr><th style="width:220px;text-align:left;">${escapeHtml(row.label)}</th><td>${escapeHtml(row.value)}</td></tr>`,
    )
    .join("");
  return `<div class="operations-table-scroll operations-table-scroll-compact"><table class="operations-kv-table">${body}</table></div>`;
}

function renderOperationsAlerts(alerts) {
  if (!Array.isArray(alerts) || !alerts.length) {
    return '<p class="hint">当前无红黄灯告警。</p>';
  }
  const cards = alerts
    .map((item, index) => {
      const level = String(item?.level || "").trim().toLowerCase();
      const isError = level === "error";
      const borderColor = isError ? "#dc2626" : "#d97706";
      const bgColor = isError ? "#fef2f2" : "#fffbeb";
      const badgeText = isError ? "红灯" : "黄灯";
      const title = item?.title || `告警 ${index + 1}`;
      const message = item?.message || "";
      const suggestion = item?.suggestion || "";
      const code = item?.code ? `代码：${item.code}` : "";
      const currentValue =
        item?.current_value === null || item?.current_value === undefined || item?.current_value === ""
          ? ""
          : `当前值：${item.current_value}`;
      const thresholdValue =
        item?.threshold_value === null || item?.threshold_value === undefined || item?.threshold_value === ""
          ? ""
          : `阈值：${item.threshold_value}`;
      const meta = [currentValue, thresholdValue, code].filter(Boolean).join(" / ");
      return `
        <div style="border:1px solid ${borderColor};background:${bgColor};padding:12px;border-radius:10px;margin-bottom:10px;">
          <p style="margin:0 0 6px;font-weight:700;">${escapeHtml(badgeText)} ${index + 1} / ${escapeHtml(title)}</p>
          <p style="margin:0 0 6px;">${escapeHtml(message)}</p>
          ${meta ? `<p class="hint" style="margin:0 0 6px;">${escapeHtml(meta)}</p>` : ""}
          ${suggestion ? `<p class="hint" style="margin:0;">建议：${escapeHtml(suggestion)}</p>` : ""}
        </div>
      `;
    })
    .join("");
  return `<div class="operations-alerts">${cards}</div>`;
}

function renderOperationsMessage(message, level = "info") {
  const root = el("operationsSummary");
  if (!root) return;
  const color = level === "error" ? "#b91c1c" : "#5f7282";
  root.innerHTML = `<p class="hint" style="color:${color};">${escapeHtml(message)}</p>`;
}

function formatOpsTime(value) {
  return value ? fmtTime(value) : "未记录";
}

function formatOpsStatus(status, { overdue = false } = {}) {
  const raw = String(status || "").trim().toLowerCase();
  let label = "未记录";
  if (raw === "success" || raw === "succeeded" || raw === "ok") {
    label = "成功";
  } else if (raw === "failed") {
    label = "失败";
  } else if (raw === "running") {
    label = "运行中";
  } else if (raw === "queued") {
    label = "排队中";
  } else if (raw === "never") {
    label = "未执行";
  } else if (raw && raw !== "unknown") {
    label = raw;
  }
  return overdue ? `${label} / 已超时` : label;
}

function formatOpsSwitch(enabled) {
  return enabled ? "已启用" : "未启用";
}

function formatOpsBytes(value) {
  const bytes = Number.parseInt(`${value ?? 0}`, 10);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatArchiveMode(mode) {
  return String(mode || "").trim().toLowerCase() === "manual" ? "手动挡" : "自动挡";
}

function formatArchiveTrigger(trigger) {
  const raw = String(trigger || "").trim().toLowerCase();
  if (raw === "manual") return "手动正式执行";
  if (raw === "auto") return "自动档执行";
  return "未记录";
}

function buildOpsBoardLevelSummary(alerts) {
  const errorCount = alerts.filter((item) => String(item?.level || "").trim().toLowerCase() === "error").length;
  const warnCount = Math.max(alerts.length - errorCount, 0);
  let boardStatus = "正常";
  if (errorCount > 0) {
    boardStatus = "异常";
  } else if (warnCount > 0) {
    boardStatus = "关注";
  }
  return { boardStatus, errorCount, warnCount };
}

function renderArchivePreviewItems(items) {
  if (!Array.isArray(items) || !items.length) {
    return '<p class="hint">最近手动试运行暂无候选明细。</p>';
  }
  const head =
    "<tr><th>批次ID</th><th>文件ID</th><th>订单号</th><th>品名</th><th>行号</th><th>数量/已发/已开</th></tr>";
  const body = items
    .map((item) => {
      const quantity = item?.quantity ?? "-";
      const shipped = item?.executed_shipped_qty ?? "-";
      const invoiced = item?.invoiced_qty ?? "-";
      return `<tr>
        <td>${escapeHtml(item?.job_id || "")}</td>
        <td>${escapeHtml(item?.file_id || "")}</td>
        <td>${escapeHtml(item?.customer_order_no || "")}</td>
        <td>${escapeHtml(item?.product_name || "")}</td>
        <td>${escapeHtml(String(item?.source_row ?? ""))}</td>
        <td>${escapeHtml(`${quantity} / ${shipped} / ${invoiced}`)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="operations-table-scroll"><table class="operations-detail-table operations-archive-preview-table">${head}${body}</table></div>`;
}

function renderSlowRequestItems(items) {
  if (!Array.isArray(items) || !items.length) {
    return '<p class="hint">最近还没记到慢接口。</p>';
  }
  const head = "<tr><th>时间</th><th>接口</th><th>状态</th><th>耗时(ms)</th><th>参数</th></tr>";
  const body = items
    .map((item) => {
      const method = String(item?.method || "").trim().toUpperCase();
      const path = String(item?.path || "").trim();
      const query = String(item?.query || "").trim();
      return `<tr>
        <td>${escapeHtml(formatOpsTime(item?.observed_at))}</td>
        <td>${escapeHtml(`${method} ${path}`.trim())}</td>
        <td>${escapeHtml(String(item?.status_code ?? ""))}</td>
        <td>${escapeHtml(String(item?.duration_ms ?? ""))}</td>
        <td>${escapeHtml(query || "无")}</td>
      </tr>`;
    })
    .join("");
  return `<div class="operations-table-scroll"><table class="operations-detail-table operations-slow-request-table">${head}${body}</table></div>`;
}

function renderOperationsSummary(payload) {
  if (!payload || typeof payload !== "object") return "<p>暂无数据</p>";
  const health = payload.health || {};
  const backup = payload.backup || {};
  const performance = payload.performance || {};
  const logs = payload.logs || {};
  const restoreDrillPayload = payload.restore_drill || {};
  const archive = payload.archive || {};
  const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
  const dbBackup = backup.db_backup || {};
  const fileBackup = backup.file_backup || {};
  const slowRequests = performance.slow_requests || {};
  const logCleanup = logs.log_cleanup || {};
  const restoreDrill = restoreDrillPayload.restore_drill || {};
  const archivePreview = archive.archive_preview || {};
  const archiveRun = archive.archive_run || {};
  const boardSummary = buildOpsBoardLevelSummary(alerts);

  const overviewRows = [
    { label: "当前总状态", value: `${boardSummary.boardStatus} / 红灯 ${boardSummary.errorCount} / 黄灯 ${boardSummary.warnCount}` },
    { label: "看板生成时间", value: formatOpsTime(payload.generated_at) },
    { label: "最近上传", value: `${formatOpsTime(health.latest_upload_at)}${health.latest_upload_job_id ? ` / ${health.latest_upload_job_id}` : ""}` },
    { label: "最近任务", value: `${formatOpsTime(health.latest_task_at)}${health.latest_task_status ? ` / ${formatOpsStatus(health.latest_task_status)}` : ""}${health.latest_task_job_id ? ` / ${health.latest_task_job_id}` : ""}` },
  ];

  const riskRows = [
    { label: "失败任务批次", value: `${health.failed_task_jobs ?? 0}` },
    { label: "待复核批次", value: `${health.review_queue_jobs ?? 0}` },
    { label: "待复核记录总数", value: `${health.review_queue_record_total ?? 0}` },
    { label: "解析失败批次", value: `${health.parse_failed_jobs ?? 0}` },
    { label: "解析失败文件总数", value: `${health.parse_failed_file_total ?? 0}` },
  ];

  const volumeRows = [
    { label: "批次总数", value: `${health.total_jobs ?? 0}` },
    { label: "非空壳批次", value: `${health.non_empty_jobs ?? 0}` },
    { label: "空壳批次", value: `${health.empty_shell_jobs ?? 0}` },
    { label: "当前有效记录总数", value: `${health.current_effective_record_total ?? 0}` },
    { label: "自动清理重复总数", value: `${health.auto_deleted_duplicate_total ?? 0}` },
  ];

  const governanceRows = [
    { label: "已归档文件总数", value: `${health.archived_file_total ?? 0}` },
    { label: "已归档记录总数", value: `${health.archived_record_total ?? 0}` },
    { label: "回收站文件总数", value: `${health.recycle_bin_file_total ?? 0}` },
    { label: "回收站记录总数", value: `${health.recycle_bin_record_total ?? 0}` },
  ];

  const backupRows = [
    { label: "数据库备份开关", value: formatOpsSwitch(dbBackup.enabled) },
    { label: "数据库备份目标", value: `${dbBackup.target_path || "未配置"}` },
    { label: "自动备份时间", value: `${dbBackup.schedule_time || backup.backup_schedule_time || "02:00"}` },
    { label: "备份保留天数", value: `${dbBackup.retention_days || backup.backup_retention_days || 30}` },
    { label: "数据库最近状态", value: `${formatOpsStatus(dbBackup.last_status, { overdue: !!dbBackup.is_overdue })} / 最近成功：${formatOpsTime(dbBackup.last_success_at)}` },
    { label: "数据库最近开始", value: formatOpsTime(dbBackup.last_started_at) },
    { label: "数据库最近完成", value: formatOpsTime(dbBackup.last_finished_at) },
    { label: "数据库最近产物标记", value: `${dbBackup.last_snapshot_label || "未记录"}` },
    { label: "数据库备份错误", value: `${dbBackup.last_error || "无"}` },
    { label: "上传文件备份开关", value: formatOpsSwitch(fileBackup.enabled) },
    { label: "上传文件备份目标", value: `${fileBackup.target_path || "未配置"}` },
    { label: "上传文件最近状态", value: `${formatOpsStatus(fileBackup.last_status, { overdue: !!fileBackup.is_overdue })} / 最近成功：${formatOpsTime(fileBackup.last_success_at)}` },
    { label: "上传文件最近开始", value: formatOpsTime(fileBackup.last_started_at) },
    { label: "上传文件最近完成", value: formatOpsTime(fileBackup.last_finished_at) },
    { label: "上传文件最近产物标记", value: `${fileBackup.last_snapshot_label || "未记录"}` },
    { label: "上传文件备份错误", value: `${fileBackup.last_error || "无"}` },
    { label: "备份超时阈值（小时）", value: `${backup.backup_overdue_hours ?? "-"}` },
  ];

  const performanceRows = [
    { label: "慢接口阈值", value: `${performance.slow_request_threshold_ms ?? "-"} ms` },
    { label: "最近慢接口时间", value: `${formatOpsTime(slowRequests.last_seen_at)}` },
    { label: "累计命中次数", value: `${slowRequests.total_count ?? 0}` },
    {
      label: "最慢接口",
      value: `${slowRequests.slowest_method ? `${slowRequests.slowest_method} ` : ""}${slowRequests.slowest_path || "未记录"}`,
    },
    { label: "最慢耗时", value: `${slowRequests.slowest_duration_ms ?? 0} ms` },
    { label: "最慢接口状态码", value: `${slowRequests.slowest_status_code ?? 0}` },
    { label: "最慢接口参数", value: `${slowRequests.slowest_query || "无"}` },
  ];

  const logRows = [
    { label: "日志自动清理开关", value: formatOpsSwitch(logCleanup.enabled) },
    { label: "日志目录", value: `${logCleanup.target_path || "未记录"}` },
    { label: "自动清理时间", value: `${logCleanup.schedule_time || logs.log_cleanup_schedule_time || "03:00"}` },
    { label: "日志保留天数", value: `${logCleanup.retention_days || logs.log_retention_days || 30}` },
    { label: "当前日志文件数", value: `${logCleanup.current_file_count ?? 0}` },
    { label: "当前日志总大小", value: formatOpsBytes(logCleanup.current_total_size_bytes) },
    { label: "最近日志文件时间", value: formatOpsTime(logCleanup.latest_file_at) },
    {
      label: "最近清理状态",
      value: `${formatOpsStatus(logCleanup.last_status)} / 最近成功：${formatOpsTime(logCleanup.last_success_at)}`,
    },
    { label: "最近清理开始", value: formatOpsTime(logCleanup.last_started_at) },
    { label: "最近清理完成", value: formatOpsTime(logCleanup.last_finished_at) },
    {
      label: "最近清理结果",
      value: `删除 ${logCleanup.last_removed_file_count ?? 0} 个 / 剩余 ${logCleanup.last_remaining_file_count ?? 0} 个`,
    },
    {
      label: "最近清理体量",
      value: `删掉 ${formatOpsBytes(logCleanup.last_removed_total_size_bytes)} / 剩余 ${formatOpsBytes(
        logCleanup.last_remaining_total_size_bytes
      )}`,
    },
    { label: "最近清理来源", value: `${logCleanup.last_trigger === "auto" ? "自动执行" : logCleanup.last_trigger === "manual" ? "手动执行" : "未记录"}` },
    { label: "日志清理错误", value: `${logCleanup.last_error || "无"}` },
  ];

  const restoreRows = [
    {
      label: "专用恢复连接",
      value:
        restoreDrill.connection_ready
          ? `已配置 / ${restoreDrill.connection_source === "env" ? "环境变量" : restoreDrill.connection_source === "policy" ? "看板配置" : "本地 sqlite"}`
          : "未配置",
    },
    { label: "当前数据库备份", value: `${restoreDrill.available_db_snapshot_label || "未找到"} / ${formatOpsTime(restoreDrill.available_db_snapshot_time)}` },
    { label: "当前上传文件备份", value: `${restoreDrill.available_file_snapshot_label || "未找到"} / ${formatOpsTime(restoreDrill.available_file_snapshot_time)}` },
    {
      label: "最近演练状态",
      value: `${formatOpsStatus(restoreDrill.last_status)} / 最近成功：${formatOpsTime(restoreDrill.last_success_at)}`,
    },
    { label: "最近演练开始", value: formatOpsTime(restoreDrill.last_started_at) },
    { label: "最近演练完成", value: formatOpsTime(restoreDrill.last_finished_at) },
    { label: "最近数据库备份", value: `${restoreDrill.last_db_snapshot_label || "未记录"}` },
    { label: "最近上传文件备份", value: `${restoreDrill.last_file_snapshot_label || "未记录"}` },
    {
      label: "最近恢复结果",
      value: `批次 ${restoreDrill.last_restored_job_count ?? 0} / 上传记录 ${restoreDrill.last_restored_uploaded_file_row_count ?? 0} / 归一化记录 ${restoreDrill.last_restored_record_count ?? 0}`,
    },
    { label: "最近恢复原始文件数", value: `${restoreDrill.last_restored_storage_file_count ?? 0}` },
    {
      label: "最近恢复任务记录数",
      value: `${restoreDrill.last_restored_task_run_count ?? 0}`,
    },
    {
      label: "最近演练来源",
      value:
        restoreDrill.last_trigger === "manual"
          ? "手动执行"
          : restoreDrill.last_trigger === "auto"
            ? "自动执行"
            : "未记录",
    },
    { label: "恢复演练错误", value: `${restoreDrill.last_error || "无"}` },
  ];

  const archiveRows = [
    { label: "归档模式", value: `${formatArchiveMode(archive.mode)}` },
    { label: "当前候选文件数", value: `${archive.candidate_file_count ?? 0}` },
    { label: "当前候选记录数", value: `${archive.candidate_record_count ?? 0}` },
    { label: "自动归档条件", value: `${archive.auto_archive_rule || "当前有效订单且发齐=数量、开齐=数量"}` },
    { label: "归档后查询", value: "点上方“进入归档查询”，到已归档视角继续按批次 / 文件 / 记录查找" },
    { label: "最近手动试运行", value: `${formatOpsStatus(archivePreview.last_status)} / ${formatOpsTime(archivePreview.last_run_at)}` },
    { label: "最近试运行候选", value: `文件 ${archivePreview.last_candidate_file_count ?? 0} / 记录 ${archivePreview.last_candidate_record_count ?? 0}` },
    {
      label: "最近正式归档执行",
      value: `${formatOpsStatus(archiveRun.last_status, { overdue: !!archiveRun.is_overdue })} / ${formatOpsTime(archiveRun.last_run_at)} / ${formatArchiveTrigger(archiveRun.trigger)}`,
    },
    { label: "最近正式归档结果", value: `文件 ${archiveRun.last_archived_file_count ?? 0} / 记录 ${archiveRun.last_archived_record_count ?? 0}` },
    { label: "归档执行错误", value: `${archiveRun.last_error || "无"}` },
  ];

  return `
    <div class="operations-board">
      <div class="operations-board-top">
        <section class="operations-panel">
          <h3>看板总览</h3>
          ${renderKeyValueTable(overviewRows)}
        </section>
        <section class="operations-panel">
          <h3>风险计数</h3>
          ${renderKeyValueTable(riskRows)}
        </section>
        <section class="operations-panel">
          <h3>数据体量</h3>
          ${renderKeyValueTable(volumeRows)}
        </section>
        <section class="operations-panel">
          <h3>治理存量</h3>
          ${renderKeyValueTable(governanceRows)}
        </section>
        <section class="operations-panel">
          <h3>备份状态</h3>
          ${renderKeyValueTable(backupRows)}
        </section>
        <section class="operations-panel">
          <h3>归档状态</h3>
          ${renderKeyValueTable(archiveRows)}
        </section>
        <section class="operations-panel">
          <h3>慢接口状态</h3>
          ${renderKeyValueTable(performanceRows)}
        </section>
        <section class="operations-panel">
          <h3>日志留存状态</h3>
          ${renderKeyValueTable(logRows)}
        </section>
        <section class="operations-panel">
          <h3>恢复演练状态</h3>
          ${renderKeyValueTable(restoreRows)}
        </section>
      </div>
      <div class="operations-board-detail">
        <section class="operations-panel operations-panel-detail">
          <h3>告警状态</h3>
          ${renderOperationsAlerts(alerts)}
        </section>
        <section class="operations-panel operations-panel-detail">
          <h3>最近慢接口明细</h3>
          ${renderSlowRequestItems(slowRequests.recent_items || [])}
        </section>
        <section class="operations-panel operations-panel-detail operations-panel-full">
          <h3>最近手动试运行候选（最多 20 条）</h3>
          ${renderArchivePreviewItems(archivePreview.last_preview_items || [])}
        </section>
      </div>
    </div>
  `;
}

function safeInt(value) {
  const parsed = Number.parseInt(`${value ?? 0}`, 10);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function jobMatchesLifecycleView(summary, lifecycleView) {
  const view = String(lifecycleView || "current").trim().toLowerCase();
  if (view === "all") return true;
  if (view === "current") {
    if (Boolean(summary?.is_empty_shell)) return true;
    const activeFileCount = safeInt(summary?.active_file_count);
    const activeRecordCount = safeInt(summary?.active_record_count);
    const currentEffectiveRecordCount = safeInt(summary?.current_effective_record_count);
    const reviewQueueRecordCount = safeInt(summary?.review_queue_record_count);
    const parseFailedCount = safeInt(summary?.parse_failed_count);
    if (currentEffectiveRecordCount > 0) return true;
    if (activeRecordCount > reviewQueueRecordCount) return true;
    if (activeRecordCount === 0 && activeFileCount > 0) return true;
    if (parseFailedCount > 0) return true;
    return false;
  }
  if (view === "review_queue") return safeInt(summary?.review_queue_record_count) > 0;
  if (view === "special_case") {
    return safeInt(summary?.special_case_file_count) > 0 || safeInt(summary?.special_case_record_count) > 0;
  }
  if (view === "archived") return safeInt(summary?.archived_file_count) > 0 || safeInt(summary?.archived_record_count) > 0;
  if (view === "recycle_bin") {
    return safeInt(summary?.recycle_bin_file_count) > 0 || safeInt(summary?.recycle_bin_record_count) > 0;
  }
  return false;
}

function inferPreferredAuditLifecycle(summary) {
  if (jobMatchesLifecycleView(summary, "current")) return "current";
  if (jobMatchesLifecycleView(summary, "review_queue")) return "review_queue";
  if (jobMatchesLifecycleView(summary, "special_case")) return "special_case";
  if (jobMatchesLifecycleView(summary, "archived")) return "archived";
  if (jobMatchesLifecycleView(summary, "recycle_bin")) return "recycle_bin";
  return "current";
}

function renderJobSummaryTable(items, options = {}) {
  if (!items || !items.length) return "<p>暂无数据</p>";
  const rows = items
    .map((r) => {
      const preferredLifecycle = inferPreferredAuditLifecycle(r);
      return `
      <tr>
        <td><button data-job-open="${r.job_id}" data-job-auto-lifecycle="${preferredLifecycle}">${r.job_id}</button></td>
        <td>${fmtTime(r.created_at)}</td>
        <td>${r.uploaded_product_row_count ?? r.total_record_count ?? 0}</td>
        <td>${r.active_file_count ?? r.file_count ?? 0}</td>
        <td>${r.recycle_bin_file_count ?? 0}</td>
        <td>${r.archived_file_count ?? 0}</td>
        <td>${r.active_record_count ?? 0}</td>
        <td>${r.current_effective_record_count ?? 0}</td>
        <td>${r.review_queue_record_count ?? 0}</td>
        <td>${r.review_released_record_count ?? 0}</td>
        <td>${r.recycle_bin_record_count ?? 0}</td>
        <td>${r.archived_record_count ?? 0}</td>
        <td>${r.auto_deleted_duplicate_count ?? 0}</td>
        <td>${r.is_empty_shell ? "是" : "否"}</td>
        <td>${r.has_anomaly ? "是" : "否"}</td>
        <td>${escapeHtml(batchAnomalySummary(r.anomaly_codes))}</td>
        <td>${r.latest_filename ?? ""}</td>
        <td>${fmtTime(r.last_upload_at)}</td>
        <td>${formatJobStatus(r.file_status_summary, r.latest_task_status)}</td>
        <td>${r.open_alert_count ?? 0}</td>
        <td>${r.resolved_alert_count ?? 0}</td>
        <td>${r.parse_failed_count ?? 0}</td>
      </tr>`;
    })
    .join("");
  return `
    <table>
      <tr>
        <th>批次ID</th>
        <th>创建时间</th>
        <th>上传产品行数</th>
        <th>当前文件</th>
        <th>回收站文件</th>
        <th>归档文件</th>
        <th>当前记录</th>
        <th>当前有效记录</th>
        <th>待复核记录数</th>
        <th>复核放回数</th>
        <th>回收站记录</th>
        <th>归档记录</th>
        <th>自动清理重复数</th>
        <th>空壳批次</th>
        <th>异常批次</th>
        <th>异常原因</th>
        <th>最新文件名</th>
        <th>最近上传</th>
        <th>任务状态</th>
        <th>未解除预警</th>
        <th>已解除预警</th>
        <th>解析失败数</th>
      </tr>
      ${rows}
    </table>
  `;
}

async function fetchAllAdminJobSummaries(params) {
  const query = new URLSearchParams(params || {});
  const size = 100;
  let page = 1;
  let total = 0;
  const items = [];
  while (true) {
    query.set("page", String(page));
    query.set("size", String(size));
    const res = await api(`/admin/jobs?${query.toString()}`);
    const chunk = (res && res.items) || [];
    const parsedTotal = Number.parseInt(`${res && res.total}`, 10);
    total = Number.isNaN(parsedTotal) ? chunk.length : parsedTotal;
    items.push(...chunk);
    if (!chunk.length || items.length >= total) break;
    page += 1;
  }
  return { total, items };
}

function renderAuditRiskMessage(message, level = "info") {
  const root = el("auditRiskNote");
  if (!root) return;
  const color = level === "error" ? "#b91c1c" : "#5f7282";
  root.innerHTML = `<span style="color:${color};">${escapeHtml(message)}</span>`;
}

function resetAuditHardDeletePreviewGuard() {
  state.audit.hardDeletePreviewToken = "";
  state.audit.hardDeletePreviewObjectType = "";
  state.audit.hardDeletePreviewObjectId = "";
}

function hasFreshAuditHardDeletePreview() {
  const { objectType, objectId } = getAuditActionTarget();
  return (
    !!state.audit.hardDeletePreviewToken &&
    state.audit.hardDeletePreviewObjectType === objectType &&
    state.audit.hardDeletePreviewObjectId === objectId
  );
}

function clearAuditFilePanels(message = "") {
  clearAuditPreviewObjectUrl();
  state.audit.selectedFileId = "";
  state.audit.detailFileId = "";
  state.audit.selectedObjectType = "record";
  state.audit.selectedObjectId = "";
  state.audit.selectedObjectLabel = "";
  state.audit.selectedObjectSpecialCaseSource = "";
  resetAuditHardDeletePreviewGuard();
  if (el("auditFileDetail")) el("auditFileDetail").innerHTML = "<p class=\"hint\">未选择文件</p>";
  if (el("auditPreview")) el("auditPreview").innerHTML = "<p class=\"hint\">请选择文件查看预览或归一化记录。</p>";
  if (el("auditDuplicatesView")) el("auditDuplicatesView").innerHTML = "";
  if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
  if (el("auditTargetObjectType")) el("auditTargetObjectType").value = "record";
  if (el("auditTargetObjectId")) el("auditTargetObjectId").value = "";
  if (el("auditLifecycleList")) el("auditLifecycleList").innerHTML = "";
  updateAuditActionButtons();
  if (message) renderAuditRiskMessage(message);
}

function clearAuditActionTarget(message = "") {
  state.audit.selectedObjectType = "record";
  state.audit.selectedObjectId = "";
  state.audit.selectedObjectLabel = "";
  state.audit.selectedObjectSpecialCaseSource = "";
  resetAuditHardDeletePreviewGuard();
  if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
  if (el("auditTargetObjectType")) el("auditTargetObjectType").value = "record";
  if (el("auditTargetObjectId")) el("auditTargetObjectId").value = "";
  updateAuditActionButtons();
  if (message) renderAuditRiskMessage(message);
}

function setAuditActionTarget(objectType, objectId, label = "", extra = {}) {
  const nextType = objectType || "record";
  const nextId = (objectId || "").trim();
  if (state.audit.selectedObjectType !== nextType || state.audit.selectedObjectId !== nextId) {
    resetAuditHardDeletePreviewGuard();
    if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
  }
  state.audit.selectedObjectType = nextType;
  state.audit.selectedObjectId = nextId;
  state.audit.selectedObjectLabel = label || state.audit.selectedObjectId;
  state.audit.selectedObjectSpecialCaseSource = (extra.specialCaseSource || "").trim();
  if (el("auditTargetObjectType")) el("auditTargetObjectType").value = state.audit.selectedObjectType;
  if (el("auditTargetObjectId")) el("auditTargetObjectId").value = state.audit.selectedObjectId;
  updateAuditActionButtons();
}

function getAuditActionTarget() {
  const objectType = (el("auditTargetObjectType")?.value || state.audit.selectedObjectType || "record").trim();
  const objectId = (el("auditTargetObjectId")?.value || state.audit.selectedObjectId || "").trim();
  return { objectType, objectId };
}

function setAuditButtonVisible(id, visible) {
  const node = el(id);
  if (!node) return;
  node.style.display = visible ? "" : "none";
}

function setAuditButtonEnabled(id, enabled) {
  const node = el(id);
  if (!node) return;
  node.disabled = !enabled;
}

function setAuditFieldVisible(id, visible) {
  const node = el(id);
  if (!node || !node.closest) return;
  const label = node.closest("label");
  if (!label) return;
  label.style.display = visible ? "" : "none";
}

function updateAuditActionButtons() {
  const lifecycleView = state.audit.lifecycleView || "current";
  const { objectType, objectId } = getAuditActionTarget();
  const isRecordTarget = objectType === "record" && !!objectId;
  const specialCaseSource = (state.audit.selectedObjectSpecialCaseSource || "").trim();

  setAuditButtonVisible("btnAuditReviewCompare", lifecycleView === "review_queue" && isRecordTarget);
  setAuditButtonVisible(
    "btnAuditDeleteImpact",
    isRecordTarget && (
      lifecycleView === "current" ||
      lifecycleView === "review_queue" ||
      lifecycleView === "archived" ||
      lifecycleView === "special_case"
    )
  );
  setAuditButtonVisible(
    "btnAuditSoftDelete",
    isRecordTarget && (
      lifecycleView === "current" ||
      lifecycleView === "review_queue" ||
      lifecycleView === "archived" ||
      lifecycleView === "special_case"
    )
  );
  setAuditButtonVisible(
    "btnAuditMoveToSpecialCase",
    isRecordTarget && (lifecycleView === "current" || lifecycleView === "review_queue")
  );
  setAuditButtonVisible("btnAuditReturnToReviewQueue", isRecordTarget && lifecycleView === "special_case" && specialCaseSource === "review_queue");
  setAuditButtonVisible("btnAuditReturnToJobList", isRecordTarget && lifecycleView === "special_case" && specialCaseSource !== "review_queue");
  setAuditButtonVisible("btnAuditRestore", isRecordTarget && lifecycleView === "recycle_bin");
  setAuditButtonVisible("btnAuditArchive", lifecycleView === "current" && isRecordTarget);
  setAuditButtonVisible("btnAuditHardDeletePreview", isRecordTarget && lifecycleView === "recycle_bin");
  setAuditButtonVisible("btnAuditHardDelete", isRecordTarget && lifecycleView === "recycle_bin");
  setAuditButtonEnabled("btnAuditHardDelete", lifecycleView === "recycle_bin" && isRecordTarget && hasFreshAuditHardDeletePreview());
  setAuditFieldVisible(
    "auditSpecialCaseReason",
    isRecordTarget && (lifecycleView === "current" || lifecycleView === "review_queue")
  );
  setAuditFieldVisible(
    "auditSpecialCaseNote",
    isRecordTarget && (lifecycleView === "current" || lifecycleView === "review_queue")
  );
}

function syncAuditLifecycleViewControls() {
  const value = state.audit.lifecycleView || "current";
  if (el("auditLifecycleView")) el("auditLifecycleView").value = value;
  if (el("auditLifecycleViewQuick")) el("auditLifecycleViewQuick").value = value;
}

async function applyAuditLifecycleViewChange(nextView) {
  state.audit.lifecycleView = nextView || "current";
  resetAuditHardDeletePreviewGuard();
  if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
  syncAuditLifecycleViewControls();
  updateArchiveLookupControls();
  updateAuditActionButtons();
  if (!state.audit.selectedJobId) {
    await refreshAuditLifecycleObjects();
    return;
  }
  await Promise.all([
    refreshAuditFiles(state.audit.selectedJobId),
    refreshAuditOverview(state.audit.selectedJobId),
  ]);
  await refreshAuditLifecycleObjects();
}

async function refreshAuditLifecycleObjects() {
  const root = el("auditLifecycleList");
  if (!root) return;
  if (state.audit.lifecycleView === "current") {
    const selectedJobId = (state.audit.selectedJobId || "").trim();
    if (!selectedJobId) {
      root.innerHTML = '<p class="hint">请先选中一个批次，再查看当前数据对象。</p>';
      updateAuditActionButtons();
      return;
    }
    const includeDeleted = state.audit.includeDeleted ? "true" : "false";
    const filesRes = await api(
      `/admin/jobs/${encodeURIComponent(selectedJobId)}/files?include_deleted=${includeDeleted}&lifecycle_view=current`
    );
    const files = (filesRes && filesRes.items) || [];
    const selectedFileId = (state.audit.selectedFileId || "").trim();
    const expandedFileId =
      (selectedFileId && files.some((item) => (item.file_id || "").trim() === selectedFileId) && selectedFileId) ||
      (files.length === 1 ? (files[0].file_id || "").trim() : "");
    let recordItems = [];
    if (expandedFileId) {
      const recordsRes = await api(`/admin/files/${encodeURIComponent(expandedFileId)}/records`);
      recordItems = ((recordsRes && recordsRes.items) || [])
        .filter((item) => String(item.lifecycle_state || "").trim().toLowerCase() === "active")
        .sort((a, b) => {
          const currentDelta = Number(Boolean(b.is_current_effective)) - Number(Boolean(a.is_current_effective));
          if (currentDelta !== 0) return currentDelta;
          return Number(a.source_row || 0) - Number(b.source_row || 0);
        })
        .map((item) => ({
          object_type: "record",
          object_id: item.record_id,
          filename: files.find((file) => (file.file_id || "").trim() === expandedFileId)?.filename || "",
          job_id: selectedJobId,
          document_type: files.find((file) => (file.file_id || "").trim() === expandedFileId)?.document_type || "",
          customer_order_no: item.customer_order_no ?? "",
          entry_line_no: item.entry_line_no ?? "",
          review_status: item.review_status ?? "",
          effective_status: item.effective_status ?? "",
          lifecycle_state: item.lifecycle_state ?? "",
          version_status: item.version_status ?? "",
          identity_mode: item.identity_mode ?? "",
          governance_reason: item.governance_reason ?? "",
          special_case_note: "",
          delete_origin: item.delete_origin ?? "",
          created_at: item.created_at ?? "",
          file_id: expandedFileId,
          special_case_source: item.special_case_source ?? "",
        }));
    }
    const items = [
      ...files.map((item) => ({
        object_type: "file",
        object_id: item.file_id,
        filename: item.filename ?? "",
        job_id: item.job_id ?? selectedJobId,
        document_type: item.document_type ?? "",
        customer_order_no: "",
        entry_line_no: "",
        review_status: "",
        effective_status: "",
        lifecycle_state: item.lifecycle_state ?? "active",
        version_status: "",
        identity_mode: "",
        governance_reason: "",
        special_case_note: "",
        delete_origin: "",
        created_at: item.uploaded_at ?? "",
        file_id: item.file_id,
        special_case_source: "",
      })),
      ...recordItems,
    ];
    if (!items.length) {
      root.innerHTML = '<p class="hint">当前批次在当前数据区暂无可操作对象。</p>';
      updateAuditActionButtons();
      return;
    }
    const rows = items
      .map(
        (item) => `
      <tr>
        <td>${objectTypeLabel(item.object_type)}</td>
        <td>${
          item.object_type === "record"
            ? `<button data-audit-target="${item.object_id}" data-audit-target-type="record" data-special-case-source="${item.special_case_source ?? ""}">${item.object_id}</button>`
            : `<button data-file-open="${item.object_id}">${item.object_id}</button>`
        }</td>
        <td>${item.filename ?? ""}</td>
        <td>${item.job_id ?? ""}</td>
        <td>${formatDisplayValue("document_type", item.document_type ?? "")}</td>
        <td>${item.customer_order_no ?? ""}</td>
        <td>${item.entry_line_no ?? ""}</td>
        <td>${reviewStatusLabel(item.review_status ?? "")}</td>
        <td>${effectiveStatusLabel(item.effective_status ?? "")}</td>
        <td>${formatDisplayValue("lifecycle_state", item.lifecycle_state ?? "")}</td>
        <td>${formatDisplayValue("version_status", item.version_status ?? "")}</td>
        <td>${formatDisplayValue("identity_mode", item.identity_mode ?? "")}</td>
        <td>${item.governance_reason ?? ""}</td>
        <td>${item.special_case_note ?? ""}</td>
        <td>${formatDisplayValue("delete_origin", item.delete_origin ?? "")}</td>
        <td>${fmtTime(item.created_at)}</td>
        <td>${
          item.object_type === "record" && item.file_id
            ? `
              <button data-review-records="${item.file_id}" data-review-record-id="${item.object_id}" data-special-case-source="${item.special_case_source ?? ""}">查看归一化记录</button>
              <button data-file-preview="${item.file_id}">预览原文件</button>
            `
            : `
              <button data-review-records="${item.object_id}">查看归一化记录</button>
              <button data-file-preview="${item.object_id}">预览原文件</button>
            `
        }</td>
      </tr>`
      )
      .join("");
    root.innerHTML = `
      <p class="hint">当前数据对象列表（文件 ${files.length} 条${expandedFileId ? `，当前展开记录 ${recordItems.length} 条` : "；点“查看归一化记录”后可展开记录目标"}）</p>
      <table>
        <tr>
          <th>类型</th>
          <th>对象ID</th>
          <th>文件名</th>
          <th>批次ID</th>
          <th>单据类型</th>
          <th>单据编号</th>
          <th>分录行号</th>
          <th>复核状态</th>
          <th>生效状态</th>
          <th>生命周期</th>
          <th>版本状态</th>
          <th>身份模式</th>
          <th>治理说明</th>
          <th>备注</th>
          <th>来源</th>
          <th>最近时间</th>
          <th>操作</th>
        </tr>
        ${rows}
      </table>
    `;
    updateAuditActionButtons();
    return;
  }
  const endpoint =
    state.audit.lifecycleView === "archived"
      ? "/admin/archived"
      : state.audit.lifecycleView === "special_case"
        ? "/admin/special-case"
      : state.audit.lifecycleView === "review_queue"
        ? "/admin/review-queue"
        : "/admin/recycle-bin";
  const res = await api(`${endpoint}?object_type=${encodeURIComponent(state.audit.lifecycleObjectType)}`);
  let items = (res && res.items) || [];
  const totalBeforeLocalFilter = items.length;
  const selectedJobId = (state.audit.selectedJobId || "").trim();
  if (selectedJobId) {
    items = items.filter((item) => !item.job_id || item.job_id === selectedJobId);
  }
  const archiveKeyword = state.audit.lifecycleView === "archived" ? normalizeArchiveLookupKeyword(state.audit.archiveQueryKeyword) : "";
  if (archiveKeyword) {
    items = items.filter((item) => matchesArchivedLookup(item, archiveKeyword));
  }
  if (!items.length) {
    const emptyLabel =
      state.audit.lifecycleView === "archived"
        ? "归档对象"
        : state.audit.lifecycleView === "special_case"
          ? "特殊情况对象"
        : state.audit.lifecycleView === "review_queue"
          ? "待复核对象"
          : "回收站对象";
    root.innerHTML =
      state.audit.lifecycleView === "archived" && archiveKeyword
        ? `<p class="hint">当前已归档区没有命中“${escapeHtml(state.audit.archiveQueryKeyword)}”的对象。可改搜批次ID、文件名、文件ID、记录ID、单据编号或行号。</p>`
        : `<p class="hint">当前视角下暂无${emptyLabel}。</p>`;
    updateAuditActionButtons();
    return;
  }
  const showRecordDetailsAction =
    state.audit.lifecycleView === "review_queue" ||
    state.audit.lifecycleView === "archived" ||
    state.audit.lifecycleView === "special_case";
  const rows = items
    .map(
      (item) => `
      <tr>
        <td>${objectTypeLabel(item.object_type)}</td>
        <td>${
          item.object_type === "record"
            ? `<button data-audit-target="${item.object_id}" data-audit-target-type="record" data-special-case-source="${item.special_case_source ?? ""}">${item.object_id}</button>`
            : `<button data-file-open="${item.object_id}">${item.object_id}</button>`
        }</td>
        <td>${item.filename ?? ""}</td>
        <td>${item.job_id ?? ""}</td>
        <td>${formatDisplayValue("document_type", item.document_type ?? "")}</td>
        <td>${item.customer_order_no ?? ""}</td>
        <td>${item.entry_line_no ?? ""}</td>
        <td>${reviewStatusLabel(item.review_status ?? "")}</td>
        <td>${effectiveStatusLabel(item.effective_status ?? "")}</td>
        <td>${formatDisplayValue("lifecycle_state", item.lifecycle_state ?? "")}</td>
        <td>${formatDisplayValue("version_status", item.version_status ?? "")}</td>
        <td>${formatDisplayValue("identity_mode", item.identity_mode ?? "")}</td>
        <td>${item.special_case_reason ?? item.governance_reason ?? ""}</td>
        <td>${item.special_case_note ?? ""}</td>
        <td>${formatDisplayValue("delete_origin", item.delete_origin ?? "")}</td>
        <td>${fmtTime(item.deleted_at || item.archived_at || item.created_at)}</td>
        ${
          showRecordDetailsAction
            ? `<td>${
                item.object_type === "record" && item.file_id
                  ? `
                    <button data-review-records="${item.file_id}" data-review-record-id="${item.object_id}" data-special-case-source="${item.special_case_source ?? ""}">查看归一化记录</button>
                    <button data-file-preview="${item.file_id}">预览原文件</button>
                  `
                  : item.object_type === "file"
                    ? `
                      <button data-review-records="${item.object_id}" data-special-case-source="${item.special_case_source ?? ""}">查看归一化记录</button>
                      <button data-file-preview="${item.object_id}">预览原文件</button>
                    `
                    : ""
              }</td>`
            : ""
        }
      </tr>`
    )
    .join("");
  const archivedLookupHint =
    state.audit.lifecycleView === "archived"
      ? `<p class="hint">归档查询支持：批次ID、文件名、文件ID、记录ID、单据编号、分录行号。${archiveKeyword ? ` 当前命中 ${items.length} / 原始 ${totalBeforeLocalFilter} 条。` : ""}</p>`
      : "";
  root.innerHTML = `
    ${archivedLookupHint}
    <p class="hint">当前视角对象列表（${items.length} 条）</p>
    <table>
      <tr>
        <th>类型</th>
        <th>对象ID</th>
        <th>文件名</th>
        <th>批次ID</th>
        <th>单据类型</th>
        <th>单据编号</th>
        <th>分录行号</th>
        <th>复核状态</th>
        <th>生效状态</th>
        <th>生命周期</th>
        <th>版本状态</th>
        <th>身份模式</th>
        <th>治理说明</th>
        <th>备注</th>
        <th>来源</th>
        <th>最近时间</th>
        ${showRecordDetailsAction ? "<th>操作</th>" : ""}
      </tr>
      ${rows}
    </table>
  `;
  updateAuditActionButtons();
}

function renderAuditRecordTable(items) {
  if (!items.length) return "<p>暂无归一化记录</p>";
  const rows = items
    .map(
      (it) => `
      <tr>
        <td><button data-audit-target="${it.record_id}" data-audit-target-type="record" data-special-case-source="${it.special_case_source ?? ""}">记录目标</button></td>
        <td>${recordStatusTag(it.review_status, it.effective_status, it.version_status, it.lifecycle_state)}</td>
        <td>${it.source_row ?? 0}</td>
        <td>${it.record_id ?? ""}</td>
        <td>${it.customer_order_no ?? ""}</td>
        <td>${it.entry_line_no ?? ""}</td>
        <td>${it.biz_date ?? ""}</td>
        <td>${reviewStatusLabel(it.review_status ?? "")}</td>
        <td>${effectiveStatusLabel(it.effective_status ?? "")}</td>
        <td>${changeTypeLabel(it.change_type ?? "")}</td>
        <td>${versionStatusLabel(it.version_status ?? "")}</td>
        <td>${it.is_current_effective ? "是" : "否"}</td>
        <td>${lifecycleStateLabel(it.lifecycle_state ?? "")}</td>
        <td>${identityModeLabel(it.identity_mode ?? "")}</td>
        <td>${it.item_code ?? ""}</td>
        <td>${outboundStatusLabel(it.order_outbound_status)}</td>
        <td>${outboundStatusLabel(it.line_outbound_status)}</td>
        <td>${it.executed_shipped_qty ?? ""}</td>
        <td>${it.order_unshipped_qty ?? ""}</td>
        <td>${it.invoiced_qty ?? ""}</td>
        <td>${it.uninvoiced_qty ?? ""}</td>
        <td>${it.latest_outbound_date ?? ""}</td>
        <td>${it.governance_reason ?? ""}</td>
      </tr>`
    )
    .join("");
  return `
    <table>
      <tr>
        <th>操作</th>
        <th>状态标签</th>
        <th>源行号</th>
        <th>记录ID</th>
        <th>单据编号</th>
        <th>分录行号</th>
        <th>单据日期</th>
        <th>复核状态</th>
        <th>生效状态</th>
        <th>治理判定</th>
        <th>版本状态</th>
        <th>当前有效</th>
        <th>生命周期</th>
        <th>身份模式</th>
        <th>商品编码</th>
        <th>整单出库</th>
        <th>本产品出库</th>
        <th>已出库</th>
        <th>未发货</th>
        <th>已开票</th>
        <th>未开票</th>
        <th>最近出库</th>
        <th>治理说明</th>
      </tr>
      ${rows}
    </table>
  `;
}

function ensureAuditDeleteControls() {
  const riskRoot = el("auditRiskNote");
  const dockRoot = el("auditControlDock");
  if (!riskRoot || !dockRoot || el("auditDeleteTools") || el("auditTopQueryTools")) return;

  const topShell = document.createElement("div");
  topShell.id = "auditTopQueryTools";
  topShell.style.marginTop = "10px";
  topShell.innerHTML = `
    <div class="grid three">
      <label>审计视角
        <select id="auditLifecycleView">
          <option value="current">当前数据</option>
          <option value="review_queue">待复核区</option>
          <option value="archived">已归档</option>
          <option value="special_case">特殊情况区</option>
          <option value="recycle_bin">回收站</option>
        </select>
      </label>
      <label>对象筛选
        <select id="auditLifecycleObjectType">
          <option value="all">全部对象</option>
          <option value="file">仅文件</option>
          <option value="record">仅记录</option>
        </select>
      </label>
      <label style="display:flex;align-items:center;gap:6px;margin-top:24px;">
        <input id="auditIncludeDeleted" type="checkbox" />
        <span>兼容旧口径：当前页含全部生命周期</span>
      </label>
    </div>
    <div id="auditArchiveLookupTools" class="grid two" style="margin-top:8px;display:none;">
      <label>归档查询关键词
        <input id="auditArchiveLookupKeyword" placeholder="可搜批次ID、文件名、文件ID、记录ID、单据编号、行号" />
      </label>
      <div style="display:flex;align-items:flex-end;gap:8px;">
        <button id="btnAuditArchiveLookupClear" type="button">清空归档查询</button>
      </div>
    </div>
  `;
  dockRoot.insertAdjacentElement("afterend", topShell);

  const shell = document.createElement("div");
  shell.id = "auditDeleteTools";
  shell.style.marginTop = "10px";
  shell.innerHTML = `
    <div class="grid two" style="margin-top:8px;">
      <label>审计视角快捷切换
        <select id="auditLifecycleViewQuick">
          <option value="current">当前数据</option>
          <option value="review_queue">待复核区</option>
          <option value="archived">已归档</option>
          <option value="special_case">特殊情况区</option>
          <option value="recycle_bin">回收站</option>
        </select>
      </label>
      <label>目标类型
        <select id="auditTargetObjectType">
          <option value="file">文件</option>
          <option value="record">记录</option>
        </select>
      </label>
    </div>
    <div class="grid one" style="margin-top:8px;">
      <label>目标ID
        <input id="auditTargetObjectId" placeholder="可由列表按钮自动带入" />
      </label>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
      <button id="btnAuditReviewCompare" type="button">查看待复核原因</button>
      <button id="btnAuditDeleteImpact" type="button">预览删除影响</button>
      <button id="btnAuditSoftDelete" type="button">放入回收站</button>
      <button id="btnAuditMoveToSpecialCase" type="button">放入特殊情况区</button>
      <button id="btnAuditReturnToReviewQueue" type="button">放回待复核区</button>
      <button id="btnAuditReturnToJobList" type="button">回到job列表</button>
      <button id="btnAuditRestore" type="button">恢复</button>
      <button id="btnAuditArchive" type="button">归档</button>
      <button id="btnAuditHardDeletePreview" type="button">预览硬删除</button>
      <button id="btnAuditHardDelete" type="button">执行硬删除</button>
    </div>
    <label>操作原因（可选）
      <input id="auditDeleteReason" placeholder="例如：误上传 / 恢复说明 / 回退说明" />
    </label>
    <label>特殊情况原因
      <select id="auditSpecialCaseReason">
        <option value="">请选择</option>
        <option value="数量调整后完成">数量调整后完成</option>
        <option value="金额/折扣调整后完成">金额/折扣调整后完成</option>
        <option value="质量问题协商后完成">质量问题协商后完成</option>
        <option value="客户取消部分后完成">客户取消部分后完成</option>
        <option value="其他特殊完成">其他特殊完成</option>
      </select>
    </label>
    <label>特殊情况备注（可选）
      <input id="auditSpecialCaseNote" placeholder="例如：员工已确认，本行不用再扫描" />
    </label>
    <div id="auditDeleteImpactView" class="hint" style="margin-top:8px;"></div>
    <div id="auditReviewQueueView" class="hint" style="margin-top:8px;"></div>
    <div id="auditLifecycleList" class="hint" style="margin-top:8px;"></div>
  `;
  riskRoot.insertAdjacentElement("afterend", shell);

  const includeDeleted = el("auditIncludeDeleted");
  if (includeDeleted) {
    includeDeleted.checked = !!state.audit.includeDeleted;
    includeDeleted.addEventListener("change", () => {
      state.audit.includeDeleted = !!includeDeleted.checked;
      if (!state.audit.selectedJobId) return;
      Promise.all([
        refreshAuditFiles(state.audit.selectedJobId),
        refreshAuditOverview(state.audit.selectedJobId),
      ])
        .then(() => refreshAuditLifecycleObjects())
        .catch((err) => alert(err.message));
    });
  }
  const lifecycleView = el("auditLifecycleView");
  if (lifecycleView) {
    syncAuditLifecycleViewControls();
    lifecycleView.addEventListener("change", () => {
      applyAuditLifecycleViewChange(lifecycleView.value || "current")
        .catch((err) => renderAuditRiskMessage(`切换审计视角失败：${err.message || err}`, "error"));
    });
  }
  const lifecycleViewQuick = el("auditLifecycleViewQuick");
  if (lifecycleViewQuick) {
    syncAuditLifecycleViewControls();
    lifecycleViewQuick.addEventListener("change", () => {
      applyAuditLifecycleViewChange(lifecycleViewQuick.value || "current")
        .catch((err) => renderAuditRiskMessage(`切换审计视角失败：${err.message || err}`, "error"));
    });
  }
  const lifecycleObjectType = el("auditLifecycleObjectType");
  if (lifecycleObjectType) {
    lifecycleObjectType.value = state.audit.lifecycleObjectType;
    lifecycleObjectType.addEventListener("change", () => {
      state.audit.lifecycleObjectType = lifecycleObjectType.value || "all";
      refreshAuditLifecycleObjects().catch((err) =>
        renderAuditRiskMessage(`刷新对象列表失败：${err.message || err}`, "error")
      );
    });
  }
  const archiveLookupKeyword = el("auditArchiveLookupKeyword");
  if (archiveLookupKeyword) {
    archiveLookupKeyword.value = state.audit.archiveQueryKeyword || "";
    archiveLookupKeyword.addEventListener("input", () => {
      state.audit.archiveQueryKeyword = archiveLookupKeyword.value || "";
      refreshAuditLifecycleObjects().catch((err) =>
        renderAuditRiskMessage(`归档查询刷新失败：${err.message || err}`, "error")
      );
    });
  }
  if (el("btnAuditArchiveLookupClear")) {
    el("btnAuditArchiveLookupClear").addEventListener("click", () => {
      state.audit.archiveQueryKeyword = "";
      if (el("auditArchiveLookupKeyword")) el("auditArchiveLookupKeyword").value = "";
      refreshAuditLifecycleObjects().catch((err) =>
        renderAuditRiskMessage(`归档查询刷新失败：${err.message || err}`, "error")
      );
    });
  }
  const auditTargetObjectType = el("auditTargetObjectType");
  if (auditTargetObjectType) {
    auditTargetObjectType.addEventListener("change", () => {
      state.audit.selectedObjectType = auditTargetObjectType.value || "record";
      resetAuditHardDeletePreviewGuard();
      if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
      updateAuditActionButtons();
    });
  }
  const auditTargetObjectId = el("auditTargetObjectId");
  if (auditTargetObjectId) {
    auditTargetObjectId.addEventListener("input", () => {
      state.audit.selectedObjectId = auditTargetObjectId.value || "";
      resetAuditHardDeletePreviewGuard();
      if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
      updateAuditActionButtons();
    });
  }
  if (el("btnAuditDeleteImpact")) {
    el("btnAuditDeleteImpact").addEventListener("click", () =>
      previewAuditDeleteImpact().catch((err) =>
        renderAuditRiskMessage(`删除影响预览失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditReviewCompare")) {
    el("btnAuditReviewCompare").addEventListener("click", () =>
      showAuditReviewQueueReason().catch((err) =>
        renderAuditRiskMessage(`待复核原因加载失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditSoftDelete")) {
    el("btnAuditSoftDelete").addEventListener("click", () =>
      softDeleteAuditFile().catch((err) =>
        renderAuditRiskMessage(`文件软删除失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditRestore")) {
    el("btnAuditRestore").addEventListener("click", () =>
      restoreAuditObject().catch((err) =>
        renderAuditRiskMessage(`恢复失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditArchive")) {
    el("btnAuditArchive").addEventListener("click", () =>
      archiveAuditObject().catch((err) =>
        renderAuditRiskMessage(`归档失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditHardDeletePreview")) {
    el("btnAuditHardDeletePreview").addEventListener("click", () =>
      previewAuditHardDelete().catch((err) =>
        renderAuditRiskMessage(`硬删除预览失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditHardDelete")) {
    el("btnAuditHardDelete").addEventListener("click", () =>
      hardDeleteAuditObject().catch((err) =>
        renderAuditRiskMessage(`硬删除失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditMoveToSpecialCase")) {
    el("btnAuditMoveToSpecialCase").addEventListener("click", () =>
      moveAuditRecordToSpecialCase().catch((err) =>
        renderAuditRiskMessage(`放入特殊情况区失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditReturnToReviewQueue")) {
    el("btnAuditReturnToReviewQueue").addEventListener("click", () =>
      returnAuditObjectToReviewQueue().catch((err) =>
        renderAuditRiskMessage(`放回待复核区失败：${err.message || err}`, "error")
      )
    );
  }
  if (el("btnAuditReturnToJobList")) {
    el("btnAuditReturnToJobList").addEventListener("click", () =>
      returnAuditObjectToJobList().catch((err) =>
        renderAuditRiskMessage(`回到job列表失败：${err.message || err}`, "error")
      )
    );
  }
  updateArchiveLookupControls();
  updateAuditActionButtons();
}

function clearAuditPreviewObjectUrl() {
  if (state.audit.previewObjectUrl) {
    URL.revokeObjectURL(state.audit.previewObjectUrl);
    state.audit.previewObjectUrl = "";
  }
}

function translateFieldLabel(key) {
  const normalized = String(key || "").trim().toLowerCase();
  return FIELD_LABELS[normalized] || key;
}

function filterKeyLabel(key) {
  const normalized = String(key || "").trim().toLowerCase();
  return FILTER_KEY_LABELS[normalized] || translateFieldLabel(normalized);
}

function filterValueLabel(key, value) {
  const normalized = String(key || "").trim().toLowerCase();
  if (normalized === "doc_type") return documentTypeLabel(value);
  if (normalized === "batch_view") return batchViewLabel(value);
  return formatDisplayValue(normalized, value);
}

function outboundStatusLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return OUTBOUND_STATUS_LABELS[normalized] || value || "";
}

function isIdentifierField(key) {
  const normalized = String(key || "").trim().toLowerCase();
  return normalized === "id" || normalized.endsWith("_id");
}

function formatDisplayValue(key, value) {
  const normalized = String(key || "").trim().toLowerCase();
  if (value === null || value === undefined) return "";
  if (isIdentifierField(normalized)) return value;

  if (normalized === "document_type" || normalized === "doc_type") {
    return documentTypeLabel(value);
  }
  if (normalized === "parse_status") {
    return statusLabel(value);
  }
  if (normalized === "duplicate_risk") {
    return duplicateRiskLabel(value);
  }
  if (normalized === "change_type") {
    return changeTypeLabel(value);
  }
  if (normalized === "review_status") {
    return reviewStatusLabel(value);
  }
  if (normalized === "effective_status") {
    return effectiveStatusLabel(value);
  }
  if (normalized === "lifecycle_state") {
    return lifecycleStateLabel(value);
  }
  if (normalized === "version_status") {
    return versionStatusLabel(value);
  }
  if (normalized === "identity_mode") {
    return identityModeLabel(value);
  }
  if (normalized === "order_outbound_status" || normalized === "line_outbound_status") {
    return outboundStatusLabel(value);
  }
  if (normalized === "delete_origin") {
    return deleteOriginLabel(value);
  }
  if (normalized === "object_type") {
    return objectTypeLabel(value);
  }
  if (normalized === "source_type") {
    return duplicateSourceTypeLabel(value);
  }
  if (normalized === "is_deleted") {
    return deletedStatusLabel(toBooleanValue(value));
  }
  if (normalized === "preview_kind") {
    return previewKindLabel(value);
  }
  if (normalized === "file_status_summary") {
    return formatJobStatus(value, "");
  }
  if (
    normalized === "status" ||
    normalized === "task_status" ||
    normalized === "latest_task_status" ||
    normalized === "alert_status"
  ) {
    return statusLabel(value);
  }
  if (typeof value === "boolean") return value ? "是" : "否";

  if (typeof value === "string") {
    const raw = value.trim();
    if (!raw) return value;
    if (raw.includes("|") && /[a-z_]+\(\d+\)/i.test(raw)) {
      return formatJobStatus(raw, "");
    }
    if (/^(order|shipment|payment_notice|invoice)$/i.test(raw)) {
      return documentTypeLabel(raw);
    }
    if (/^(queued|running|succeeded|failed|created|files_uploaded|success|parsed|parsing|open|resolved|unknown|no_files)$/i.test(raw)) {
      return statusLabel(raw);
    }
    if (/^(same_job|global|none|not_checked)$/i.test(raw)) {
      return duplicateRiskLabel(raw);
    }
  }
  return value;
}

function toBooleanValue(value) {
  if (typeof value === "boolean") return value;
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "y" || normalized === "是";
}

async function refreshAuditJobs() {
  renderAuditRoleHint();
  if (getRole() !== "admin") {
    renderAuditJobsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }

  const params = new URLSearchParams();
  const by = (id) => (el(id)?.value || "").trim();
  const activeFilters = [];
  const useFilter = (queryKey, inputId) => {
    const value = by(inputId);
    if (!value) return;
    params.set(queryKey, value);
    activeFilters.push({ key: queryKey, value });
  };

  useFilter("job_id", "auditFilterJobId");
  useFilter("task_status", "auditFilterTaskStatus");
  useFilter("alert_status", "auditFilterAlertStatus");
  useFilter("doc_type", "auditFilterDocType");
  useFilter("parse_status", "auditFilterParseStatus");
  useFilter("date_from", "auditFilterDateFrom");
  useFilter("date_to", "auditFilterDateTo");
  const batchView = (el("auditBatchView")?.value || state.audit.batchView || "all").trim() || "all";
  state.audit.batchView = batchView;
  params.set("batch_view", batchView);
  activeFilters.push({ key: "batch_view", value: batchView });
  const filterSummary = buildAuditFilterSummary(activeFilters);

  try {
    const res = await fetchAllAdminJobSummaries(params);
    const items = res.items || [];
    const total = Number.isFinite(res.total) ? res.total : items.length;
    const autoDeletedTotal = sumAutoDeletedDuplicateCount(items);
    const diag = `${renderAuditDiagHeader(filterSummary, total, items.length).replace(
      "</p>",
      `<br/>自动清理总数：${autoDeletedTotal}</p>`,
    )}`;
    if (el("auditJobsMeta")) el("auditJobsMeta").innerHTML = diag;
    if (!items.length) {
      el("auditJobsList").innerHTML = `<p class="hint">接口已返回 200，但本次筛选无匹配数据。</p>`;
      return;
    }
    el("auditJobsList").innerHTML = renderJobSummaryTable(items);
  } catch (err) {
    const status = Number(err && err.status);
    const text = err && err.message ? err.message : String(err);
    if (status === 403) {
      renderAuditJobsMessage(`审计列表加载失败：仅管理员可用（403）。请将角色切换为管理员。生效筛选：${filterSummary}`, "error");
      return;
    }
    if (status === 401) {
      renderAuditJobsMessage(`审计列表加载失败：缺少或无效角色信息（401）。生效筛选：${filterSummary}`, "error");
      return;
    }
    renderAuditJobsMessage(`审计列表加载失败：${text}。生效筛选：${filterSummary}`, "error");
  }
}

async function openAuditJob(jobId, lifecycleHint = "") {
  const normalizedHint = String(lifecycleHint || "").trim().toLowerCase();
  if (normalizedHint && ["current", "review_queue", "archived", "special_case", "recycle_bin"].includes(normalizedHint)) {
    state.audit.lifecycleView = normalizedHint;
    syncAuditLifecycleViewControls();
  }
  state.audit.selectedJobId = jobId;
  el("auditSelectedJob").textContent = `当前批次ID: ${jobId}`;
  clearAuditFilePanels("已切换批次，请重新选择文件。");
  if (el("jobIdInput")) el("jobIdInput").value = jobId;
  await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId)]);
  await refreshAuditLifecycleObjects();
}

async function openArchiveLookupEntry() {
  state.audit.lifecycleView = "archived";
  state.audit.lifecycleObjectType = "all";
  state.audit.archiveQueryKeyword = "";
  state.audit.selectedJobId = "";
  state.audit.selectedFileId = "";
  state.audit.detailFileId = "";
  state.audit.selectedObjectId = "";
  state.audit.selectedObjectLabel = "";
  state.audit.selectedObjectType = "record";
  syncAuditLifecycleViewControls();
  if (el("auditLifecycleObjectType")) el("auditLifecycleObjectType").value = "all";
  if (el("auditArchiveLookupKeyword")) el("auditArchiveLookupKeyword").value = "";
  if (el("auditSelectedJob")) {
    el("auditSelectedJob").textContent = "未选择 job（当前处于归档查询入口，可直接查全局已归档对象）";
  }
  clearAuditFilePanels("已进入归档查询入口。可按批次ID、文件名、文件ID、记录ID、单据编号继续查找。");
  updateArchiveLookupControls();
  updateAuditActionButtons();
  await refreshAuditLifecycleObjects();
  renderAuditRiskMessage("已进入归档查询入口。可先按批次ID、文件名、文件ID、记录ID、单据编号继续查找。");
  const auditControlDock = el("auditControlDock");
  if (auditControlDock && typeof auditControlDock.scrollIntoView === "function") {
    auditControlDock.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function refreshAuditFiles(jobId) {
  const includeDeleted = state.audit.includeDeleted ? "true" : "false";
  const lifecycleView = state.audit.lifecycleView || "current";
  const res = await api(
    `/admin/jobs/${encodeURIComponent(jobId)}/files?include_deleted=${includeDeleted}&lifecycle_view=${encodeURIComponent(lifecycleView)}`
  );
  const items = (res && res.items) || [];
  const interactiveFileView =
    lifecycleView === "current" ||
    lifecycleView === "review_queue" ||
    lifecycleView === "archived" ||
    lifecycleView === "special_case" ||
    lifecycleView === "recycle_bin";
  if (!interactiveFileView) {
    const tip =
      lifecycleView === "archived"
        ? "已归档文件视角"
        : lifecycleView === "special_case"
        ? "特殊情况区文件视角"
        : lifecycleView === "review_queue"
        ? "待复核区文件视角"
          : "回收站文件视角";
    el("auditFilesList").innerHTML = items.length
      ? toTable(
          items.map((item) => ({
            file_id: item.file_id,
            filename: item.filename,
            document_type: item.document_type,
            lifecycle_state: item.lifecycle_state,
            review_queue_record_count: item.review_queue_record_count,
            review_released_record_count: item.review_released_record_count,
            auto_deleted_duplicate_count: item.auto_deleted_duplicate_count,
            has_anomaly: item.has_anomaly,
            anomaly_codes: batchAnomalySummary(item.anomaly_codes),
            deleted_at: item.deleted_at,
            archived_at: item.archived_at,
          }))
        )
      : `<p>${tip}暂无文件</p>`;
    return;
  }
  if (!items.length) {
    const tip =
      lifecycleView === "review_queue"
        ? "该批次在待复核区暂无文件"
        : lifecycleView === "special_case"
        ? "该批次在特殊情况区暂无文件"
        : lifecycleView === "archived"
        ? "该批次在已归档区暂无文件"
        : lifecycleView === "recycle_bin"
        ? "该批次在回收站暂无文件"
        : `该批次暂无文件${state.audit.includeDeleted ? "（含已删除）" : "（仅未删除）"}`;
    el("auditFilesList").innerHTML = `<p>${tip}</p>`;
    clearAuditFilePanels("该批次当前无可选文件。");
    return;
  }

  const rows = items
    .map(
      (f) => `
      <tr>
        <td><button data-file-open="${f.file_id}">${f.file_id}</button></td>
        <td><button data-file-open="${f.file_id}">${f.filename ?? ""}</button></td>
        <td>${formatDisplayValue("document_type", f.document_type ?? "")}</td>
        <td>${formatDisplayValue("parse_status", f.parse_status ?? "")}</td>
        <td>${f.parsed_count ?? 0}</td>
        <td>${f.review_queue_record_count ?? 0}</td>
        <td>${f.review_released_record_count ?? 0}</td>
        <td>${f.auto_deleted_duplicate_count ?? 0}</td>
        <td>${f.has_anomaly ? "是" : "否"}</td>
        <td>${escapeHtml(batchAnomalySummary(f.anomaly_codes))}</td>
        <td>${fmtTime(f.uploaded_at)}</td>
        <td>${f.file_size ?? 0}</td>
        <td>${duplicateRiskLabel(f.duplicate_risk)}</td>
        <td>${lifecycleStateLabel(f.lifecycle_state)}${f.deleted_at ? ` (${fmtTime(f.deleted_at)})` : ""}${f.archived_at ? ` / ${fmtTime(f.archived_at)}` : ""}</td>
        <td>
          <button data-file-records="${f.file_id}">查看归一化记录</button>
          <button data-file-preview="${f.file_id}">预览</button>
          <button data-file-download="${f.file_id}">下载</button>
        </td>
      </tr>`
    )
    .join("");

  el("auditFilesList").innerHTML = `
    <table>
      <tr>
        <th>文件ID</th>
        <th>原始文件名</th>
        <th>单据类型</th>
        <th>解析状态</th>
        <th>解析条数</th>
        <th>待复核数</th>
        <th>复核放回数</th>
        <th>自动清理重复数</th>
        <th>异常</th>
        <th>异常原因</th>
        <th>上传时间</th>
        <th>文件大小</th>
        <th>重复风险</th>
        <th>生命周期</th>
        <th>操作</th>
      </tr>
      ${rows}
    </table>
  `;

  const selectedId = (state.audit.selectedFileId || "").trim();
  if (!selectedId) return;
  const stillVisible = items.some((f) => (f.file_id || "").trim() === selectedId);
  if (!stillVisible) {
    clearAuditFilePanels("当前已选文件不在该 job 可见范围内，请重新选择文件。");
  }
}

async function refreshAuditOverview(jobId) {
  const includeDeleted = state.audit.includeDeleted ? "true" : "false";
  const lifecycleView = state.audit.lifecycleView || "current";
  const ov = await api(
    `/admin/jobs/${encodeURIComponent(jobId)}/overview?include_deleted=${includeDeleted}&lifecycle_view=${encodeURIComponent(lifecycleView)}`
  );
  const row = {
    lifecycle_view: lifecycleStateLabel(ov.lifecycle_view ?? lifecycleView),
    total_record_count: ov.total_record_count ?? ov.normalized_record_count ?? 0,
    effective_scan_count: ov.effective_scan_count ?? ov.normalized_record_count ?? 0,
    skip_scan_count: ov.skip_scan_count ?? 0,
    normalized_record_count: ov.normalized_record_count ?? 0,
    match_group_count: ov.match_group_count ?? 0,
    open_alert_count: ov.open_alert_count ?? 0,
    resolved_alert_count: ov.resolved_alert_count ?? 0,
    latest_task_id: ov.latest_task_id ?? "",
    latest_task_updated_at: fmtTime(ov.latest_task_updated_at),
    latest_task_error: ov.latest_task_error ?? "",
  };
  el("auditOverview").innerHTML = toTable([row]);
}

async function openAuditFile(fileId) {
  const currentFileId = (fileId || "").trim();
  if (!currentFileId) return;
  state.audit.selectedFileId = currentFileId;
  state.audit.detailFileId = "";
  clearAuditPreviewObjectUrl();
  if (el("auditPreview")) el("auditPreview").innerHTML = "<p class=\"hint\">已切换文件，请点击“预览”查看原文件。</p>";
  if (el("auditDuplicatesView")) el("auditDuplicatesView").innerHTML = "";
  if (el("auditDeleteImpactView")) el("auditDeleteImpactView").innerHTML = "";
  if (el("auditReviewQueueView")) el("auditReviewQueueView").innerHTML = "";
  const detail = await api(`/admin/files/${encodeURIComponent(currentFileId)}`);
  if ((state.audit.selectedFileId || "").trim() !== currentFileId) return;
  const data = {
    file_id: detail.file_id,
    job_id: detail.job_id,
    filename: detail.filename,
    document_type: formatDisplayValue("document_type", detail.document_type),
    parse_status: formatDisplayValue("parse_status", detail.parse_status),
    parse_error: detail.parse_error ?? "",
    parsed_count: detail.parsed_count ?? 0,
    auto_deleted_duplicate_count: detail.auto_deleted_duplicate_count ?? 0,
    uploaded_at: fmtTime(detail.uploaded_at),
    file_size: detail.file_size ?? 0,
    duplicate_risk: duplicateRiskLabel(detail.duplicate_risk),
    total_record_count: detail.total_record_count ?? detail.normalized_record_count ?? 0,
    effective_scan_count: detail.effective_scan_count ?? detail.normalized_record_count ?? 0,
    skip_scan_count: detail.skip_scan_count ?? 0,
    normalized_record_count: detail.normalized_record_count ?? 0,
    match_group_count: detail.match_group_count ?? 0,
    open_alert_count: detail.open_alert_count ?? 0,
    resolved_alert_count: detail.resolved_alert_count ?? 0,
    preview_kind: formatDisplayValue("preview_kind", detail.preview_kind ?? ""),
    file_hash_sha256: detail.file_hash_sha256 ?? "",
    lifecycle_state: lifecycleStateLabel(detail.lifecycle_state),
    is_deleted: !!detail.is_deleted,
    is_archived: !!detail.is_archived,
    deleted_at: fmtTime(detail.deleted_at),
    restored_at: fmtTime(detail.restored_at),
    archived_at: fmtTime(detail.archived_at),
  };
  el("auditFileDetail").innerHTML = toTable([data]);
  state.audit.detailFileId = currentFileId;
  clearAuditActionTarget(`已打开文件：${detail.filename || currentFileId}。文件只用于查看来源和内容；如需操作，请在对象列表或归一化记录里选择记录。`);
}

async function previewAuditNormalizedRecords(fileId = state.audit.selectedFileId) {
  const currentFileId = (fileId || "").trim();
  if (!currentFileId) {
    renderAuditRiskMessage("请先选择文件，再查看归一化记录。", "error");
    return;
  }
  if ((state.audit.selectedFileId || "").trim() !== currentFileId || (state.audit.detailFileId || "").trim() !== currentFileId) {
    await openAuditFile(currentFileId);
  }
  const res = await api(`/admin/files/${encodeURIComponent(currentFileId)}/records`);
  if ((state.audit.selectedFileId || "").trim() !== currentFileId) return;

  const statsRow = {
    total_record_count: res.total_record_count ?? res.count ?? 0,
    effective_scan_count: res.effective_scan_count ?? res.count ?? 0,
    skip_scan_count: res.skip_scan_count ?? 0,
  };
  const rows = (res.items || []).map((it) => ({
    record_id: it.record_id ?? "",
    source_row: it.source_row ?? 0,
    scan_state: it.scan_state ?? "",
    customer_order_no: it.customer_order_no ?? "",
    entry_line_no: it.entry_line_no ?? "",
    biz_date: it.biz_date ?? "",
    lifecycle_state: it.lifecycle_state ?? "",
    change_type: changeTypeLabel(it.change_type ?? ""),
    version_status: versionStatusLabel(it.version_status ?? ""),
    is_current_effective: it.is_current_effective,
    identity_mode: identityModeLabel(it.identity_mode ?? ""),
    duplicate_of_record_id: it.duplicate_of_record_id ?? "",
    superseded_by_record_id: it.superseded_by_record_id ?? "",
    supersedes_record_id: it.supersedes_record_id ?? "",
    governance_reason: it.governance_reason ?? "",
    item_name: it.item_name ?? "",
    item_code: it.item_code ?? "",
    quantity: it.quantity ?? "",
    order_outbound_status: it.order_outbound_status ?? "",
    line_outbound_status: it.line_outbound_status ?? "",
    executed_shipped_qty: it.executed_shipped_qty ?? "",
    order_unshipped_qty: it.order_unshipped_qty ?? "",
    invoiced_qty: it.invoiced_qty ?? "",
    uninvoiced_qty: it.uninvoiced_qty ?? "",
    due_date: it.due_date ?? "",
    latest_outbound_date: it.latest_outbound_date ?? "",
    scan_reason: it.scan_reason ?? "",
  }));
  const body = renderAuditRecordTable(res.items || []);
  el("auditPreview").innerHTML = `
    <p class="hint">当前文件归一化记录（${res.count ?? rows.length} 条）</p>
    ${toTable([statsRow])}
    ${body}
  `;
}

function requireRecordAuditTarget(actionLabel) {
  const { objectType, objectId } = getAuditActionTarget();
  if (objectType !== "record" || !objectId) {
    renderAuditRiskMessage(`${actionLabel}只允许对记录执行。文件只用于查看来源和内容。请先点“记录目标”。`, "error");
    return null;
  }
  return { objectType, objectId };
}

async function previewAuditDeleteImpact() {
  const target = requireRecordAuditTarget("删除影响预览");
  if (!target) return;
  const { objectType, objectId } = target;
  const path = objectType === "record" ? `/admin/records/${encodeURIComponent(objectId)}/delete-impact` : `/admin/files/${encodeURIComponent(objectId)}/delete-impact`;
  const impact = await api(path, {
    method: "POST",
    body: JSON.stringify({}),
  });
  const row = { ...impact };
  if (el("auditDeleteImpactView")) {
    el("auditDeleteImpactView").innerHTML = toTable([row]);
  }
  renderAuditRiskMessage("删除影响预览已生成。确认无误后可直接执行放入回收站。");
}

async function softDeleteAuditFile() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const target = requireRecordAuditTarget("放入回收站");
  if (!jobId || !target) return;
  const { objectType, objectId } = target;
  const reason = (el("auditDeleteReason")?.value || "").trim();
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/soft-delete`
      : `/admin/files/${encodeURIComponent(objectId)}/soft-delete`;
  await api(path, {
    method: "POST",
    body: JSON.stringify({
      reason,
    }),
  });

  clearAuditPreviewObjectUrl();
  clearAuditActionTarget();
  if (el("auditPreview")) el("auditPreview").innerHTML = "<p class=\"hint\">记录已放入回收站，请重新选择文件或记录。</p>";
  if (el("auditFileDetail")) el("auditFileDetail").innerHTML = "<p class=\"hint\">当前仅保留文件查看；如需继续操作，请重新选择记录。</p>";
  if (el("auditDeleteImpactView")) {
    el("auditDeleteImpactView").innerHTML = "<p class=\"hint\">已放入回收站。可继续选择其他对象。</p>";
  }
  if (el("auditDuplicatesView")) {
    el("auditDuplicatesView").innerHTML = "";
  }
  renderAuditRiskMessage(`对象 ${objectId} 已进入回收站。后端已自动排队重算相关 job。`);

  await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  await refreshAuditLifecycleObjects();
}

async function restoreAuditObject() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const target = requireRecordAuditTarget("恢复");
  if (!target) return;
  const { objectType, objectId } = target;
  const reason = (el("auditDeleteReason")?.value || "").trim();
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/restore`
      : `/admin/files/${encodeURIComponent(objectId)}/restore`;
  const res = await api(path, { method: "POST", body: JSON.stringify({ reason }) });
  if (res?.lifecycle_state === "archived" || res?.lifecycle_state === "special_case") {
    renderAuditRiskMessage(`对象 ${objectId} 已恢复回${lifecycleStateLabel(res.lifecycle_state)}，不参与扫描。`);
  } else if (Array.isArray(res?.recompute_task_ids) && res.recompute_task_ids.length > 0) {
    renderAuditRiskMessage(`对象 ${objectId} 已恢复。后端已自动排队重算相关 job。`);
  } else {
    renderAuditRiskMessage(`对象 ${objectId} 已恢复。`);
  }
  if (jobId) {
    await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  }
  await refreshAuditLifecycleObjects();
}

async function archiveAuditObject() {
  const target = requireRecordAuditTarget("归档");
  if (!target) return;
  const { objectType, objectId } = target;
  const reason = (el("auditDeleteReason")?.value || "").trim();
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/archive`
      : `/admin/files/${encodeURIComponent(objectId)}/archive`;
  await api(path, { method: "POST", body: JSON.stringify({ reason }) });
  renderAuditRiskMessage(`对象 ${objectId} 已归档。`);
  await refreshAuditLifecycleObjects();
}

async function moveAuditRecordToSpecialCase() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const { objectType, objectId } = getAuditActionTarget();
  if (objectType !== "record" || !objectId) {
    renderAuditRiskMessage("特殊情况区只支持记录对象。请先点右侧对象列表或归一化记录里的“记录目标”。", "error");
    return;
  }
  const specialCaseReason = (el("auditSpecialCaseReason")?.value || "").trim();
  const specialCaseNote = (el("auditSpecialCaseNote")?.value || "").trim();
  await api(`/admin/records/${encodeURIComponent(objectId)}/special-case`, {
    method: "POST",
    body: JSON.stringify({
      special_case_reason: specialCaseReason || null,
      special_case_note: specialCaseNote || null,
    }),
  });
  const displayReason = specialCaseReason || "其他特殊完成";
  if (el("auditReviewQueueView")) {
    el("auditReviewQueueView").innerHTML = `<p class="hint">记录已放入特殊情况区：${escapeHtml(displayReason)}${specialCaseNote ? ` / ${escapeHtml(specialCaseNote)}` : ""}</p>`;
  }
  renderAuditRiskMessage(`记录 ${objectId} 已放入特殊情况区，不再参与当前扫描。`);
  if (jobId) {
    await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  }
  await refreshAuditLifecycleObjects();
  if (state.audit.selectedFileId) {
    await previewAuditNormalizedRecords(state.audit.selectedFileId);
  }
}

async function returnAuditObjectToJobList() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const target = requireRecordAuditTarget("回到job列表");
  if (!target) return;
  const { objectType, objectId } = target;
  const reason = (el("auditDeleteReason")?.value || "").trim();
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/return-to-job-list`
      : `/admin/files/${encodeURIComponent(objectId)}/return-to-job-list`;
  const res = await api(path, { method: "POST", body: JSON.stringify({ reason }) });
  if (Array.isArray(res?.recompute_task_ids) && res.recompute_task_ids.length > 0) {
    renderAuditRiskMessage(`对象 ${objectId} 已回到job列表，并重新参与扫描与预警。`);
  } else {
    renderAuditRiskMessage(`对象 ${objectId} 已回到job列表。`);
  }
  if (jobId) {
    await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  }
  await refreshAuditLifecycleObjects();
  if (state.audit.selectedFileId) {
    await previewAuditNormalizedRecords(state.audit.selectedFileId);
  }
}

async function returnAuditObjectToReviewQueue() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const target = requireRecordAuditTarget("放回待复核区");
  if (!target) return;
  const { objectType, objectId } = target;
  const reason = (el("auditDeleteReason")?.value || "").trim();
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/return-to-review-queue`
      : `/admin/files/${encodeURIComponent(objectId)}/return-to-review-queue`;
  await api(path, { method: "POST", body: JSON.stringify({ reason }) });
  renderAuditRiskMessage(`对象 ${objectId} 已放回待复核区。`);
  if (jobId) {
    await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  }
  await refreshAuditLifecycleObjects();
  if (state.audit.selectedFileId) {
    await previewAuditNormalizedRecords(state.audit.selectedFileId);
  }
}

async function previewAuditHardDelete() {
  const target = requireRecordAuditTarget("硬删除预览");
  if (!target) return;
  const { objectType, objectId } = target;
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/hard-delete-preview`
      : `/admin/files/${encodeURIComponent(objectId)}/hard-delete-preview`;
  const preview = await api(path, { method: "POST", body: JSON.stringify({}) });
  state.audit.hardDeletePreviewToken = String(preview.preview_token || "").trim();
  state.audit.hardDeletePreviewObjectType = objectType;
  state.audit.hardDeletePreviewObjectId = objectId;
  if (el("auditDeleteImpactView")) {
    el("auditDeleteImpactView").innerHTML = toTable([preview]);
  }
  updateAuditActionButtons();
  renderAuditRiskMessage("硬删除预览已生成。现在才允许执行硬删除。");
}

async function hardDeleteAuditObject() {
  const jobId = (state.audit.selectedJobId || "").trim();
  const target = requireRecordAuditTarget("执行硬删除");
  if (!target) return;
  if (!hasFreshAuditHardDeletePreview()) {
    renderAuditRiskMessage("请先点一次“预览硬删除”，再执行真正删除。", "error");
    return;
  }
  const { objectType, objectId } = target;
  const path =
    objectType === "record"
      ? `/admin/records/${encodeURIComponent(objectId)}/hard-delete`
      : `/admin/files/${encodeURIComponent(objectId)}/hard-delete`;
  await api(path, {
    method: "POST",
    body: JSON.stringify({
      preview_token: state.audit.hardDeletePreviewToken || null,
    }),
  });
  resetAuditHardDeletePreviewGuard();
  if (el("auditDeleteImpactView")) {
    el("auditDeleteImpactView").innerHTML = "<p class=\"hint\">已执行硬删除。</p>";
  }
  renderAuditRiskMessage(`对象 ${objectId} 已硬删除。`);
  if (jobId) {
    await Promise.all([refreshAuditFiles(jobId), refreshAuditOverview(jobId), refreshAuditJobs()]);
  }
  await refreshAuditLifecycleObjects();
}

function renderAuditReviewReasonList(title, items) {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return "";
  const rows = values.map((item) => `<li>${escapeHtml(translateFieldLabel(item))}</li>`).join("");
  return `
    <p class="hint" style="margin:6px 0;">${escapeHtml(title)}</p>
    <ul style="margin:0 0 10px 20px;padding:0;">${rows}</ul>
  `;
}

async function showAuditReviewQueueReason() {
  const { objectType, objectId } = getAuditActionTarget();
  if (objectType !== "record" || !objectId) {
    renderAuditRiskMessage("请先在待复核区选中记录对象。", "error");
    return;
  }
  const res = await api(`/admin/review-queue/records/${encodeURIComponent(objectId)}/compare`);
  const root = el("auditReviewQueueView");
  if (!root) return;
  const reviewRecord = res.review_record || {};
  const basicInfo = {
    record_id: reviewRecord.record_id || objectId,
    filename: reviewRecord.filename || reviewRecord.source_filename || "",
    source_row: reviewRecord.source_row || 0,
    job_id: reviewRecord.job_id || "",
  };
  const detailBlocks = [
    renderAuditReviewReasonList("缺少关键列", res.missing_required_columns),
    renderAuditReviewReasonList("认人字段空值", res.blank_identity_values),
  ].filter(Boolean);
  root.innerHTML = `
    <p class="hint" style="margin:6px 0;">主原因</p>
    <p style="margin:0 0 10px 0;">${escapeHtml(res.reason_label || "命中待复核规则，系统先放入待复核区")}</p>
    <p class="hint" style="margin:6px 0;">基础信息</p>
    ${toTable([basicInfo])}
    <p class="hint" style="margin:6px 0;">具体明细</p>
    ${detailBlocks.length ? detailBlocks.join("") : "<p>当前没有额外明细，系统先冻结等待人工处理。</p>"}
  `;
  renderAuditRiskMessage("待复核原因已生成。请继续决定“放入回收站”或“放入特殊情况区”。");
}

async function previewAuditFile(fileId) {
  clearAuditPreviewObjectUrl();
  const res = await api(`/admin/files/${encodeURIComponent(fileId)}/content?mode=preview`);
  if (res instanceof Response) {
    const blob = await res.blob();
    const ct = res.headers.get("content-type") || "";
    const url = URL.createObjectURL(blob);
    state.audit.previewObjectUrl = url;
    if (ct.includes("image")) {
      el("auditPreview").innerHTML = `<img src="${url}" style="max-width:100%;height:auto;border:1px solid #d7e0e7;border-radius:8px;" />`;
      return;
    }
    if (ct.includes("pdf")) {
      el("auditPreview").innerHTML = `<iframe src="${url}" style="width:100%;min-height:420px;border:1px solid #d7e0e7;border-radius:8px;"></iframe>`;
      return;
    }
    el("auditPreview").innerHTML = `<p>该类型预览能力有限，请下载查看。</p>`;
    return;
  }

  if (res.kind === "tabular") {
    el("auditPreview").innerHTML = `
      <p class="hint">表格预览（前 ${res.rows?.length || 0} 行）${res.truncated ? "，已截断" : ""}</p>
      ${toTable(res.rows || [])}
    `;
    return;
  }
  if (res.kind === "text") {
    el("auditPreview").innerHTML = `<pre>${(res.text || "").replace(/</g, "&lt;")}</pre>`;
    return;
  }
  el("auditPreview").innerHTML = `<p>${res.message || "暂不支持预览，请下载查看。"}</p>`;
}

async function downloadAuditFile(fileId) {
  const res = await api(`/admin/files/${encodeURIComponent(fileId)}/content?mode=download`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename=\"?([^\";]+)\"?/i);
  a.download = m ? m[1] : `${fileId}`;
  a.click();
  URL.revokeObjectURL(url);
}

function bindAuditInteractions() {
  const topJobRoot = el("jobList");
  if (topJobRoot) {
    topJobRoot.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-job-open]");
      if (!btn) return;
      const jobId = btn.getAttribute("data-job-open");
      const hintedLifecycle =
        (state.topLifecycleView && state.topLifecycleView !== "all" ? state.topLifecycleView : "") ||
        btn.getAttribute("data-job-auto-lifecycle") ||
        "";
      if (!jobId) return;
      openAuditJob(jobId, hintedLifecycle).catch((err) => alert(err.message));
    });
  }

  const jobRoot = el("auditJobsList");
  if (jobRoot) {
    jobRoot.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-job-open]");
      if (!btn) return;
      const jobId = btn.getAttribute("data-job-open");
      const hintedLifecycle = btn.getAttribute("data-job-auto-lifecycle") || "";
      if (!jobId) return;
      openAuditJob(jobId, hintedLifecycle).catch((err) => alert(err.message));
    });
  }

  const fileRoot = el("auditFilesList");
  if (fileRoot) {
    fileRoot.addEventListener("click", (e) => {
      const targetBtn = e.target.closest("[data-audit-target]");
      if (targetBtn) {
        const objectId = targetBtn.getAttribute("data-audit-target");
        const objectType = targetBtn.getAttribute("data-audit-target-type") || "file";
        const specialCaseSource = targetBtn.getAttribute("data-special-case-source") || "";
        if (objectId) {
          setAuditActionTarget(objectType, objectId, objectId, { specialCaseSource });
          renderAuditRiskMessage(`已选中${objectType === "record" ? "记录" : "文件"}目标：${objectId}`);
        }
        return;
      }
      const openBtn = e.target.closest("[data-file-open]");
      if (openBtn) {
        const fileId = openBtn.getAttribute("data-file-open");
        if (fileId) openAuditFile(fileId).catch((err) => alert(err.message));
        return;
      }
      const pvBtn = e.target.closest("[data-file-preview]");
      if (pvBtn) {
        const fileId = pvBtn.getAttribute("data-file-preview");
        if (fileId) previewAuditFile(fileId).catch((err) => alert(err.message));
        return;
      }
      const recordBtn = e.target.closest("[data-file-records]");
      if (recordBtn) {
        const fileId = recordBtn.getAttribute("data-file-records");
        if (fileId) previewAuditNormalizedRecords(fileId).catch((err) => alert(err.message));
        return;
      }
      const dlBtn = e.target.closest("[data-file-download]");
      if (dlBtn) {
        const fileId = dlBtn.getAttribute("data-file-download");
        if (fileId) downloadAuditFile(fileId).catch((err) => alert(err.message));
      }
    });
  }

  const previewRoot = el("auditPreview");
  if (previewRoot) {
    previewRoot.addEventListener("click", (e) => {
      const targetBtn = e.target.closest("[data-audit-target]");
      if (!targetBtn) return;
      const objectId = targetBtn.getAttribute("data-audit-target");
      const objectType = targetBtn.getAttribute("data-audit-target-type") || "record";
      const specialCaseSource = targetBtn.getAttribute("data-special-case-source") || "";
      if (!objectId) return;
      setAuditActionTarget(objectType, objectId, objectId, { specialCaseSource });
      renderAuditRiskMessage(`已选中${objectType === "record" ? "记录" : "文件"}目标：${objectId}`);
    });
  }

  const lifecycleRoot = el("auditLifecycleList");
  if (lifecycleRoot) {
    lifecycleRoot.addEventListener("click", (e) => {
      const openBtn = e.target.closest("[data-file-open]");
      if (openBtn) {
        const fileId = openBtn.getAttribute("data-file-open");
        if (fileId) openAuditFile(fileId).catch((err) => alert(err.message));
        return;
      }
      const previewBtn = e.target.closest("[data-file-preview]");
      if (previewBtn) {
        const fileId = previewBtn.getAttribute("data-file-preview");
        if (fileId) previewAuditFile(fileId).catch((err) => alert(err.message));
        return;
      }
      const recordBtn = e.target.closest("[data-review-records]");
      if (recordBtn) {
        const fileId = recordBtn.getAttribute("data-review-records");
        const recordId = recordBtn.getAttribute("data-review-record-id");
        const specialCaseSource = recordBtn.getAttribute("data-special-case-source") || "";
        if (fileId) {
          openAuditFile(fileId)
            .then(() => previewAuditNormalizedRecords(fileId))
            .then(() => {
              if (recordId) {
                setAuditActionTarget("record", recordId, recordId, { specialCaseSource });
                renderAuditRiskMessage(`已打开归一化记录，并选中记录目标：${recordId}`);
              }
            })
            .catch((err) => alert(err.message));
        }
        return;
      }
      const targetBtn = e.target.closest("[data-audit-target]");
      if (!targetBtn) return;
      const objectId = targetBtn.getAttribute("data-audit-target");
      const objectType = targetBtn.getAttribute("data-audit-target-type") || "file";
      const specialCaseSource = targetBtn.getAttribute("data-special-case-source") || "";
      if (!objectId) return;
      setAuditActionTarget(objectType, objectId, objectId, { specialCaseSource });
      renderAuditRiskMessage(`已选中${objectType === "record" ? "记录" : "文件"}目标：${objectId}`);
    });
  }
}

async function createJob() {
  const job = await api("/upload-jobs", { method: "POST" });
  el("jobIdInput").value = job.id;
  await refreshJobs();
}

async function refreshJobs() {
  try {
    const batchView = (el("jobListBatchView")?.value || state.topBatchView || "all").trim() || "all";
    const lifecycleView = (el("jobListLifecycleView")?.value || state.topLifecycleView || "current").trim() || "current";
    const businessView = (el("jobListBusinessView")?.value || state.topBusinessView || "all").trim() || "all";
    state.topBatchView = batchView;
    state.topLifecycleView = lifecycleView;
    state.topBusinessView = businessView;
    const query = new URLSearchParams({
      batch_view: batchView,
      lifecycle_view: lifecycleView,
      business_view: businessView,
    });
    const res = await api(`/upload-jobs/summary?${query.toString()}`);
    const items = (res && res.items) || [];
    const total = Number.parseInt(`${res && res.total}`, 10);
    const autoDeletedTotal = sumAutoDeletedDuplicateCount(items);
    const title = `当前批次汇总：${Number.isNaN(total) ? items.length : total} 条。批次视角：${batchViewLabel(res?.batch_view || batchView)}；区域视角：${jobLifecycleViewLabel(res?.lifecycle_view || lifecycleView)}；业务视角：${jobBusinessViewLabel(res?.business_view || businessView)}。自动清理总数：${autoDeletedTotal}。此处已与审计后台使用同一批次汇总口径。`;
    if (el("jobListMeta")) el("jobListMeta").innerHTML = escapeHtml(title);
    el("jobList").innerHTML = renderJobSummaryTable(items);
  } catch (err) {
    const text = err && err.message ? err.message : String(err);
    renderJobListMessage(`批次汇总加载失败：${text}`, "error");
  }
}

function refreshConnectionBoundViews() {
  refreshJobs();
  if (el("btnAuditRefresh")) {
    refreshAuditJobs();
  }
  refreshOperationsSummary().catch(() => {});
}

async function uploadFile() {
  const jobId = el("jobIdInput").value.trim();
  if (!jobId) {
    alert("请先创建 job_id");
    return;
  }

  const files = Array.from(el("fileInput")?.files || []);
  if (!files.length) {
    alert("请至少选择一个文件");
    return;
  }

  const docType = (el("docType")?.value || "").trim();
  if (!docType) {
    alert("请先选择单据类型后再上传。");
    return;
  }
  const validation = validateUploadFilesByDocType(files, docType);
  if (!validation.ok) {
    alert(validation.message);
    return;
  }
  const metaRaw = el("metadataJson").value.trim();
  const items = [];

  for (const file of files) {
    const fd = new FormData();
    fd.append("document_type", docType);
    fd.append("upload", file);
    if (metaRaw) fd.append("metadata_json", metaRaw);

    try {
      const res = await api(`/upload-jobs/${jobId}/files`, { method: "POST", body: fd });
      items.push({
        文件名: file.name,
        状态: "成功",
        文件ID: res?.file_id || "",
        解析状态: formatDisplayValue("parse_status", res?.parse_status || ""),
      });
    } catch (err) {
      items.push({
        文件名: file.name,
        状态: "失败",
        错误信息: err && err.message ? err.message : String(err),
      });
    }
  }

  const successCount = items.filter((x) => x.状态 === "成功").length;
  const capability = uploadDocCapability(docType);
  const summary = {
    批次ID: jobId,
    单据类型: documentTypeLabel(docType),
    推荐格式: capability ? capability.recommendedText : "",
    允许格式: capability ? capability.allowedText : "",
    文件总数: files.length,
    成功数: successCount,
    失败数: files.length - successCount,
    明细: items,
  };
  el("uploadResult").textContent = JSON.stringify(summary, null, 2);
}

async function startTask() {
  const jobId = el("jobIdInput").value.trim();
  if (!jobId) {
    alert("请先设置 job_id");
    return;
  }
  await api("/tasks/lobster-feed", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
  await refreshTasks();
}

async function refreshTasks() {
  const jobId = el("jobIdInput").value.trim();
  const tasks = await api(`/tasks${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ""}`);
  el("taskList").innerHTML = toTable(tasks);
}

async function refreshAlerts() {
  const jobId = el("jobIdInput").value.trim();
  if (!jobId) return;
  const alerts = await api(`/alerts?job_id=${encodeURIComponent(jobId)}`);
  el("alertsList").innerHTML = renderAlertsTable(alerts);
}

async function refreshSummary() {
  const jobId = el("jobIdInput").value.trim();
  if (!jobId) return;
  const items = await api(`/alerts/customer-summary?job_id=${encodeURIComponent(jobId)}`);
  el("summaryList").innerHTML = toTable(items);
}

async function refreshOperationsSummary() {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  try {
    const payload = await api("/admin/operations/summary");
    el("operationsSummary").innerHTML = renderOperationsSummary(payload);
  } catch (err) {
    const text = err && err.message ? err.message : String(err);
    renderOperationsMessage(`运营状态加载失败：${text}`, "error");
  }
}

async function runOperationsBackup(kind) {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  const isDatabase = kind === "database";
  const actionLabel = isDatabase ? "数据库备份" : "上传文件备份";
  renderOperationsMessage(`${actionLabel}执行中，请稍等...`);
  try {
    const payload = await api(`/admin/operations/backup/${isDatabase ? "database" : "files"}/run`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshOperationsSummary();
    renderOperationsMessage(`${actionLabel}完成：${payload.snapshot_label || "已生成备份文件"}`);
  } catch (err) {
    const text = err && err.message ? err.message : String(err);
    renderOperationsMessage(`${actionLabel}失败：${text}`, "error");
  }
}

async function runOperationsLogCleanup() {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  renderOperationsMessage("日志清理执行中，请稍等...");
  try {
    const payload = await api("/admin/operations/logs/cleanup/run", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshOperationsSummary();
    renderOperationsMessage(
      `日志清理完成：删掉 ${payload.removed_file_count ?? 0} 个，剩余 ${payload.remaining_file_count ?? 0} 个。`
    );
  } catch (err) {
    const text = err && err.message ? err.message : String(err);
    renderOperationsMessage(`日志清理失败：${text}`, "error");
  }
}

async function runRestoreDrill() {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  renderOperationsMessage("恢复演练执行中，请稍等...");
  try {
    const payload = await api("/admin/operations/restore-drill/run", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshOperationsSummary();
    renderOperationsMessage(
      `恢复演练完成：批次 ${payload.restored_job_count ?? 0} / 归一化记录 ${payload.restored_record_count ?? 0} / 原始文件 ${payload.restored_storage_file_count ?? 0}。`
    );
  } catch (err) {
    const text = err && err.message ? err.message : String(err);
    renderOperationsMessage(`恢复演练失败：${text}`, "error");
  }
}

async function setArchiveMode(mode) {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  const normalizedMode = String(mode || "").trim().toLowerCase() === "manual" ? "manual" : "auto";
  const current = await api("/config/operations_monitoring_policy");
  const nextValue = {
    ...(current.value || {}),
    archive_mode: normalizedMode,
  };
  latestArchivePreviewToken = "";
  await api("/config/operations_monitoring_policy", {
    method: "PUT",
    body: JSON.stringify({ value: nextValue }),
  });
  await refreshOperationsSummary();
  alert(
    normalizedMode === "manual"
      ? "已切到手动挡，后续请先查看手动归档候选，再执行手动归档。"
      : "已切到自动挡，后续新命中候选会自动归档。"
  );
}

async function runArchivePreview() {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  const payload = await api("/admin/operations/archive/preview", {
    method: "POST",
    body: JSON.stringify({}),
  });
  latestArchivePreviewToken = String(payload.preview_token || "").trim();
  await refreshOperationsSummary();
}

async function runArchiveExecute() {
  if (getRole() !== "admin") {
    renderOperationsMessage("仅管理员可用：请先将上方角色切换为管理员。");
    return;
  }
  if (!latestArchivePreviewToken) {
    renderOperationsMessage("执行手动归档前，请先点一次“查看手动归档候选”。", "error");
    return;
  }
  await api("/admin/operations/archive/run", {
    method: "POST",
    body: JSON.stringify({ preview_token: latestArchivePreviewToken }),
  });
  latestArchivePreviewToken = "";
  await refreshOperationsSummary();
}

async function download(kind) {
  const jobId = el("jobIdInput").value.trim();
  if (!jobId) return;
  const res = await api("/exports", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId, kind }),
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${kind}-${jobId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

async function loadConfig(key, targetId) {
  const res = await api(`/config/${key}`);
  el(targetId).value = JSON.stringify(res.value, null, 2);
}

async function saveConfig(key, targetId) {
  const parsed = JSON.parse(el(targetId).value || "{}");
  await api(`/config/${key}`, {
    method: "PUT",
    body: JSON.stringify({ value: parsed }),
  });
  alert(`${key} 已保存`);
}

function reminderAlertTypeLabel(alertType) {
  return alertType === "ship_after_no_finance" ? "超60天没开票" : "该发没发";
}

function reminderStateLabel(isEnabled) {
  return isEnabled ? "启用中" : "已关闭";
}

function trimSimpleNumber(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "";
  if (Number.isInteger(number)) return String(number);
  return number.toFixed(2).replace(/\.?0+$/, "");
}

function formatReminderAmount(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number) || number <= 0) return "暂无";
  if (Math.abs(number) >= 10000) return `${trimSimpleNumber(number / 10000)}万`;
  return trimSimpleNumber(number);
}

function formatCustomerOverviewAmount(value, hasMissingAmount = false) {
  const base = formatReminderAmount(value);
  if (hasMissingAmount) {
    return base === "暂无" ? "部分金额暂缺" : `${base}（部分金额暂缺）`;
  }
  return base;
}

function viewerAccountRoleLabel(role) {
  return role === "viewer_boss" ? "老板娘查看账号" : "姚建锋查看账号";
}

function renderViewerAccountHint(text) {
  if (el("viewerAccountHint")) {
    el("viewerAccountHint").textContent = text;
  }
}

function renderViewerReminderHint(text) {
  if (el("viewerReminderHint")) {
    el("viewerReminderHint").textContent = text;
  }
}

function renderCustomerOverviewHint(text) {
  if (el("customerOverviewHint")) {
    el("customerOverviewHint").textContent = text;
  }
}

function renderViewerAccounts(items) {
  const root = el("viewerAccountsList");
  if (!root) return;
  if (!items || !items.length) {
    root.innerHTML = '<p class="hint viewer-account-empty">还没有员工账号。</p>';
    return;
  }

  root.innerHTML = `
    <div class="viewer-account-table-wrap">
      <table class="viewer-account-table">
        <thead>
          <tr>
            <th>显示名称</th>
            <th>手机号</th>
            <th>角色</th>
            <th>启用状态</th>
            <th>最近登录</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map(
              (item) => `
                <tr data-account-id="${escapeHtml(item.id)}">
                  <td><input data-field="display_name" value="${escapeHtml(item.display_name || "")}" /></td>
                  <td><input data-field="phone" inputmode="numeric" value="${escapeHtml(item.phone || "")}" /></td>
                  <td>
                    <select data-field="role">
                      <option value="viewer_yao" ${item.role === "viewer_yao" ? "selected" : ""}>姚建锋查看账号</option>
                      <option value="viewer_boss" ${item.role === "viewer_boss" ? "selected" : ""}>老板娘查看账号</option>
                    </select>
                  </td>
                  <td>
                    <label class="viewer-account-status">
                      <input data-field="is_active" type="checkbox" ${item.is_active ? "checked" : ""} />
                      ${item.is_active ? "启用中" : "已停用"}
                    </label>
                  </td>
                  <td class="viewer-account-last-login">${item.last_login_at ? escapeHtml(fmtTime(item.last_login_at)) : "未登录"}</td>
                  <td>
                    <div class="viewer-account-actions">
                      <button data-action="save-account">保存</button>
                      <button data-action="reset-password">重置密码</button>
                    </div>
                  </td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderViewerReminderSettings(items) {
  const root = el("viewerReminderSettingsList");
  if (!root) return;
  if (!items || !items.length) {
    root.innerHTML = '<p class="hint viewer-setting-empty">当前还没有可管理的客户提醒开关。</p>';
    return;
  }

  root.innerHTML = `
    <div class="viewer-setting-table-wrap">
      <table class="viewer-setting-table">
        <thead>
          <tr>
            <th>客户</th>
            <th>提醒类型</th>
            <th>当前影响</th>
            <th>当前状态</th>
            <th>最近一次操作</th>
            <th>原因</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map(
              (item) => `
                <tr data-customer="${escapeHtml(item.customer || "")}" data-alert-type="${escapeHtml(item.alert_type || "")}">
                  <td>${escapeHtml(item.customer || "")}</td>
                  <td>${escapeHtml(reminderAlertTypeLabel(item.alert_type))}</td>
                  <td>
                    <div class="viewer-setting-badges">
                      <span class="viewer-setting-badge">当前 ${escapeHtml(String(item.open_alert_count ?? 0))} 条</span>
                      <span class="viewer-setting-badge">已解除 ${escapeHtml(String(item.resolved_alert_count ?? 0))} 条</span>
                      ${
                        item.alert_type === "ship_after_no_finance"
                          ? `<span class="viewer-setting-badge">${escapeHtml(formatReminderAmount(item.known_amount_total))}</span>`
                          : ""
                      }
                      ${item.has_missing_amount ? '<span class="viewer-setting-badge is-warn">部分金额暂缺</span>' : ""}
                    </div>
                  </td>
                  <td>
                    <label class="viewer-setting-state">
                      <select data-field="enabled">
                        <option value="true" ${item.is_enabled ? "selected" : ""}>启用中</option>
                        <option value="false" ${item.is_enabled ? "" : "selected"}>已关闭</option>
                      </select>
                    </label>
                  </td>
                  <td>
                    <div>${escapeHtml(item.last_operator_name || "未操作")}</div>
                    <div class="hint">${item.last_changed_at ? escapeHtml(fmtTime(item.last_changed_at)) : "暂无时间"}</div>
                    <div class="hint">${escapeHtml(item.last_reason || "暂无原因")}</div>
                  </td>
                  <td>
                    <input data-field="reason" value="${escapeHtml(item.last_reason || "")}" placeholder="这次为什么要改" />
                  </td>
                  <td>
                    <div class="viewer-setting-actions">
                      <button data-action="save-reminder-setting">保存</button>
                    </div>
                  </td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderViewerReminderLogs(logs) {
  const root = el("viewerReminderLogs");
  if (!root) return;
  if (!logs || !logs.length) {
    root.innerHTML = '<p class="hint viewer-setting-empty">还没有客户提醒开关操作记录。</p>';
    return;
  }

  root.innerHTML = `
    <div class="viewer-setting-log-wrap">
      <table class="viewer-setting-log-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>客户</th>
            <th>提醒类型</th>
            <th>状态</th>
            <th>操作人</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          ${logs
            .map(
              (item) => `
                <tr>
                  <td>${escapeHtml(fmtTime(item.created_at) || "")}</td>
                  <td>${escapeHtml(item.customer || "")}</td>
                  <td>${escapeHtml(reminderAlertTypeLabel(item.alert_type))}</td>
                  <td>${escapeHtml(reminderStateLabel(Boolean(item.is_enabled)))}</td>
                  <td>${escapeHtml(item.operator_name || "")}</td>
                  <td>${escapeHtml(item.reason || "")}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCustomerOverviewList(items) {
  const root = el("customerOverviewList");
  if (!root) return;
  if (!items || !items.length) {
    root.innerHTML = '<p class="hint customer-overview-empty">当前没有命中的客户。</p>';
    return;
  }
  root.innerHTML = `
    <div class="customer-overview-table-wrap">
      <table class="customer-overview-table">
        <thead>
          <tr>
            <th>客户</th>
            <th>当前未发货</th>
            <th>当前未开票</th>
            <th>未开票总额</th>
            <th>最久已拖 / 已超</th>
            <th>涉及批次 / 文件 / 记录</th>
            <th>最近变化</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map((item) => {
              const selected = (state.customerOverview.selectedCustomer || "") === (item.customer || "");
              const overdueSummary = [
                item.unshipped_overdue_max_days ? `未发货 ${item.unshipped_overdue_max_days} 天` : "",
                item.uninvoiced_overdue_max_days ? `未开票 ${item.uninvoiced_overdue_max_days} 天` : "",
              ]
                .filter(Boolean)
                .join(" / ");
              return `
                <tr class="${selected ? "is-selected" : ""}">
                  <td>${escapeHtml(item.customer || "")}</td>
                  <td>${escapeHtml(String(item.open_unshipped_count ?? 0))} 条</td>
                  <td>${escapeHtml(String(item.open_uninvoiced_count ?? 0))} 条</td>
                  <td>${escapeHtml(formatCustomerOverviewAmount(item.known_uninvoiced_amount_total, item.has_missing_amount))}</td>
                  <td>${escapeHtml(overdueSummary || "暂无")}</td>
                  <td>${escapeHtml(`${item.job_count ?? 0} / ${item.file_count ?? 0} / ${item.record_count ?? 0}`)}</td>
                  <td>${escapeHtml(fmtTime(item.latest_changed_at) || "暂无")}</td>
                  <td><button data-customer-overview-open="${escapeHtml(item.customer || "")}">查看详情</button></td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCustomerOverviewSummaryCards(summary) {
  const cards = [
    { label: "客户", value: summary.customer || "未知客户" },
    { label: "当前未发货", value: `${summary.open_unshipped_count ?? 0} 条` },
    { label: "当前未开票", value: `${summary.open_uninvoiced_count ?? 0} 条` },
    { label: "未开票总额", value: formatCustomerOverviewAmount(summary.known_uninvoiced_amount_total, summary.has_missing_amount) },
    {
      label: "最久已拖",
      value: summary.unshipped_overdue_max_days ? `${summary.unshipped_overdue_max_days} 天` : "暂无",
    },
    {
      label: "最久已超",
      value: summary.uninvoiced_overdue_max_days ? `${summary.uninvoiced_overdue_max_days} 天` : "暂无",
    },
    { label: "涉及批次", value: `${summary.job_count ?? 0}` },
    { label: "涉及文件 / 记录", value: `${summary.file_count ?? 0} / ${summary.record_count ?? 0}` },
  ];
  return `
    <div class="customer-overview-summary-grid">
      ${cards
        .map(
          (card) => `
            <div class="customer-overview-summary-card">
              <div class="label">${escapeHtml(card.label)}</div>
              <div class="value">${escapeHtml(card.value)}</div>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderCustomerOverviewDetailTable(items, kind) {
  if (!items || !items.length) {
    return `<p class="hint customer-overview-empty">当前没有${kind === "unshipped" ? "未发货" : "未开票"}明细。</p>`;
  }
  const rows = items
    .map((item) => {
      const metric =
        kind === "unshipped"
          ? [
              item.order_unshipped_qty !== null && item.order_unshipped_qty !== undefined
                ? `还差 ${trimSimpleNumber(item.order_unshipped_qty)}`
                : "",
              item.days_until_due !== null && item.days_until_due !== undefined
                ? item.days_until_due < 0
                  ? `已拖 ${Math.abs(item.days_until_due)} 天`
                  : `还有 ${item.days_until_due} 天`
                : "",
            ]
              .filter(Boolean)
              .join(" / ")
          : [
              item.uninvoiced_qty !== null && item.uninvoiced_qty !== undefined
                ? `未开 ${trimSimpleNumber(item.uninvoiced_qty)}`
                : "",
              item.known_amount !== null && item.known_amount !== undefined
                ? `金额 ${formatReminderAmount(item.known_amount)}`
                : "金额暂缺",
              item.overdue_days !== null && item.overdue_days !== undefined ? `已超 ${item.overdue_days} 天` : "",
            ]
              .filter(Boolean)
              .join(" / ");
      return `
        <tr>
          <td>${escapeHtml(item.customer_order_no || "")}</td>
          <td>${escapeHtml(item.item_name || "")}</td>
          <td>${escapeHtml(item.item_code || "")}</td>
          <td>${escapeHtml(item.severity_label || "")}</td>
          <td>${escapeHtml(metric || "暂无")}</td>
          <td>${escapeHtml(item.message || "")}</td>
          <td>${escapeHtml(item.job_id || "")}</td>
          <td><button
            data-customer-overview-audit="1"
            data-job-id="${escapeHtml(item.job_id || "")}"
            data-file-id="${escapeHtml(item.file_id || "")}"
            data-record-id="${escapeHtml(item.record_id || "")}"
            data-order-no="${escapeHtml(item.customer_order_no || "")}"
          >打开审计</button></td>
        </tr>
      `;
    })
    .join("");
  return `
    <div class="customer-overview-detail-table-wrap">
      <table class="customer-overview-detail-table">
        <thead>
          <tr>
            <th>单据编号</th>
            <th>品名</th>
            <th>商品编码</th>
            <th>级别</th>
            <th>关键数字</th>
            <th>说明</th>
            <th>批次ID</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderCustomerOverviewDetail(payload) {
  const root = el("customerOverviewDetail");
  if (!root) return;
  if (!payload || !payload.summary) {
    root.innerHTML = '<p class="hint customer-overview-empty">请先从上面选一个客户。</p>';
    return;
  }
  root.innerHTML = `
    ${renderCustomerOverviewSummaryCards(payload.summary)}
    <div class="customer-overview-detail-block">
      <h3>当前未发货明细</h3>
      ${renderCustomerOverviewDetailTable(payload.unshipped_items || [], "unshipped")}
    </div>
    <div class="customer-overview-detail-block">
      <h3>当前未开票明细</h3>
      ${renderCustomerOverviewDetailTable(payload.uninvoiced_items || [], "uninvoiced")}
    </div>
  `;
}

async function refreshViewerAccounts() {
  if (!el("viewerAccountsList")) return;
  if (getRole() !== "admin") {
    renderViewerAccountHint("当前是上传员，切到管理员后才能维护员工账号。");
    el("viewerAccountsList").innerHTML = '<p class="hint viewer-account-empty">仅管理员可用。</p>';
    return;
  }
  const items = await api("/admin/viewer-accounts");
  renderViewerAccountHint(`当前共 ${items.length} 个员工账号。`);
  renderViewerAccounts(items);
}

async function refreshViewerReminderSettings() {
  if (!el("viewerReminderSettingsList")) return;
  if (getRole() !== "admin") {
    renderViewerReminderHint("当前是上传员，切到管理员后才能维护客户提醒开关。");
    el("viewerReminderSettingsList").innerHTML = '<p class="hint viewer-setting-empty">仅管理员可用。</p>';
    if (el("viewerReminderLogs")) {
      el("viewerReminderLogs").innerHTML = '<p class="hint viewer-setting-empty">仅管理员可用。</p>';
    }
    return;
  }
  const payload = await api("/admin/viewer-reminder-settings");
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const logs = Array.isArray(payload?.logs) ? payload.logs : [];
  renderViewerReminderHint(`当前共 ${items.length} 条“客户 + 提醒类型”开关。`);
  renderViewerReminderSettings(items);
  renderViewerReminderLogs(logs);
}

async function refreshCustomerOverviewCustomers() {
  if (!el("customerOverviewList")) return;
  if (getRole() !== "admin") {
    renderCustomerOverviewHint("当前是上传员，切到管理员后才能查看客户总览。");
    el("customerOverviewList").innerHTML = '<p class="hint customer-overview-empty">仅管理员可用。</p>';
    el("customerOverviewDetail").innerHTML = "";
    return;
  }
  const keyword = el("customerOverviewKeyword")?.value?.trim() || "";
  state.customerOverview.keyword = keyword;
  const query = new URLSearchParams();
  if (keyword) query.set("keyword", keyword);
  const items = await api(`/admin/customer-overview/customers?${query.toString()}`);
  renderCustomerOverviewHint(
    keyword
      ? `当前命中 ${items.length} 个客户。点“查看详情”可继续看未发货 / 未开票并下钻审计。`
      : `当前展示最需要关注的前 ${items.length} 个客户。`
  );
  renderCustomerOverviewList(Array.isArray(items) ? items : []);
  if (state.customerOverview.selectedCustomer) {
    const stillExists = Array.isArray(items)
      ? items.some((item) => (item.customer || "") === state.customerOverview.selectedCustomer)
      : false;
    if (!stillExists) {
      state.customerOverview.selectedCustomer = "";
      renderCustomerOverviewDetail(null);
    }
  }
}

async function openCustomerOverviewDetail(customer) {
  if (getRole() !== "admin") {
    alert("仅管理员可查看客户总览。");
    return;
  }
  const customerName = String(customer || "").trim();
  if (!customerName) return;
  const payload = await api(`/admin/customer-overview/detail?customer=${encodeURIComponent(customerName)}`);
  state.customerOverview.selectedCustomer = payload?.customer || customerName;
  renderCustomerOverviewList(await api(`/admin/customer-overview/customers?${new URLSearchParams(state.customerOverview.keyword ? { keyword: state.customerOverview.keyword } : {}).toString()}`));
  renderCustomerOverviewDetail(payload);
  renderCustomerOverviewHint(`当前查看：${state.customerOverview.selectedCustomer}。需要继续查时，可点表格里的“打开审计”。`);
}

async function openCustomerOverviewAudit(button) {
  const jobId = button?.dataset?.jobId || "";
  const fileId = button?.dataset?.fileId || "";
  const recordId = button?.dataset?.recordId || "";
  const orderNo = button?.dataset?.orderNo || "";
  if (!jobId) {
    alert("这条明细暂时缺少批次ID，当前没法直接跳审计。");
    return;
  }
  await openAuditJob(jobId, "current");
  if (fileId) {
    await previewAuditNormalizedRecords(fileId);
  }
  if (recordId) {
    setAuditActionTarget("record", recordId, recordId);
    renderAuditRiskMessage(`已从客户总览定位到记录：${orderNo || recordId}`);
  } else {
    renderAuditRiskMessage(`已从客户总览定位到批次：${jobId}`);
  }
  const auditControlDock = el("auditControlDock");
  if (auditControlDock && typeof auditControlDock.scrollIntoView === "function") {
    auditControlDock.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function bindCustomerOverviewInteractions() {
  const listRoot = el("customerOverviewList");
  if (listRoot && listRoot.dataset.bound !== "1") {
    listRoot.dataset.bound = "1";
    listRoot.addEventListener("click", (event) => {
      const button = event.target.closest("[data-customer-overview-open]");
      if (!button) return;
      const customer = button.getAttribute("data-customer-overview-open");
      if (!customer) return;
      openCustomerOverviewDetail(customer).catch((e) => alert(e.message));
    });
  }
  const detailRoot = el("customerOverviewDetail");
  if (detailRoot && detailRoot.dataset.bound !== "1") {
    detailRoot.dataset.bound = "1";
    detailRoot.addEventListener("click", (event) => {
      const button = event.target.closest("[data-customer-overview-audit]");
      if (!button) return;
      openCustomerOverviewAudit(button).catch((e) => alert(e.message));
    });
  }
}

async function createViewerAccount() {
  if (getRole() !== "admin") {
    alert("仅管理员可创建员工账号。");
    return;
  }
  const displayName = el("viewerAccountDisplayName")?.value?.trim() || "";
  const phone = el("viewerAccountPhone")?.value?.trim() || "";
  const role = el("viewerAccountRole")?.value || "viewer_yao";
  const password = el("viewerAccountPassword")?.value || "";
  if (!displayName || !phone || !password) {
    alert("请先填完整：显示名称、手机号、初始密码。");
    return;
  }
  await api("/admin/viewer-accounts", {
    method: "POST",
    body: JSON.stringify({
      display_name: displayName,
      phone,
      role,
      password,
    }),
  });
  el("viewerAccountDisplayName").value = "";
  el("viewerAccountPhone").value = "";
  el("viewerAccountPassword").value = "";
  el("viewerAccountRole").value = "viewer_yao";
  await refreshViewerAccounts();
  alert(`已创建 ${viewerAccountRoleLabel(role)}。`);
}

function readViewerReminderRow(button) {
  const row = button?.closest?.("tr[data-customer]");
  if (!row) return null;
  return {
    customer: row.dataset.customer || "",
    alertType: row.dataset.alertType || "",
    enabled: (row.querySelector('[data-field="enabled"]')?.value || "true") === "true",
    reason: row.querySelector('[data-field="reason"]')?.value?.trim() || "",
    operatorName: el("viewerSettingOperatorName")?.value?.trim() || "",
  };
}

function readViewerAccountRow(button) {
  const row = button?.closest?.("tr[data-account-id]");
  if (!row) return null;
  const accountId = row.dataset.accountId || "";
  const displayName = row.querySelector('[data-field="display_name"]')?.value?.trim() || "";
  const phone = row.querySelector('[data-field="phone"]')?.value?.trim() || "";
  const role = row.querySelector('[data-field="role"]')?.value || "viewer_yao";
  const isActive = Boolean(row.querySelector('[data-field="is_active"]')?.checked);
  return {
    accountId,
    payload: {
      display_name: displayName,
      phone,
      role,
      is_active: isActive,
    },
  };
}

async function saveViewerAccount(button) {
  const row = readViewerAccountRow(button);
  if (!row || !row.accountId) return;
  await api(`/admin/viewer-accounts/${encodeURIComponent(row.accountId)}`, {
    method: "PATCH",
    body: JSON.stringify(row.payload),
  });
  await refreshViewerAccounts();
  alert("员工账号已保存。");
}

async function resetViewerAccountPassword(button) {
  const row = readViewerAccountRow(button);
  if (!row || !row.accountId) return;
  const nextPassword = window.prompt("请输入新密码（至少 6 位）：", "");
  if (!nextPassword) return;
  await api(`/admin/viewer-accounts/${encodeURIComponent(row.accountId)}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ password: nextPassword }),
  });
  alert("密码已重置，旧登录会自动失效。");
}

async function saveViewerReminderSetting(button) {
  if (getRole() !== "admin") {
    alert("仅管理员可改客户提醒开关。");
    return;
  }
  const row = readViewerReminderRow(button);
  if (!row) return;
  if (!row.reason) {
    alert("请先写这次变更原因。");
    return;
  }
  if (!row.operatorName) {
    alert("请先写操作人。");
    return;
  }
  await api("/admin/viewer-reminder-settings", {
    method: "PUT",
    body: JSON.stringify({
      customer: row.customer,
      alert_type: row.alertType,
      enabled: row.enabled,
      reason: row.reason,
      operator_name: row.operatorName,
    }),
  });
  await refreshViewerReminderSettings();
  alert(`客户提醒开关已更新为：${row.enabled ? "启用中" : "已关闭"}。`);
}

function bindViewerAccountInteractions() {
  const root = el("viewerAccountsList");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "save-account") {
      saveViewerAccount(button).catch((e) => alert(e.message));
    }
    if (action === "reset-password") {
      resetViewerAccountPassword(button).catch((e) => alert(e.message));
    }
  });
}

function bindViewerReminderInteractions() {
  const root = el("viewerReminderSettingsList");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "save-reminder-setting") {
      saveViewerReminderSetting(button).catch((e) => alert(e.message));
    }
  });
}

initBackendBaseInput();
restoreRoleSelection();
renderAuditRoleHint();
ensureAuditDeleteControls();
bindUploadDocTypeBehavior();
bindViewerAccountInteractions();
bindViewerReminderInteractions();
bindCustomerOverviewInteractions();

el("btnCreateJob").addEventListener("click", () => createJob().catch((e) => alert(e.message)));
el("btnUpload").addEventListener("click", () => uploadFile().catch((e) => alert(e.message)));
el("btnStartTask").addEventListener("click", () => startTask().catch((e) => alert(e.message)));
el("btnRefreshTasks").addEventListener("click", () => refreshTasks().catch((e) => alert(e.message)));
el("btnRefreshAlerts").addEventListener("click", () => refreshAlerts().catch((e) => alert(e.message)));
el("btnSummary").addEventListener("click", () => refreshSummary().catch((e) => alert(e.message)));
el("btnExportAlerts").addEventListener("click", () => download("alerts").catch((e) => alert(e.message)));
el("btnExportSummary").addEventListener("click", () => download("customer-summary").catch((e) => alert(e.message)));
el("btnLoadRule").addEventListener("click", () => loadConfig("rule_parameters", "ruleConfig").catch((e) => alert(e.message)));
el("btnSaveRule").addEventListener("click", () => saveConfig("rule_parameters", "ruleConfig").catch((e) => alert(e.message)));
el("btnLoadMatch").addEventListener("click", () => loadConfig("match_template", "matchConfig").catch((e) => alert(e.message)));
el("btnSaveMatch").addEventListener("click", () => saveConfig("match_template", "matchConfig").catch((e) => alert(e.message)));
if (el("btnViewerAccountCreate")) {
  el("btnViewerAccountCreate").addEventListener("click", () => createViewerAccount().catch((e) => alert(e.message)));
}
if (el("btnViewerAccountsRefresh")) {
  el("btnViewerAccountsRefresh").addEventListener("click", () => refreshViewerAccounts().catch((e) => alert(e.message)));
}
if (el("btnViewerReminderRefresh")) {
  el("btnViewerReminderRefresh").addEventListener("click", () =>
    refreshViewerReminderSettings().catch((e) => alert(e.message))
  );
}
if (el("btnCustomerOverviewSearch")) {
  el("btnCustomerOverviewSearch").addEventListener("click", () =>
    refreshCustomerOverviewCustomers().catch((e) => alert(e.message))
  );
}
if (el("btnCustomerOverviewClear")) {
  el("btnCustomerOverviewClear").addEventListener("click", () => {
    if (el("customerOverviewKeyword")) el("customerOverviewKeyword").value = "";
    state.customerOverview.keyword = "";
    state.customerOverview.selectedCustomer = "";
    renderCustomerOverviewDetail(null);
    refreshCustomerOverviewCustomers().catch((e) => alert(e.message));
  });
}
if (el("customerOverviewKeyword")) {
  el("customerOverviewKeyword").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      refreshCustomerOverviewCustomers().catch((e) => alert(e.message));
    }
  });
}
if (el("btnLoadRetention")) {
  el("btnLoadRetention").addEventListener("click", () =>
    loadConfig("data_retention_policy", "retentionConfig").catch((e) => alert(e.message))
  );
}
if (el("btnSaveRetention")) {
  el("btnSaveRetention").addEventListener("click", () =>
    saveConfig("data_retention_policy", "retentionConfig").catch((e) => alert(e.message))
  );
}
if (el("btnLoadOperationsPolicy")) {
  el("btnLoadOperationsPolicy").addEventListener("click", () =>
    loadConfig("operations_monitoring_policy", "operationsPolicyConfig").catch((e) => alert(e.message))
  );
}
if (el("btnSaveOperationsPolicy")) {
  el("btnSaveOperationsPolicy").addEventListener("click", () =>
    saveConfig("operations_monitoring_policy", "operationsPolicyConfig").catch((e) => alert(e.message))
  );
}
if (el("btnLoadOperationsRuntime")) {
  el("btnLoadOperationsRuntime").addEventListener("click", () =>
    loadConfig("operations_runtime_status", "operationsRuntimeConfig").catch((e) => alert(e.message))
  );
}
if (el("btnSaveOperationsRuntime")) {
  el("btnSaveOperationsRuntime").addEventListener("click", () =>
    saveConfig("operations_runtime_status", "operationsRuntimeConfig").catch((e) => alert(e.message))
  );
}
if (el("btnRefreshOperations")) {
  el("btnRefreshOperations").addEventListener("click", () => refreshOperationsSummary().catch((e) => alert(e.message)));
}
if (el("btnRunDatabaseBackup")) {
  el("btnRunDatabaseBackup").addEventListener("click", () => runOperationsBackup("database").catch((e) => alert(e.message)));
}
if (el("btnRunFileBackup")) {
  el("btnRunFileBackup").addEventListener("click", () => runOperationsBackup("files").catch((e) => alert(e.message)));
}
if (el("btnRunLogCleanup")) {
  el("btnRunLogCleanup").addEventListener("click", () => runOperationsLogCleanup().catch((e) => alert(e.message)));
}
if (el("btnRunRestoreDrill")) {
  el("btnRunRestoreDrill").addEventListener("click", () => runRestoreDrill().catch((e) => alert(e.message)));
}
if (el("btnArchiveModeAuto")) {
  el("btnArchiveModeAuto").addEventListener("click", () => setArchiveMode("auto").catch((e) => alert(e.message)));
}
if (el("btnArchiveModeManual")) {
  el("btnArchiveModeManual").addEventListener("click", () => setArchiveMode("manual").catch((e) => alert(e.message)));
}
if (el("btnArchivePreview")) {
  el("btnArchivePreview").addEventListener("click", () => runArchivePreview().catch((e) => alert(e.message)));
}
if (el("btnArchiveExecute")) {
  el("btnArchiveExecute").addEventListener("click", () => runArchiveExecute().catch((e) => alert(e.message)));
}
if (el("btnOpenArchiveLookup")) {
  el("btnOpenArchiveLookup").addEventListener("click", () => openArchiveLookupEntry().catch((e) => alert(e.message)));
}
if (el("roleSelect")) {
  el("roleSelect").addEventListener("change", () => {
    persistRoleSelection();
    renderAuditRoleHint();
    if (el("btnAuditRefresh")) {
      refreshAuditJobs();
    }
    refreshOperationsSummary().catch(() => {});
    refreshViewerAccounts().catch(() => {});
    refreshViewerReminderSettings().catch(() => {});
  });
}
if (el("backendBase")) {
  el("backendBase").addEventListener("change", () => {
    refreshConnectionBoundViews();
    refreshViewerAccounts().catch(() => {});
    refreshViewerReminderSettings().catch(() => {});
  });
}
if (el("jobListBatchView")) {
  el("jobListBatchView").addEventListener("change", () => refreshJobs().catch((e) => alert(e.message)));
}
if (el("jobListLifecycleView")) {
  el("jobListLifecycleView").addEventListener("change", () => refreshJobs().catch((e) => alert(e.message)));
}
if (el("jobListBusinessView")) {
  el("jobListBusinessView").addEventListener("change", () => refreshJobs().catch((e) => alert(e.message)));
}
if (el("btnRefreshJobs")) {
  el("btnRefreshJobs").addEventListener("click", () => refreshJobs());
}
if (el("auditBatchView")) {
  el("auditBatchView").addEventListener("change", () => refreshAuditJobs().catch((e) => alert(e.message)));
}

refreshJobs().catch(() => {});
bindAuditInteractions();
if (el("btnAuditRefresh")) {
  el("btnAuditRefresh").addEventListener("click", () => refreshAuditJobs());
  refreshAuditJobs();
}
refreshOperationsSummary().catch(() => {});
refreshViewerAccounts().catch(() => {});
refreshViewerReminderSettings().catch(() => {});
