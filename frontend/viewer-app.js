const SH_TZ = "Asia/Shanghai";
const APP_PATH = "/app/";
const VIEWER_DEVICE_ID_KEY = "hanyu_viewer_device_id";
const INSTALLABLE_PROTOCOLS = new Set(["https:", "http:"]);
const TIME_BUCKETS = [
  { id: "all", label: "不限" },
  { id: "week", label: "一周" },
  { id: "twoWeeks", label: "两周" },
  { id: "month", label: "一个月" },
  { id: "longer", label: "更久" },
];
const SECTION_LABELS = {
  unshipped: "该发没发",
  uninvoiced: "超60天没开票",
};
const SECTION_COPY = {
  unshipped: {
    quickTitle: "先看最急的这一条",
    quickCopy: "今天先不用看全部，先把最该补发的先看掉。",
    listTitle: "先把这一类看完整",
    listCopy: "这里才允许往下拉，把这一类的提醒看完整。",
  },
  uninvoiced: {
    quickTitle: "先看最该先看的客户",
    quickCopy: "今天先不用把所有单子看完，先看哪位客户最该先催票。",
    listTitle: "先把这些客户看完整",
    listCopy: "默认先按客户看，需要时再切到按单子看。",
  },
};
const ALERT_STATE_LABELS = {
  open: "已解除了",
  resolved: "回到当前",
};
const SEVERITY_RANK = {
  fatal: 0,
  important: 1,
  hint: 2,
};
const UNINVOICED_THRESHOLD_DAYS = 60;

const state = {
  profile: null,
  openAlerts: [],
  resolvedAlerts: [],
  openUninvoicedCustomers: [],
  resolvedUninvoicedCustomers: [],
  uninvoicedCustomerList: [],
  uninvoicedCustomerListKey: "",
  uninvoicedCustomerListLoading: false,
  currentScreen: "overview",
  activeSection: "unshipped",
  uninvoicedView: "customer",
  dueSegment: "overdue",
  alertState: "open",
  filters: {
    unshipped: { customer: "", draftCustomer: "", bucket: "all" },
    uninvoiced: { customer: "", draftCustomer: "", bucket: "all" },
  },
  currentCustomer: "",
  customerDetail: null,
  selectedAlertId: "",
  detail: null,
  source: null,
  sourceVisible: false,
  downloadModalOpen: false,
  downloadChoice: "all",
  downloadBusy: false,
  installPromptEvent: null,
  autoRefreshTimer: null,
  scrollPositions: {
    overview: 0,
    quick: 0,
    list: 0,
    customer: 0,
  },
  returnStack: [],
};

function byId(id) {
  return document.getElementById(id);
}

function isLoginPage() {
  return document.body?.dataset?.page === "login";
}

function isAppPage() {
  return document.body?.dataset?.page === "app";
}

function isMobileViewport() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(max-width: 719px)").matches
    : false;
}

function appOrigin() {
  return window.location.origin.replace(/\/$/, "");
}

function getApiBase() {
  const protocol = window.location?.protocol || "http:";
  const hostname = window.location?.hostname || "";
  const port = String(window.location?.port || "");
  if (port === "5173") {
    return `${protocol}//${hostname}:8000/v1`;
  }
  return `${appOrigin()}/v1`;
}

function appHref() {
  return `${appOrigin()}${APP_PATH}`;
}

function loginHref() {
  return `${appOrigin()}/`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function viewerDeviceId() {
  try {
    const existing = window.localStorage?.getItem(VIEWER_DEVICE_ID_KEY);
    if (existing) return existing;
    const generated =
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `device-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    window.localStorage?.setItem(VIEWER_DEVICE_ID_KEY, generated);
    return generated;
  } catch (_) {
    return `device-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
}

function viewerDeviceInfo() {
  const width = window.screen?.width || "";
  const height = window.screen?.height || "";
  return {
    device_id: viewerDeviceId(),
    user_agent: navigator.userAgent || "",
    platform: navigator.platform || "",
    language: navigator.language || "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
    screen: width && height ? `${width}x${height}` : "",
  };
}

function showMessage(id, text, tone = "error") {
  const node = byId(id);
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.textContent = "";
    node.className = "message-line";
    return;
  }
  node.hidden = false;
  node.textContent = text;
  node.className = `message-line ${tone === "success" ? "is-success" : "is-error"}`;
}

function tryParseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function formatApiError(responseText, status) {
  const payload = tryParseJson(responseText);
  if (payload && typeof payload === "object" && typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail.trim();
  }
  const raw = String(responseText || "").trim();
  if (raw) return raw;
  if (status === 401) return "登录已失效，请重新登录。";
  if (status === 403) return "当前账号没有权限。";
  return "请求失败，请稍后再试。";
}

async function request(path, options = {}) {
  const headers = {
    ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(`${getApiBase()}${path}`, {
    ...options,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    const error = new Error(formatApiError(text, response.status));
    error.status = response.status;
    error.rawText = text;
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response;
}

function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

function restoreScroll(y) {
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: y, behavior: "auto" });
  });
}

function currentFilterState() {
  return state.filters[state.activeSection];
}

function saveCurrentScroll() {
  if (["overview", "quick", "list"].includes(state.currentScreen)) {
    state.scrollPositions[state.currentScreen] = window.scrollY;
  }
}

function fmtTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: SH_TZ,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function fmtDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: SH_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function trimNumber(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "";
  if (Number.isInteger(number)) return String(number);
  return number.toFixed(2).replace(/\.?0+$/, "");
}

function numberFromText(value) {
  const cleaned = String(value ?? "")
    .replaceAll(",", "")
    .replace(/[^\d.-]/g, "");
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatAmount(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "部分金额暂缺";
  if (Math.abs(number) >= 10000) {
    return `${trimNumber(number / 10000)}万`;
  }
  return trimNumber(number);
}

function formatKnownAmount(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number) || number < 0) return "暂无";
  return formatAmount(number);
}

function outboundDaysFromOverdue(overdueDays) {
  const number = Number(overdueDays);
  if (!Number.isFinite(number)) return null;
  return Math.max(number, 0) + UNINVOICED_THRESHOLD_DAYS;
}

function formatOutboundDays(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "暂无";
  return `${trimNumber(number)} 天`;
}

function formatCustomerAmountText(total, hasMissingAmount) {
  const number = Number(total);
  if (Number.isFinite(number) && number > 0) return formatKnownAmount(number);
  return hasMissingAmount ? "金额暂缺" : "暂无";
}

function roleGreeting(profile) {
  const displayName = String(profile?.display_name || "").trim();
  if (!displayName) return "先看今天的事";
  return `${displayName}，先看今天的事`;
}

function parseDocNo(message) {
  return message.match(/订单〔([^〕]+)〕/)?.[1] || "";
}

function parseItemLabel(item, message) {
  const payload = item.payload || {};
  return (
    String(item.item_name || payload.item_name || item.item_code || payload.item_code || "").trim() ||
    message.match(/-\〔([^〕]+)〕/)?.[1] ||
    message.match(/产品〔([^〕]+)〕/)?.[1] ||
    "商品未写"
  );
}

function actualUninvoicedAmountFromItem(item, payload, message) {
  if (Object.prototype.hasOwnProperty.call(item, "actual_uninvoiced_amount")) {
    return numberFromText(item.actual_uninvoiced_amount);
  }
  return Number.isFinite(Number(payload.amount)) ? Number(payload.amount) : numberFromText(message.match(/金额〔([^〕]+)〕/)?.[1]);
}

function decorateAlert(item) {
  const message = String(item.message || "");
  const payload = item.payload || {};
  const itemLabel = parseItemLabel(item, message);
  const docNo = String(item.customer_order_no || payload.customer_order_no || "").trim() || parseDocNo(message) || "单据未写";
  const severityRank = SEVERITY_RANK[item.severity || item.level] ?? 9;

  if (item.alert_type === "due_before_ship") {
    const daysUntilDue = Number.isFinite(Number(payload.days_until_due)) ? Number(payload.days_until_due) : null;
    const overdueDays = daysUntilDue !== null && daysUntilDue < 0 ? Math.abs(daysUntilDue) : numberFromText(message.match(/交期已过〔([^〕]+)〕天/)?.[1]);
    const upcomingDays = daysUntilDue !== null && daysUntilDue >= 0 ? daysUntilDue : numberFromText(message.match(/距交期还有〔([^〕]+)〕天/)?.[1]);
    const quantityMatch = message.match(/应发〔([^〕]+)〕，已发〔([^〕]+)〕/);
    const quantity = Number.isFinite(Number(payload.quantity)) ? Number(payload.quantity) : numberFromText(quantityMatch?.[1]);
    const shipped = Number.isFinite(Number(payload.executed_shipped_qty)) ? Number(payload.executed_shipped_qty) : numberFromText(quantityMatch?.[2]);
    const pending = Number.isFinite(Number(payload.order_unshipped_qty))
      ? Math.max(Number(payload.order_unshipped_qty), 0)
      : quantity !== null && shipped !== null
        ? Math.max(quantity - shipped, 0)
        : null;
    const dueSegment = overdueDays !== null ? "overdue" : "upcoming";
    const bucketValue = overdueDays !== null ? overdueDays : upcomingDays;
    const lastChangedLabel = fmtDate(item.last_changed_at);
    return {
      ...item,
      viewSection: "unshipped",
      dueSegment,
      severityRank,
      docNo,
      itemLabel,
      quantity,
      shipped,
      pending,
      bucketValue,
      displayDate: lastChangedLabel,
      statusCallout:
        dueSegment === "overdue"
          ? `已拖 ${trimNumber(overdueDays ?? 0)} 天`
          : `还有 ${trimNumber(upcomingDays ?? 0)} 天到期`,
      summaryCopy:
        pending !== null ? `还差 ${trimNumber(pending)} 件` : dueSegment === "overdue" ? "这条先补发" : "这条先盯住",
      metricItems: [
        { label: "应发", value: quantity !== null ? trimNumber(quantity) : "暂无" },
        { label: "已发", value: shipped !== null ? trimNumber(shipped) : "暂无" },
        { label: "还差", value: pending !== null ? trimNumber(pending) : "暂无" },
      ],
    };
  }

  const daysAfter = Number.isFinite(Number(item.current_days_after_outbound))
    ? Number(item.current_days_after_outbound)
    : Number.isFinite(Number(payload.days_after_outbound))
      ? Number(payload.days_after_outbound)
      : numberFromText(message.match(/距最近出库已〔([^〕]+)〕天/)?.[1]);
  const overdueBeyond = daysAfter !== null ? Math.max(daysAfter - 60, 0) : null;
  const outboundDaysText = formatOutboundDays(daysAfter);
  const amount = actualUninvoicedAmountFromItem(item, payload, message);
  const uninvoicedQty = Number.isFinite(Number(payload.uninvoiced_qty))
    ? Number(payload.uninvoiced_qty)
    : numberFromText(message.match(/未开票数量〔([^〕]+)〕/)?.[1]);
  const lastChangedLabel = fmtDate(item.last_changed_at);
  return {
    ...item,
    viewSection: "uninvoiced",
    severityRank,
    docNo,
    itemLabel,
    daysAfter,
    overdueBeyond,
    amount,
    uninvoicedQty,
    bucketValue: overdueBeyond,
    displayDate: lastChangedLabel,
    statusCallout: `出库天数 ${outboundDaysText}`,
    summaryCopy: amount !== null ? `还没开 ${formatAmount(amount)}` : "金额暂缺，先催票",
    metricItems: [
      { label: "本笔未开", value: amount !== null ? formatAmount(amount) : "部分金额暂缺" },
      { label: "出库天数", value: outboundDaysText },
      { label: "未开票", value: uninvoicedQty !== null ? trimNumber(uninvoicedQty) : "暂无" },
    ],
  };
}

function getDecoratedAlerts(status = "open") {
  const source = status === "resolved" ? state.resolvedAlerts : state.openAlerts;
  return source.map(decorateAlert);
}

function compareUnshipped(a, b) {
  if (a.severityRank !== b.severityRank) return a.severityRank - b.severityRank;
  if (a.dueSegment !== b.dueSegment) return a.dueSegment === "overdue" ? -1 : 1;
  if (a.dueSegment === "overdue") return (b.bucketValue ?? -1) - (a.bucketValue ?? -1);
  if ((a.bucketValue ?? 999) !== (b.bucketValue ?? 999)) return (a.bucketValue ?? 999) - (b.bucketValue ?? 999);
  return new Date(b.last_changed_at).getTime() - new Date(a.last_changed_at).getTime();
}

function compareUninvoiced(a, b) {
  const aSort = Number.isFinite(Number(a.viewer_sort_index)) ? Number(a.viewer_sort_index) : null;
  const bSort = Number.isFinite(Number(b.viewer_sort_index)) ? Number(b.viewer_sort_index) : null;
  if (aSort !== null || bSort !== null) return (aSort ?? 999999) - (bSort ?? 999999);
  if (a.severityRank !== b.severityRank) return a.severityRank - b.severityRank;
  if ((a.amount ?? -1) !== (b.amount ?? -1)) return (b.amount ?? -1) - (a.amount ?? -1);
  if ((a.bucketValue ?? -1) !== (b.bucketValue ?? -1)) return (b.bucketValue ?? -1) - (a.bucketValue ?? -1);
  return new Date(b.last_changed_at).getTime() - new Date(a.last_changed_at).getTime();
}

function compareBySection(a, b) {
  return a.viewSection === "unshipped" ? compareUnshipped(a, b) : compareUninvoiced(a, b);
}

function matchesBucket(value, bucketId) {
  if (bucketId === "all") return true;
  if (value === null || value === undefined) return false;
  if (bucketId === "week") return value <= 7;
  if (bucketId === "twoWeeks") return value > 7 && value <= 14;
  if (bucketId === "month") return value > 14 && value <= 30;
  return value > 30;
}

function listAlerts(section, { status = state.alertState, applyFilters = true } = {}) {
  const filter = state.filters[section];
  const normalizedCustomer = String(filter.customer || "").trim().toLowerCase();
  return getDecoratedAlerts(status)
    .filter((item) => item.viewSection === section)
    .filter((item) => (section === "unshipped" ? item.dueSegment === state.dueSegment : true))
    .filter((item) => (applyFilters && normalizedCustomer ? item.customer.toLowerCase().includes(normalizedCustomer) : true))
    .filter((item) => (applyFilters ? matchesBucket(item.bucketValue, filter.bucket) : true))
    .sort(compareBySection);
}

function baseUninvoicedCustomers(status = "open") {
  return status === "resolved" ? state.resolvedUninvoicedCustomers : state.openUninvoicedCustomers;
}

function currentUninvoicedCustomers() {
  if (state.currentScreen === "list" && state.activeSection === "uninvoiced" && state.uninvoicedView === "customer") {
    return state.uninvoicedCustomerList;
  }
  return baseUninvoicedCustomers(state.alertState);
}

function topUrgentAlert(section) {
  const items = getDecoratedAlerts("open").filter((item) => item.viewSection === section);
  if (!items.length) return null;
  if (section === "unshipped") {
    const overdueItems = items.filter((item) => item.dueSegment === "overdue").sort(compareUnshipped);
    if (overdueItems.length) return overdueItems[0];
    return items.sort(compareUnshipped)[0];
  }
  return items.sort(compareUninvoiced)[0];
}

function topUrgentUninvoicedCustomer() {
  const items = baseUninvoicedCustomers("open");
  return items.length ? items[0] : null;
}

function quickRemainingCount(section) {
  if (section === "uninvoiced") {
    return Math.max(baseUninvoicedCustomers("open").length - 1, 0);
  }
  return Math.max(getDecoratedAlerts("open").filter((item) => item.viewSection === section).length - 1, 0);
}

function uniqueCustomerCount(section) {
  if (section === "uninvoiced") {
    return baseUninvoicedCustomers("open").length;
  }
  const names = new Set(
    getDecoratedAlerts("open")
      .filter((item) => item.viewSection === section)
      .map((item) => item.customer)
  );
  return names.size;
}

function overviewCardModel(section) {
  const openItems = getDecoratedAlerts("open").filter((item) => item.viewSection === section);
  if (section === "unshipped") {
    const overdueItems = openItems.filter((item) => item.dueSegment === "overdue").sort(compareUnshipped);
    const upcomingItems = openItems.filter((item) => item.dueSegment === "upcoming").sort(compareUnshipped);
    let support = "现在没有拖单";
    if (overdueItems.length) {
      support = `最久已拖 ${trimNumber(overdueItems[0].bucketValue ?? 0)} 天`;
    } else if (upcomingItems.length) {
      support = `最近还有 ${trimNumber(upcomingItems[0].bucketValue ?? 0)} 天`;
    }
    return {
      section,
      label: SECTION_LABELS[section],
      value: trimNumber(openItems.length || 0),
      support,
      cta: "点一下看最急的",
    };
  }
  const customers = baseUninvoicedCustomers("open");
  let knownTotal = 0;
  let hasMissingAmount = false;
  for (const item of customers) {
    if (Number.isFinite(Number(item.known_amount_total))) {
      knownTotal += Number(item.known_amount_total);
    }
    if (item.has_missing_amount) hasMissingAmount = true;
  }
  return {
    section,
    label: SECTION_LABELS[section],
    value: customers.length ? (knownTotal > 0 ? formatKnownAmount(knownTotal) : hasMissingAmount ? "暂缺" : "0") : "0",
    support: `涉及 ${trimNumber(uniqueCustomerCount(section))} 位客户${hasMissingAmount ? " · 部分金额暂缺" : ""}`,
    cta: "点一下看最急的客户",
  };
}

function findUninvoicedCustomerSummary(customer) {
  const target = String(customer || "").trim();
  if (!target) return null;
  const fromOpen = baseUninvoicedCustomers("open").find((item) => item.customer === target);
  if (fromOpen) return fromOpen;
  if (state.customerDetail && state.customerDetail.customer === target) {
    return {
      customer: state.customerDetail.customer,
      known_amount_total: state.customerDetail.known_amount_total,
      has_missing_amount: state.customerDetail.has_missing_amount,
      overdue_max_days: state.customerDetail.overdue_max_days,
      alert_count: state.customerDetail.alert_count,
      related_order_count: state.customerDetail.related_order_count,
    };
  }
  return null;
}

function relatedOrderCount(item) {
  const orderCount = Number(item?.related_order_count);
  if (Number.isFinite(orderCount)) return orderCount;
  const alertCount = Number(item?.alert_count);
  return Number.isFinite(alertCount) ? alertCount : 0;
}

function customerDebtSnapshot(customer) {
  const summary = findUninvoicedCustomerSummary(customer);
  if (summary) {
    const amountText = formatCustomerAmountText(summary.known_amount_total, summary.has_missing_amount);
    return {
      text: summary.has_missing_amount ? `${amountText}（部分金额暂缺）` : amountText,
      hasMissing: Boolean(summary.has_missing_amount),
    };
  }
  const rows = getDecoratedAlerts("open").filter((item) => item.viewSection === "uninvoiced" && item.customer === customer);
  let total = 0;
  let hasAmount = false;
  let hasMissing = false;
  for (const row of rows) {
    if (Number.isFinite(row.amount)) {
      total += row.amount;
      hasAmount = true;
    } else {
      hasMissing = true;
    }
  }
  if (!hasAmount && hasMissing) return { text: "部分金额暂缺", hasMissing: true };
  if (!hasAmount) return { text: "暂无", hasMissing: false };
  return { text: hasMissing ? `${formatAmount(total)}（部分金额暂缺）` : formatAmount(total), hasMissing };
}

function renderMetricStrip(metrics) {
  return `
    <div class="metric-strip">
      ${metrics
        .map(
          (metric) => `
            <div class="metric-strip__item">
              <span>${escapeHtml(metric.label)}</span>
              <strong>${escapeHtml(metric.value)}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderMetaChips(item) {
  const chips = [item.docNo, item.displayDate, item.change_label || item.status_label].filter(Boolean);
  return chips.map((chip) => `<span class="meta-chip">${escapeHtml(chip)}</span>`).join("");
}

function renderOverviewCard(model) {
  return `
    <button
      type="button"
      class="overview-card overview-card--${escapeHtml(model.section)}"
      data-action="open-quick"
      data-section="${escapeHtml(model.section)}"
    >
      <div class="overview-card__top">
        <p>${escapeHtml(model.label)}</p>
        <span>${escapeHtml(model.cta)}</span>
      </div>
      <strong class="overview-card__value">${escapeHtml(model.value)}</strong>
      <p class="overview-card__support">${escapeHtml(model.support)}</p>
    </button>
  `;
}

function renderOverviewScreen() {
  return `
    <section class="page-shell page-shell--overview">
      <header class="topbar topbar--overview">
        <div>
          <p class="page-kicker">提醒中心</p>
          <h1 class="page-title">今天先看这两件事</h1>
          <p class="page-subtitle">${escapeHtml(roleGreeting(state.profile))}</p>
        </div>
        <div class="topbar__actions">
          <button type="button" class="text-action" data-action="refresh-app">刷新</button>
          <button type="button" class="text-action" data-action="logout">退出</button>
        </div>
      </header>
      <div class="overview-grid">
        ${renderOverviewCard(overviewCardModel("unshipped"))}
        ${renderOverviewCard(overviewCardModel("uninvoiced"))}
      </div>
    </section>
  `;
}

function renderPrimaryCard(item) {
  if (!item) {
    return `
      <div class="empty-panel">
        <p>这一类现在没有最急提醒。</p>
        <p>可以点“查看更多”，去完整列表里再看一眼。</p>
      </div>
    `;
  }
  return `
    <button type="button" class="focus-card focus-card--${escapeHtml(item.viewSection)}" data-action="open-detail" data-alert-id="${escapeHtml(item.id)}">
      <div class="focus-card__header">
        <div>
          <p class="card-kicker">${escapeHtml(SECTION_LABELS[item.viewSection])}</p>
          <h2>${escapeHtml(item.customer)}</h2>
        </div>
        <span class="status-chip status-chip--${escapeHtml(item.viewSection)}">${escapeHtml(item.statusCallout)}</span>
      </div>
      <div class="focus-card__body">
        <p class="focus-card__item">${escapeHtml(item.itemLabel)}</p>
        <p class="focus-card__summary">${escapeHtml(item.summaryCopy)}</p>
      </div>
      ${renderMetricStrip(item.metricItems)}
      <div class="card-footer">
        <div class="meta-row">${renderMetaChips(item)}</div>
        <span class="detail-link">点开看详情</span>
      </div>
    </button>
  `;
}

function renderCustomerPrimaryCard(item) {
  if (!item) {
    return `
      <div class="empty-panel">
        <p>这一类现在没有最该先看的客户。</p>
        <p>可以点“查看更多”，去完整客户排行里再看一眼。</p>
      </div>
    `;
  }
  const amountText = formatCustomerAmountText(item.known_amount_total, item.has_missing_amount);
  const outboundDaysText = formatOutboundDays(outboundDaysFromOverdue(item.overdue_max_days));
  const summaryCopy =
    item.has_missing_amount && amountText !== "金额暂缺"
      ? `${amountText}，部分金额暂缺`
      : item.has_missing_amount
        ? "金额暂缺，先看这位客户"
        : `当前总未开票 ${amountText}`;
  const customerAction = isMobileViewport() ? "open-uninvoiced-lead-detail" : "open-customer";
  const detailLinkClass = isMobileViewport() ? "detail-link" : "detail-link detail-link--customer";
  return `
    <button
      type="button"
      class="focus-card focus-card--uninvoiced"
      data-action="${customerAction}"
      data-customer="${escapeHtml(item.customer)}"
    >
      <div class="focus-card__header">
        <div>
          <p class="card-kicker">${escapeHtml(SECTION_LABELS.uninvoiced)}</p>
          <h2>${escapeHtml(item.customer)}</h2>
        </div>
        <span class="status-chip status-chip--uninvoiced">出库天数 ${escapeHtml(outboundDaysText)}</span>
      </div>
      <div class="focus-card__body">
        <p class="focus-card__item">${escapeHtml(summaryCopy)}</p>
        <p class="focus-card__summary">先看这位客户，再决定先催哪一笔。</p>
      </div>
      ${renderMetricStrip([
        { label: "总未开票", value: amountText },
        { label: "出库天数", value: outboundDaysText },
        { label: "相关订单数", value: trimNumber(relatedOrderCount(item)) },
      ])}
      <div class="card-footer">
        <div class="meta-row">
          ${item.change_label ? `<span class="meta-chip">${escapeHtml(item.change_label)}</span>` : ""}
          <span class="meta-chip">${escapeHtml(fmtDate(item.latest_changed_at))}</span>
          ${item.has_missing_amount ? '<span class="meta-chip">部分金额暂缺</span>' : ""}
        </div>
        <span class="${detailLinkClass}">点开看详情</span>
      </div>
    </button>
  `;
}

function renderDownloadAction(section) {
  if (section !== "uninvoiced") return "";
  return `<button type="button" class="text-action download-action" data-action="open-download">下载</button>`;
}

function hasUninvoicedDownloadData() {
  return baseUninvoicedCustomers("open").length > 0 || getDecoratedAlerts("open").some((item) => item.viewSection === "uninvoiced");
}

function renderQuickScreen() {
  const section = state.activeSection;
  const copy = SECTION_COPY[section];
  const remainingCount = quickRemainingCount(section);
  return `
    <section class="page-shell page-shell--quick">
      <header class="topbar topbar--quick ${section === "uninvoiced" ? "topbar--with-download" : ""}">
        <button type="button" class="back-link" data-action="back-to-overview">返回</button>
        <p class="topbar__title">${escapeHtml(SECTION_LABELS[section])}</p>
        ${renderDownloadAction(section)}
      </header>
      <div class="page-intro">
        <h1 class="page-title page-title--secondary">${escapeHtml(copy.quickTitle)}</h1>
        <p class="page-subtitle">${escapeHtml(copy.quickCopy)}</p>
      </div>
      ${section === "uninvoiced" ? renderCustomerPrimaryCard(topUrgentUninvoicedCustomer()) : renderPrimaryCard(topUrgentAlert(section))}
      <button
        type="button"
        class="stack-button"
        data-action="open-list"
        data-section="${escapeHtml(section)}"
        data-remaining="${escapeHtml(String(remainingCount))}"
      >
        查看更多
      </button>
    </section>
  `;
}

function mobileListTitle(section) {
  return section === "unshipped" ? "全部未发" : "全部未开票";
}

function resolvedToggleCopy() {
  return state.alertState === "open" ? "已解除了" : "回到当前";
}

function renderBucketButtons() {
  const filter = currentFilterState();
  return TIME_BUCKETS.map(
    (bucket) => `
      <button
        type="button"
        class="bucket-button ${filter.bucket === bucket.id ? "is-active" : ""}"
        data-action="switch-bucket"
        data-bucket="${escapeHtml(bucket.id)}"
      >
        ${escapeHtml(bucket.label)}
      </button>
    `
  ).join("");
}

function renderUninvoicedViewButtons() {
  return `
    <div class="segmented-group">
      <button type="button" class="segment-button ${state.uninvoicedView === "customer" ? "is-active" : ""}" data-action="switch-uninvoiced-view" data-view="customer">按客户看</button>
      <button type="button" class="segment-button ${state.uninvoicedView === "order" ? "is-active" : ""}" data-action="switch-uninvoiced-view" data-view="order">按单子看</button>
    </div>
  `;
}

function renderListCard(item) {
  return `
    <button type="button" class="list-card list-card--${escapeHtml(item.viewSection)}" data-action="open-detail" data-alert-id="${escapeHtml(item.id)}">
      <div class="list-card__header">
        <div>
          <p class="card-kicker">${escapeHtml(SECTION_LABELS[item.viewSection])}</p>
          <h3>${escapeHtml(item.customer)}</h3>
        </div>
        <span class="status-chip status-chip--${escapeHtml(item.viewSection)}">${escapeHtml(item.statusCallout)}</span>
      </div>
      <div class="list-card__body">
        <p class="list-card__item">${escapeHtml(item.itemLabel)}</p>
        <p class="list-card__summary">${escapeHtml(item.summaryCopy)}</p>
      </div>
      ${renderMetricStrip(item.metricItems)}
      <div class="card-footer">
        <div class="meta-row">${renderMetaChips(item)}</div>
        <span class="detail-link">点开看详情</span>
      </div>
    </button>
  `;
}

function renderCustomerListCard(item) {
  const amountText = formatCustomerAmountText(item.known_amount_total, item.has_missing_amount);
  const outboundDaysText = formatOutboundDays(outboundDaysFromOverdue(item.overdue_max_days));
  const customerAction = isMobileViewport() ? "open-uninvoiced-lead-detail" : "open-customer";
  const detailLinkClass = isMobileViewport() ? "detail-link" : "detail-link detail-link--customer";
  return `
    <button
      type="button"
      class="list-card list-card--uninvoiced"
      data-action="${customerAction}"
      data-customer="${escapeHtml(item.customer)}"
    >
      <div class="list-card__header">
        <div>
          <p class="card-kicker">${escapeHtml(SECTION_LABELS.uninvoiced)}</p>
          <h3>${escapeHtml(item.customer)}</h3>
        </div>
        <span class="status-chip status-chip--uninvoiced">出库天数 ${escapeHtml(outboundDaysText)}</span>
      </div>
      <div class="list-card__body">
        <p class="list-card__item">总未开票 ${escapeHtml(amountText)}</p>
        <p class="list-card__summary">${escapeHtml(item.has_missing_amount ? "这家客户里还有部分金额暂缺。" : "先看客户总览，再往下看具体单子。")}</p>
      </div>
      ${renderMetricStrip([
        { label: "总未开票", value: amountText },
        { label: "出库天数", value: outboundDaysText },
        { label: "相关订单数", value: trimNumber(relatedOrderCount(item)) },
      ])}
      <div class="card-footer">
        <div class="meta-row">
          <span class="meta-chip">${escapeHtml(fmtDate(item.latest_changed_at))}</span>
          ${item.change_label ? `<span class="meta-chip">${escapeHtml(item.change_label)}</span>` : ""}
          ${item.has_missing_amount ? '<span class="meta-chip">部分金额暂缺</span>' : ""}
        </div>
        <span class="${detailLinkClass}">点开看详情</span>
      </div>
    </button>
  `;
}

function renderListEmpty(section) {
  if (section === "uninvoiced" && state.uninvoicedView === "customer") {
    const copy = state.alertState === "resolved" ? "当前没有已解除的客户提醒。" : "当前没有需要继续催票的客户排行。";
    return `
      <div class="empty-panel">
        <p>${escapeHtml(copy)}</p>
        <p>可以换一下时间挡位、客户关键词，或切到“按单子看”。</p>
      </div>
    `;
  }
  const copy =
    state.alertState === "resolved"
      ? "这一类已解除提醒现在是空的。"
      : section === "unshipped"
        ? "这一类现在没有需要继续盯的发货提醒。"
        : "这一类现在没有需要继续催票的提醒。";
  return `
    <div class="empty-panel">
      <p>${escapeHtml(copy)}</p>
      <p>可以换一下时间挡位或客户关键词再看。</p>
    </div>
  `;
}

function renderListScreen() {
  const section = state.activeSection;
  const copy = SECTION_COPY[section];
  const filter = currentFilterState();
  const isUninvoicedCustomerView = section === "uninvoiced" && state.uninvoicedView === "customer";
  const items = isUninvoicedCustomerView ? currentUninvoicedCustomers() : listAlerts(section, { status: state.alertState, applyFilters: true });
  const countLabel = isUninvoicedCustomerView ? "位客户" : "条";
  return `
    <section class="page-shell page-shell--list">
      <header class="topbar topbar--list ${section === "uninvoiced" ? "topbar--with-download" : ""}">
        <button type="button" class="back-link" data-action="back-to-quick">返回</button>
        <p class="topbar__title">${escapeHtml(isMobileViewport() ? mobileListTitle(section) : SECTION_LABELS[section])}</p>
        ${renderDownloadAction(section)}
      </header>
      <div class="page-intro">
        <h1 class="page-title page-title--secondary">${escapeHtml(copy.listTitle)}</h1>
        <p class="page-subtitle">${escapeHtml(copy.listCopy)}</p>
      </div>

      <label class="search-field">
        <span>搜索客户</span>
        <input
          type="text"
          data-role="customer-filter"
          placeholder="输入客户关键词"
          value="${escapeHtml(filter.draftCustomer)}"
        />
      </label>

      ${
        section === "unshipped"
          ? `
            <div class="segmented-group">
              <button type="button" class="segment-button ${state.dueSegment === "overdue" ? "is-active" : ""}" data-action="switch-due-segment" data-segment="overdue">已经拖了</button>
              <button type="button" class="segment-button ${state.dueSegment === "upcoming" ? "is-active" : ""}" data-action="switch-due-segment" data-segment="upcoming">快到该发</button>
            </div>
          `
          : section === "uninvoiced"
            ? renderUninvoicedViewButtons()
            : ""
      }

      <div class="bucket-row">${renderBucketButtons()}</div>

      <button type="button" class="inline-link" data-action="toggle-alert-state">
        <span>${escapeHtml(resolvedToggleCopy())}</span>
        <span>›</span>
      </button>

      <p class="list-note">
        ${
          isUninvoicedCustomerView && state.uninvoicedCustomerListLoading
            ? "正在按客户重算这份排行。"
            : `当前共 ${escapeHtml(trimNumber(items.length || 0))}${escapeHtml(countLabel)}。${escapeHtml(
                state.alertState === "open" ? "当前只看还在成立的提醒。" : "这里看的都是已解除提醒。"
              )}`
        }
      </p>

      <div class="list-stack">
        ${
          isUninvoicedCustomerView
            ? items.length
              ? items.map(renderCustomerListCard).join("")
              : renderListEmpty(section)
            : items.length
              ? items.map(renderListCard).join("")
              : renderListEmpty(section)
        }
      </div>
    </section>
  `;
}

function detailModel(detail) {
  const payload = detail.payload || {};
  if (detail.alert_type === "due_before_ship") {
    const quantity = Number(payload.quantity);
    const shipped = Number(payload.executed_shipped_qty);
    const pending = Number(payload.order_unshipped_qty);
    const daysUntilDue = Number(payload.days_until_due);
    const overdueDays = Number.isFinite(daysUntilDue) && daysUntilDue < 0 ? Math.abs(daysUntilDue) : null;
    const upcomingDays = Number.isFinite(daysUntilDue) && daysUntilDue >= 0 ? daysUntilDue : null;
    const headline =
      overdueDays !== null
        ? `这单还没发齐，还差 ${trimNumber(pending)} 件，已经晚了 ${trimNumber(overdueDays)} 天`
        : `这单还没发齐，还差 ${trimNumber(pending)} 件，还有 ${trimNumber(upcomingDays ?? 0)} 天到期`;
    return {
      section: "unshipped",
      headline,
      support: "先看清详情，再联系处理",
      metrics: [
        { label: "应发", value: Number.isFinite(quantity) ? trimNumber(quantity) : "暂无" },
        { label: "已发", value: Number.isFinite(shipped) ? trimNumber(shipped) : "暂无" },
        { label: "还差", value: Number.isFinite(pending) ? trimNumber(pending) : "暂无" },
      ],
      infoFields: [
        { label: "客户", value: detail.customer || "暂无" },
        { label: "时间", value: overdueDays !== null ? `已拖 ${trimNumber(overdueDays)} 天` : `还有 ${trimNumber(upcomingDays ?? 0)} 天到期` },
        { label: "货品", value: payload.item_name || detail.item_name || payload.item_code || detail.item_code || "暂无" },
        { label: "订单号", value: payload.customer_order_no || detail.customer_order_no || "暂无" },
        { label: "交期", value: payload.due_date || "暂无" },
        { label: "最近变化", value: fmtTime(detail.last_changed_at) || "暂无" },
      ],
      mobileRows: [
        { label: "客户", value: detail.customer || "暂无" },
        { label: "货品", value: payload.item_name || detail.item_name || payload.item_code || detail.item_code || "暂无" },
        { label: "还差", value: Number.isFinite(pending) ? trimNumber(pending) : "暂无", tone: "danger", emphasis: true },
        { label: "应发", value: Number.isFinite(quantity) ? trimNumber(quantity) : "暂无" },
        { label: "已发", value: Number.isFinite(shipped) ? trimNumber(shipped) : "暂无" },
        { label: "逾期", value: overdueDays !== null ? `已拖 ${trimNumber(overdueDays)} 天` : `还有 ${trimNumber(upcomingDays ?? 0)} 天到期`, tone: overdueDays !== null ? "danger" : "" },
        { label: "交期", value: payload.due_date || "暂无" },
        { label: "订单号", value: payload.customer_order_no || detail.customer_order_no || "暂无" },
        { label: "最近变化", value: fmtTime(detail.last_changed_at) || "暂无" },
      ],
    };
  }

  const message = String(detail.message || payload.message_short || "");
  const amount = actualUninvoicedAmountFromItem(detail, payload, message);
  const daysAfter = Number.isFinite(Number(detail.current_days_after_outbound))
    ? Number(detail.current_days_after_outbound)
    : numberFromText(payload.days_after_outbound);
  const overdueBeyond = daysAfter !== null ? Math.max(daysAfter - 60, 0) : null;
  const outboundDaysText = formatOutboundDays(daysAfter);
  const customerDebt = customerDebtSnapshot(detail.customer);
  const amountText = amount !== null ? formatAmount(amount) : "部分金额暂缺";
  const headline = amount !== null ? `这笔还没开 ${amountText}，出库天数 ${outboundDaysText}` : `这笔还没开票，出库天数 ${outboundDaysText}`;
  return {
    section: "uninvoiced",
    headline,
    support: `这个客户当前共欠 ${customerDebt.text}`,
    supportNote: customerDebt.hasMissing ? "客户总额里还有部分金额暂缺。": "",
    metrics: [
      { label: "本笔未开", value: amountText },
      { label: "出库天数", value: outboundDaysText },
      { label: "客户共欠", value: customerDebt.text },
    ],
    infoFields: [
      { label: "客户", value: detail.customer || "暂无" },
      { label: "未开票数量", value: payload.uninvoiced_qty !== undefined ? trimNumber(payload.uninvoiced_qty) : "暂无" },
      { label: "货品", value: payload.item_name || detail.item_name || payload.item_code || detail.item_code || "暂无" },
      { label: "订单号", value: payload.customer_order_no || detail.customer_order_no || "暂无" },
      { label: "最近出库", value: payload.latest_outbound_date || "暂无" },
      { label: "最近变化", value: fmtTime(detail.last_changed_at) || "暂无" },
    ],
    mobileRows: [
      { label: "客户", value: detail.customer || "暂无" },
      { label: "货品", value: payload.item_name || detail.item_name || payload.item_code || detail.item_code || "暂无" },
      { label: "本笔未开", value: amountText, tone: "danger", emphasis: true },
      { label: "客户共欠", value: customerDebt.text, emphasis: true },
      { label: "出库天数", value: outboundDaysText, tone: daysAfter !== null ? "danger" : "" },
      { label: "未开票数量", value: payload.uninvoiced_qty !== undefined ? trimNumber(payload.uninvoiced_qty) : "暂无" },
      { label: "最近出库", value: payload.latest_outbound_date || "暂无" },
      { label: "订单号", value: payload.customer_order_no || detail.customer_order_no || "暂无" },
      { label: "最近变化", value: fmtTime(detail.last_changed_at) || "暂无" },
    ],
  };
}

function renderInfoFields(fields) {
  return `
    <dl class="info-grid">
      ${fields
        .map(
          (field) => `
            <div class="info-grid__item">
              <dt>${escapeHtml(field.label)}</dt>
              <dd>${escapeHtml(field.value)}</dd>
            </div>
          `
        )
        .join("")}
    </dl>
  `;
}

function renderMobileDetailRowsMarkup(rows) {
  return rows
    .map(
      (row) => `
        <div class="detail-sheet__row">
          <span class="detail-sheet__label">${escapeHtml(row.label)}</span>
          <strong class="detail-sheet__value${row.emphasis ? " is-strong" : ""}${row.tone ? ` is-${escapeHtml(row.tone)}` : ""}">${escapeHtml(row.value)}</strong>
        </div>
      `
    )
    .join("");
}

function renderMobileDetailRows(rows, options = {}) {
  const title = String(options.title || "").trim();
  const titleClass = String(options.titleClass || "").trim();
  const footerLabel = String(options.footerLabel || "").trim();
  const footerClass = String(options.footerClass || "").trim();
  return `
    <div class="detail-sheet${title ? " has-heading" : ""}${footerLabel ? " has-footer" : ""}">
      ${title ? `<div class="detail-sheet__heading${titleClass ? ` ${titleClass}` : ""}">${escapeHtml(title)}</div>` : ""}
      ${renderMobileDetailRowsMarkup(rows)}
      ${footerLabel ? `<div class="detail-sheet__footer${footerClass ? ` ${footerClass}` : ""}">${escapeHtml(footerLabel)}</div>` : ""}
    </div>
  `;
}

function renderCustomerMobileOrderCard(item) {
  const amountText = item.amount !== null ? formatAmount(item.amount) : "金额暂缺";
  const outboundDaysText = formatOutboundDays(item.daysAfter);
  return `
    <button type="button" class="detail-sheet detail-sheet--order-card has-heading has-footer" data-action="open-detail" data-alert-id="${escapeHtml(item.id)}">
      <div class="detail-sheet__heading detail-sheet__heading--order">${escapeHtml(item.itemLabel)}</div>
      ${renderMobileDetailRowsMarkup([
        { label: "本笔未开", value: amountText, emphasis: true, tone: item.amount !== null ? "danger" : "" },
        { label: "未开票数", value: item.uninvoicedQty !== null ? trimNumber(item.uninvoicedQty) : "暂无" },
        { label: "出库天数", value: outboundDaysText, tone: item.daysAfter !== null ? "danger" : "" },
        { label: "订单号", value: item.docNo || "暂无" },
        { label: "日期", value: item.displayDate || "暂无" },
      ])}
      <div class="detail-sheet__footer">查看完整详情</div>
    </button>
  `;
}

function renderSourceTable(title, rows) {
  const entries = Object.entries(rows || {}).filter(([, value]) => value !== null && value !== undefined && String(value) !== "");
  if (!entries.length) return "";
  return `
    <section class="source-table-wrap">
      <table class="source-table">
        <thead>
          <tr>
            <th colspan="2">${escapeHtml(title)}</th>
          </tr>
        </thead>
        <tbody>
          ${entries
            .map(
              ([key, value]) => `
                <tr>
                  <th>${escapeHtml(key)}</th>
                  <td>${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </section>
  `;
}

function renderSourcePanel() {
  if (!state.source) {
    return `
      <div class="source-panel">
        <div class="empty-panel">
          <p>正在读取原表内容。</p>
        </div>
      </div>
    `;
  }

  return `
    <div class="source-panel">
      <div class="source-summary">
        <div class="source-summary__item">
          <span>文件</span>
          <strong>${escapeHtml(state.source.filename || "未知文件")}</strong>
        </div>
        <div class="source-summary__item">
          <span>源行号</span>
          <strong>${escapeHtml(state.source.source_row ?? "暂无")}</strong>
        </div>
      </div>
      ${renderSourceTable("核心字段", state.source.core)}
      ${renderSourceTable("扩展字段", state.source.ext)}
    </div>
  `;
}

function renderCustomerScreen() {
  if (!state.customerDetail) {
    return `
      <section class="page-shell page-shell--detail">
        <header class="topbar topbar--detail">
          <button type="button" class="back-link" data-action="back-from-customer">返回</button>
          <p class="topbar__title">${isMobileViewport() ? "客户详情" : "客户页"}</p>
        </header>
        <div class="empty-panel">
          <p>正在读取这个客户的汇总和单子。</p>
        </div>
      </section>
    `;
  }

  const detail = state.customerDetail;
  const amountText = formatCustomerAmountText(detail.known_amount_total, detail.has_missing_amount);
  const outboundDaysText = formatOutboundDays(outboundDaysFromOverdue(detail.overdue_max_days));
  const items = Array.isArray(detail.items) ? detail.items.map(decorateAlert) : [];
  if (isMobileViewport()) {
    return `
      <section class="page-shell page-shell--detail page-shell--customer">
        <header class="topbar topbar--detail">
          <button type="button" class="back-link" data-action="back-from-customer">返回</button>
          <p class="topbar__title">客户详情</p>
        </header>

        ${renderMobileDetailRows([
          { label: "总未开票", value: amountText, tone: "danger", emphasis: true },
          { label: "出库天数", value: outboundDaysText, tone: detail.overdue_max_days !== null ? "danger" : "" },
          { label: "相关订单数", value: `${trimNumber(relatedOrderCount(detail))} 笔` },
          { label: "最近变化", value: fmtTime(detail.latest_changed_at) || "暂无" },
        ], { title: detail.customer || "暂无", titleClass: "detail-sheet__heading--customer" })}

        <div class="list-stack list-stack--customer">
          ${items.length ? items.map(renderCustomerMobileOrderCard).join("") : renderListEmpty("uninvoiced")}
        </div>
      </section>
    `;
  }
  return `
    <section class="page-shell page-shell--detail">
      <header class="topbar topbar--detail">
        <button type="button" class="back-link" data-action="back-from-customer">返回</button>
        <p class="topbar__title">客户页</p>
      </header>

      <p class="page-kicker">${escapeHtml(SECTION_LABELS.uninvoiced)}</p>
      <div class="detail-hero">
        <h1 class="page-title page-title--secondary">${escapeHtml(detail.customer)}｜${escapeHtml(amountText)}</h1>
        <p class="page-subtitle">出库天数 ${escapeHtml(outboundDaysText)}｜相关订单 ${escapeHtml(trimNumber(relatedOrderCount(detail)))} 笔</p>
        ${detail.has_missing_amount ? '<p class="detail-note">这个客户里还有部分金额暂缺，但仍已参与排行。</p>' : ""}
      </div>

      ${renderMetricStrip([
        { label: "总未开票", value: amountText },
        { label: "出库天数", value: outboundDaysText },
        { label: "相关订单数", value: trimNumber(relatedOrderCount(detail)) },
      ])}

      <div class="list-stack">
        ${items.length ? items.map(renderListCard).join("") : renderListEmpty("uninvoiced")}
      </div>
    </section>
  `;
}

function renderDetailScreen() {
  if (!state.detail) {
    return `
      <section class="page-shell page-shell--detail">
        <header class="topbar topbar--detail">
          <button type="button" class="back-link" data-action="back-from-detail">返回</button>
          <p class="topbar__title">${isMobileViewport() ? "订单详情" : "先看这一条"}</p>
        </header>
        <div class="empty-panel">
          <p>正在读取这一条提醒。</p>
        </div>
      </section>
    `;
  }

  const detail = state.detail;
  const model = detailModel(detail);
  if (isMobileViewport()) {
    return `
      <section class="page-shell page-shell--detail">
        <header class="topbar topbar--detail">
          <button type="button" class="back-link" data-action="back-from-detail">返回</button>
          <p class="topbar__title">订单详情</p>
        </header>
        ${renderMobileDetailRows(model.mobileRows)}
      </section>
    `;
  }
  const copyMessage = String(detail.message_long || detail.payload?.message_long || detail.message || "").trim();
  return `
    <section class="page-shell page-shell--detail">
      <header class="topbar topbar--detail">
        <button type="button" class="back-link" data-action="back-from-detail">返回</button>
        <p class="topbar__title">先看这一条</p>
      </header>

      <p class="page-kicker">${escapeHtml(SECTION_LABELS[model.section])}</p>
      <div class="detail-hero">
        <h1 class="page-title page-title--secondary">${escapeHtml(model.headline)}</h1>
        <p class="page-subtitle">${escapeHtml(model.support)}</p>
        ${model.supportNote ? `<p class="detail-note">${escapeHtml(model.supportNote)}</p>` : ""}
      </div>

      ${renderMetricStrip(model.metrics)}

      <section class="info-panel">
        ${renderInfoFields(model.infoFields)}
      </section>

      <button type="button" class="source-toggle" data-action="toggle-source">
        ${state.sourceVisible ? "收起原表内容" : "看原表内容"}
      </button>

      ${state.sourceVisible ? renderSourcePanel() : ""}

      <section class="detail-copy-panel">
        <p>${escapeHtml(copyMessage)}</p>
      </section>
    </section>
  `;
}

function renderAiScreen() {
  if (isMobileViewport()) {
    return `
      <section class="page-shell page-shell--ai">
        <header class="topbar topbar--ai">
          <button type="button" class="back-link" data-action="back-from-ai">返回</button>
          <p class="topbar__title">AI 助手</p>
        </header>
        <div class="ai-thread">
          <article class="ai-bubble ai-bubble--welcome">
            <p class="ai-bubble__lead">老板娘您好，我是您的智能军师。正式能力接入后，我将为您解锁以下技能：</p>
            <ul class="ai-bubble__list">
              <li><strong>提醒翻译：</strong>把复杂提醒讲成人话，直指核心。</li>
              <li><strong>问题归纳：</strong>一键总结客户近期反复出现的异常。</li>
              <li><strong>来龙去脉：</strong>穿透详情与原表字段，拒绝来回翻找。</li>
            </ul>
          </article>
        </div>
        <div class="ai-dock" aria-hidden="true">
          <div class="ai-dock__field">能力正在接入中，敬请期待...</div>
          <button type="button" class="ai-dock__send" disabled>发送</button>
        </div>
      </section>
    `;
  }
  return `
    <section class="page-shell page-shell--ai">
      <header class="topbar">
        <button type="button" class="back-link" data-action="back-from-ai">返回</button>
        <p class="topbar__title">AI 小入口</p>
      </header>
      <div class="page-intro">
        <h1 class="page-title page-title--secondary">AI 入口先留在这里</h1>
        <p class="page-subtitle">这一版先留小入口，不在当前页直接弹聊天框。</p>
      </div>
      <div class="ai-panel">
        <p>后面主要接这几件事：</p>
        <div class="ai-list">
          <div>把提醒讲成人话，直接告诉你为什么它最急。</div>
          <div>把这家客户最近反复出现的问题先归一下。</div>
          <div>把详情和原表字段一起讲清楚，少来回翻。</div>
        </div>
      </div>
    </section>
  `;
}

function renderDownloadModal() {
  if (!state.downloadModalOpen) return "";
  const options = [
    { id: "excel", label: "Excel" },
    { id: "html", label: "HTML" },
    { id: "all", label: "全部" },
  ];
  return `
    <div class="download-backdrop" data-action="close-download">
      <section class="download-modal" role="dialog" aria-modal="true" aria-labelledby="downloadTitle" data-action="download-dialog">
        <div class="download-modal__head">
          <h2 id="downloadTitle">下载</h2>
          <button type="button" class="text-action" data-action="close-download">关闭</button>
        </div>
        <div class="download-choice-grid">
          ${options
            .map(
              (option) => `
                <button
                  type="button"
                  class="download-choice ${state.downloadChoice === option.id ? "is-active" : ""}"
                  data-action="choose-download"
                  data-download-kind="${escapeHtml(option.id)}"
                  ${state.downloadBusy ? "disabled" : ""}
                >
                  ${escapeHtml(option.label)}
                </button>
              `
            )
            .join("")}
        </div>
        <div class="download-modal__actions">
          <button type="button" class="ghost-button" data-action="close-download" ${state.downloadBusy ? "disabled" : ""}>取消</button>
          <button type="button" class="primary-button" data-action="run-download" ${state.downloadBusy ? "disabled" : ""}>
            ${state.downloadBusy ? "下载中" : "下载"}
          </button>
        </div>
      </section>
    </div>
  `;
}

function renderApp() {
  const root = byId("viewerRoot");
  const floatingAi = byId("floatingAiButton");
  if (!root || !floatingAi) return;

  if (state.currentScreen === "detail") {
    root.innerHTML = renderDetailScreen();
  } else if (state.currentScreen === "customer") {
    root.innerHTML = renderCustomerScreen();
  } else if (state.currentScreen === "ai") {
    root.innerHTML = renderAiScreen();
  } else if (state.currentScreen === "quick") {
    root.innerHTML = renderQuickScreen();
  } else if (state.currentScreen === "list") {
    root.innerHTML = renderListScreen();
  } else {
    root.innerHTML = renderOverviewScreen();
  }
  root.insertAdjacentHTML("beforeend", renderDownloadModal());

  floatingAi.hidden = state.currentScreen === "ai";
}

async function registerServiceWorker() {
  const canRegister =
    "serviceWorker" in navigator &&
    INSTALLABLE_PROTOCOLS.has(window.location.protocol) &&
    (window.location.protocol === "https:" ||
      window.location.hostname === "localhost" ||
      window.location.hostname === "127.0.0.1");
  if (!canRegister) return;
  try {
    await navigator.serviceWorker.register(`${appOrigin()}/sw.js`);
  } catch {
    // PWA 不是主链，注册失败不阻塞提醒中心。
  }
}

function bindInstallPrompt() {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPromptEvent = event;
    const button = byId("btnInstallApp");
    if (button) button.hidden = false;
  });
}

async function triggerInstallPrompt() {
  if (!state.installPromptEvent) return;
  await state.installPromptEvent.prompt();
  state.installPromptEvent = null;
  const button = byId("btnInstallApp");
  if (button) button.hidden = true;
}

async function ensureViewerSession() {
  try {
    return await request("/viewer/me");
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      return null;
    }
    throw error;
  }
}

async function loadAlertPools() {
  const [openAlerts, resolvedAlerts] = await Promise.all([
    request("/viewer/alerts?tab=all&state=open"),
    request("/viewer/alerts?tab=all&state=resolved"),
  ]);
  state.openAlerts = Array.isArray(openAlerts) ? openAlerts : [];
  state.resolvedAlerts = Array.isArray(resolvedAlerts) ? resolvedAlerts : [];
}

async function loadBaseUninvoicedCustomerPools() {
  const [openCustomers, resolvedCustomers] = await Promise.all([
    request("/viewer/uninvoiced/customers?state=open"),
    request("/viewer/uninvoiced/customers?state=resolved"),
  ]);
  state.openUninvoicedCustomers = Array.isArray(openCustomers) ? openCustomers : [];
  state.resolvedUninvoicedCustomers = Array.isArray(resolvedCustomers) ? resolvedCustomers : [];
}

function buildUninvoicedCustomerPath(status = state.alertState) {
  const filter = state.filters.uninvoiced;
  const query = new URLSearchParams({ state: status, bucket: filter.bucket || "all" });
  if (filter.customer) query.set("customer", filter.customer);
  return `/viewer/uninvoiced/customers?${query.toString()}`;
}

async function syncUninvoicedCustomerList({ force = false } = {}) {
  if (state.activeSection !== "uninvoiced") return;
  const key = `${state.alertState}|${state.filters.uninvoiced.customer}|${state.filters.uninvoiced.bucket}`;
  if (!force && state.uninvoicedCustomerListKey === key && !state.uninvoicedCustomerListLoading) return;
  state.uninvoicedCustomerListKey = key;
  state.uninvoicedCustomerListLoading = true;
  if (state.currentScreen === "list" && state.uninvoicedView === "customer") {
    renderApp();
  }
  try {
    const items = await request(buildUninvoicedCustomerPath(state.alertState));
    if (state.uninvoicedCustomerListKey === key) {
      state.uninvoicedCustomerList = Array.isArray(items) ? items : [];
    }
  } finally {
    if (state.uninvoicedCustomerListKey === key) {
      state.uninvoicedCustomerListLoading = false;
      if (state.currentScreen === "list" && state.uninvoicedView === "customer") {
        renderApp();
      }
    }
  }
}

async function loadCustomerDetail(customer) {
  const query = new URLSearchParams({
    customer,
    state: state.alertState,
    bucket: state.filters.uninvoiced.bucket || "all",
  });
  return request(`/viewer/uninvoiced/customer-detail?${query.toString()}`);
}

function filenameFromDisposition(disposition, fallbackName) {
  const text = String(disposition || "");
  const utf8Match = text.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const match = text.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallbackName;
}

async function downloadBlob(path, fallbackName) {
  const response = await request(path, { headers: { "X-Role": "upload" } });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filenameFromDisposition(response.headers.get("content-disposition"), fallbackName);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function runUninvoicedDownload() {
  if (state.downloadBusy) return;
  showMessage("appError", "");
  state.downloadBusy = true;
  renderApp();
  try {
    const choice = state.downloadChoice || "all";
    if (choice === "excel" || choice === "all") {
      await downloadBlob("/exports/uninvoiced/excel", "超60天没开票.xlsx");
    }
    if (choice === "html" || choice === "all") {
      await downloadBlob("/exports/uninvoiced/html", "超60天没开票.html");
    }
    state.downloadModalOpen = false;
    showMessage("appError", "已开始下载。", "success");
  } catch (error) {
    showMessage("appError", error.message);
  } finally {
    state.downloadBusy = false;
    renderApp();
  }
}

function screenContext() {
  return {
    screen: state.currentScreen,
    section: state.activeSection,
    uninvoicedView: state.uninvoicedView,
    customer: state.currentCustomer,
    dueSegment: state.dueSegment,
    alertState: state.alertState,
    scrollY: window.scrollY,
  };
}

function restoreContext(context) {
  if (!context) {
    state.currentScreen = "overview";
    renderApp();
    scrollToTop();
    return;
  }
  state.currentScreen = context.screen || "overview";
  state.activeSection = context.section || state.activeSection;
  state.uninvoicedView = context.uninvoicedView || state.uninvoicedView;
  state.currentCustomer = context.customer ?? "";
  state.dueSegment = context.dueSegment || state.dueSegment;
  state.alertState = context.alertState || state.alertState;
  renderApp();
  restoreScroll(context.scrollY || 0);
}

function pushReturnContext() {
  state.returnStack.push(screenContext());
}

function restorePreviousContext() {
  restoreContext(state.returnStack.pop() || null);
}

function clearReturnStack() {
  state.returnStack = [];
}

function goOverview() {
  clearReturnStack();
  saveCurrentScroll();
  state.currentScreen = "overview";
  renderApp();
  restoreScroll(state.scrollPositions.overview || 0);
}

function openQuick(section) {
  clearReturnStack();
  saveCurrentScroll();
  state.activeSection = section;
  if (section === "uninvoiced") {
    state.uninvoicedView = "customer";
  }
  state.alertState = "open";
  state.currentScreen = "quick";
  renderApp();
  scrollToTop();
}

function closeQuick() {
  clearReturnStack();
  state.currentScreen = "overview";
  renderApp();
  restoreScroll(state.scrollPositions.overview || 0);
}

async function openList(section) {
  clearReturnStack();
  saveCurrentScroll();
  state.activeSection = section;
  if (section === "uninvoiced") {
    state.uninvoicedView = "customer";
  }
  state.alertState = "open";
  state.currentScreen = "list";
  if (section === "uninvoiced" && state.uninvoicedView === "customer") {
    await syncUninvoicedCustomerList({ force: true });
  }
  renderApp();
  scrollToTop();
}

function closeList() {
  clearReturnStack();
  state.currentScreen = "quick";
  renderApp();
  restoreScroll(state.scrollPositions.quick || 0);
}

function markAlertReadLocally(alertId) {
  const mutate = (items) =>
    items.map((item) =>
      item.id === alertId
        ? {
            ...item,
            is_unread_change: false,
            change_label: null,
          }
        : item
    );
  state.openAlerts = mutate(state.openAlerts);
  state.resolvedAlerts = mutate(state.resolvedAlerts);
  if (state.detail?.id === alertId) {
    state.detail = { ...state.detail, is_unread_change: false, change_label: null };
  }
  if (state.customerDetail?.items?.length) {
    state.customerDetail = {
      ...state.customerDetail,
      items: mutate(state.customerDetail.items),
    };
  }
}

async function openDetail(alertId, { markRead = true } = {}) {
  pushReturnContext();
  state.selectedAlertId = alertId;
  state.detail = await request(`/viewer/alerts/${encodeURIComponent(alertId)}`);
  state.activeSection = state.detail.alert_type === "due_before_ship" ? "unshipped" : "uninvoiced";
  state.source = null;
  state.sourceVisible = false;
  state.currentScreen = "detail";
  renderApp();
  scrollToTop();

  if (markRead) {
    try {
      await request(`/viewer/alerts/${encodeURIComponent(alertId)}/read`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      markAlertReadLocally(alertId);
      renderApp();
    } catch {
      // 已读留痕失败不阻塞详情查看。
    }
  }
}

async function openLeadUninvoicedDetail(customer) {
  const detail = await loadCustomerDetail(customer);
  const items = Array.isArray(detail.items) ? detail.items.map(decorateAlert) : [];
  const target = items[0];
  if (!target?.id) {
    throw new Error("这个客户当前没有可查看的单子。");
  }
  await openDetail(target.id, { markRead: true });
}

async function openCustomer(customer) {
  pushReturnContext();
  state.currentCustomer = customer;
  state.customerDetail = null;
  state.currentScreen = "customer";
  renderApp();
  scrollToTop();
  state.customerDetail = await loadCustomerDetail(customer);
  renderApp();
}

function closeCustomer() {
  restorePreviousContext();
}

function closeDetail() {
  state.sourceVisible = false;
  state.source = null;
  restorePreviousContext();
}

async function toggleSourcePanel() {
  if (!state.detail?.has_source_row) return;
  if (state.sourceVisible) {
    state.sourceVisible = false;
    renderApp();
    return;
  }
  state.sourceVisible = true;
  renderApp();
  if (!state.source) {
    state.source = await request(`/viewer/alerts/${encodeURIComponent(state.selectedAlertId)}/source-row`);
    renderApp();
  }
}

async function toggleAlertState() {
  state.alertState = state.alertState === "open" ? "resolved" : "open";
  if (state.activeSection === "uninvoiced" && state.currentScreen === "list" && state.uninvoicedView === "customer") {
    await syncUninvoicedCustomerList({ force: true });
  }
  if (state.currentScreen === "customer" && state.currentCustomer) {
    try {
      state.customerDetail = await loadCustomerDetail(state.currentCustomer);
    } catch (error) {
      state.currentScreen = "list";
      state.customerDetail = null;
      throw error;
    }
  }
  renderApp();
  scrollToTop();
}

function openAiFromCurrentScreen() {
  pushReturnContext();
  state.currentScreen = "ai";
  renderApp();
  scrollToTop();
}

function closeAi() {
  restorePreviousContext();
}

async function refreshApp({ preserveDetail = true } = {}) {
  showMessage("appError", "");
  await Promise.all([loadAlertPools(), loadBaseUninvoicedCustomerPools()]);
  if (preserveDetail && state.currentScreen === "detail" && state.selectedAlertId) {
    try {
      state.detail = await request(`/viewer/alerts/${encodeURIComponent(state.selectedAlertId)}`);
      if (state.sourceVisible && state.detail.has_source_row) {
        state.source = await request(`/viewer/alerts/${encodeURIComponent(state.selectedAlertId)}/source-row`);
      }
    } catch (error) {
      state.currentScreen = "overview";
      state.selectedAlertId = "";
      state.detail = null;
      state.source = null;
      state.sourceVisible = false;
      showMessage("appError", error.message);
    }
  }
  if (state.currentScreen === "customer" && state.currentCustomer) {
    try {
      state.customerDetail = await loadCustomerDetail(state.currentCustomer);
    } catch (error) {
      state.currentScreen = "list";
      state.customerDetail = null;
      showMessage("appError", error.message);
    }
  }
  if (state.currentScreen === "list" && state.activeSection === "uninvoiced" && state.uninvoicedView === "customer") {
    await syncUninvoicedCustomerList({ force: true });
  }
  renderApp();
}

async function logoutViewer() {
  try {
    await request("/viewer/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    });
  } finally {
    window.location.replace(loginHref());
  }
}

function startAutoRefresh() {
  if (state.autoRefreshTimer) window.clearInterval(state.autoRefreshTimer);
  state.autoRefreshTimer = window.setInterval(() => {
    refreshApp({ preserveDetail: true }).catch(() => {});
  }, 45000);
}

async function bootLoginPage() {
  bindInstallPrompt();
  await registerServiceWorker();
  const session = await ensureViewerSession();
  if (session) {
    window.location.replace(appHref());
    return;
  }

  const form = byId("loginForm");
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      showMessage("loginError", "");
      const phone = byId("loginPhone")?.value?.trim() || "";
      const password = byId("loginPassword")?.value || "";
      if (!phone || !password) {
        showMessage("loginError", "请先输入手机号和密码。");
        return;
      }
      const button = byId("btnLogin");
      if (button) button.disabled = true;
      try {
        await request("/viewer/auth/login", {
          method: "POST",
          body: JSON.stringify({ phone, password, device: viewerDeviceInfo() }),
        });
        window.location.replace(appHref());
      } catch (error) {
        showMessage("loginError", error.message);
      } finally {
        if (button) button.disabled = false;
      }
    });
  }

  byId("btnInstallApp")?.addEventListener("click", () => {
    triggerInstallPrompt().catch(() => {});
  });
}

function bindAppInteractions() {
  const root = byId("viewerRoot");

  root?.addEventListener("click", (event) => {
    const actionNode = event.target.closest("[data-action]");
    if (!actionNode) return;
    const action = actionNode.dataset.action;

    if (action === "refresh-app") {
      refreshApp({ preserveDetail: true }).catch((error) => showMessage("appError", error.message));
      return;
    }
    if (action === "logout") {
      logoutViewer().catch(() => {});
      return;
    }
    if (action === "open-download") {
      if (!hasUninvoicedDownloadData()) {
        showMessage("appError", "当前没有超60天没开票数据");
        return;
      }
      state.downloadModalOpen = true;
      state.downloadChoice = state.downloadChoice || "all";
      renderApp();
      return;
    }
    if (action === "download-dialog") {
      return;
    }
    if (action === "close-download") {
      if (!state.downloadBusy) {
        state.downloadModalOpen = false;
        renderApp();
      }
      return;
    }
    if (action === "choose-download") {
      const kind = actionNode.dataset.downloadKind;
      if (kind === "excel" || kind === "html" || kind === "all") {
        state.downloadChoice = kind;
        renderApp();
      }
      return;
    }
    if (action === "run-download") {
      runUninvoicedDownload().catch((error) => showMessage("appError", error.message));
      return;
    }
    if (action === "open-quick") {
      const section = actionNode.dataset.section;
      if (section === "unshipped" || section === "uninvoiced") {
        openQuick(section);
      }
      return;
    }
    if (action === "back-to-overview") {
      closeQuick();
      return;
    }
    if (action === "open-list") {
      const section = actionNode.dataset.section || state.activeSection;
      if (section === "unshipped" || section === "uninvoiced") {
        openList(section).catch((error) => showMessage("appError", error.message));
      }
      return;
    }
    if (action === "back-to-quick") {
      closeList();
      return;
    }
    if (action === "switch-due-segment") {
      const segment = actionNode.dataset.segment;
      if (segment === "overdue" || segment === "upcoming") {
        state.dueSegment = segment;
        renderApp();
        scrollToTop();
      }
      return;
    }
    if (action === "switch-uninvoiced-view") {
      const nextView = actionNode.dataset.view;
      if (nextView === "customer" || nextView === "order") {
        state.uninvoicedView = nextView;
        if (nextView === "customer") {
          syncUninvoicedCustomerList({ force: true }).catch((error) => showMessage("appError", error.message));
        }
        renderApp();
        scrollToTop();
      }
      return;
    }
    if (action === "switch-bucket") {
      currentFilterState().bucket = actionNode.dataset.bucket || "all";
       if (state.activeSection === "uninvoiced" && state.currentScreen === "list" && state.uninvoicedView === "customer") {
        syncUninvoicedCustomerList({ force: true }).catch((error) => showMessage("appError", error.message));
      }
      renderApp();
      scrollToTop();
      return;
    }
    if (action === "toggle-alert-state") {
      toggleAlertState().catch((error) => showMessage("appError", error.message));
      return;
    }
    if (action === "open-customer") {
      const customer = actionNode.dataset.customer || "";
      if (customer) {
        openCustomer(customer).catch((error) => showMessage("appError", error.message));
      }
      return;
    }
    if (action === "open-uninvoiced-lead-detail") {
      const customer = actionNode.dataset.customer || "";
      if (customer) {
        openLeadUninvoicedDetail(customer).catch((error) => showMessage("appError", error.message));
      }
      return;
    }
    if (action === "back-from-customer") {
      closeCustomer();
      return;
    }
    if (action === "open-detail") {
      const alertId = actionNode.dataset.alertId || "";
      if (alertId) {
        openDetail(alertId, { markRead: true }).catch((error) => showMessage("appError", error.message));
      }
      return;
    }
    if (action === "back-from-detail") {
      closeDetail();
      return;
    }
    if (action === "toggle-source") {
      toggleSourcePanel().catch((error) => showMessage("appError", error.message));
      return;
    }
    if (action === "back-from-ai") {
      closeAi();
    }
  });

  root?.addEventListener("input", (event) => {
    if (event.target.matches('[data-role="customer-filter"]')) {
      const filter = currentFilterState();
      filter.draftCustomer = event.target.value;
    }
  });

  root?.addEventListener("change", (event) => {
    if (event.target.matches('[data-role="customer-filter"]')) {
      const filter = currentFilterState();
      filter.customer = filter.draftCustomer.trim();
      if (state.activeSection === "uninvoiced" && state.currentScreen === "list" && state.uninvoicedView === "customer") {
        syncUninvoicedCustomerList({ force: true }).catch((error) => showMessage("appError", error.message));
      }
      renderApp();
    }
  });

  root?.addEventListener("keydown", (event) => {
    if (event.target.matches('[data-role="customer-filter"]') && event.key === "Enter") {
      event.preventDefault();
      const filter = currentFilterState();
      filter.customer = filter.draftCustomer.trim();
      if (state.activeSection === "uninvoiced" && state.currentScreen === "list" && state.uninvoicedView === "customer") {
        syncUninvoicedCustomerList({ force: true }).catch((error) => showMessage("appError", error.message));
      }
      renderApp();
    }
  });

  byId("floatingAiButton")?.addEventListener("click", () => {
    openAiFromCurrentScreen();
  });
}

async function bootAppPage() {
  const session = await ensureViewerSession();
  if (!session) {
    window.location.replace(loginHref());
    return;
  }

  state.profile = session;
  await Promise.all([loadAlertPools(), loadBaseUninvoicedCustomerPools()]);
  renderApp();
  bindAppInteractions();
  startAutoRefresh();
  registerServiceWorker().catch(() => {});
}

document.addEventListener("DOMContentLoaded", () => {
  if (isLoginPage()) {
    bootLoginPage().catch((error) => showMessage("loginError", error.message));
    return;
  }
  if (isAppPage()) {
    bootAppPage().catch((error) => showMessage("appError", error.message));
  }
});
