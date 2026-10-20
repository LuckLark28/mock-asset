export class ApiError extends Error {}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("sandbox-token");
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Sandbox-Token": token } : {}),
      ...(options?.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    sessionStorage.removeItem("sandbox-token");
    window.dispatchEvent(new Event("sandbox-locked"));
  }
  if (!response.ok) {
    const detail = data.detail;
    const text = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join("；") : "请求失败";
    throw new ApiError(text);
  }
  return data as T;
}

export const api = {
  bootstrap: () => request<Bootstrap>("/api/bootstrap"),
  dashboard: () => request<Dashboard>("/api/dashboard"),
  orders: () => request<Order[]>("/api/orders"),
  logs: () => request<Order[]>("/api/logs"),
  jobs: () => request<Job[]>("/api/jobs"),
  createAccount: (body: { name: string; initial_cash: string; plan_id: number }) =>
    request("/api/account", { method: "POST", body: JSON.stringify(body) }),
  createPlan: (body: unknown) => request("/api/plans", { method: "POST", body: JSON.stringify(body) }),
  updatePlan: (id: number, body: unknown) => request(`/api/plans/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  activate: (id: number) => request(`/api/plans/${id}/activate`, { method: "POST" }),
  archive: (id: number) => request(`/api/plans/${id}/archive`, { method: "POST" }),
  addInstrument: (body: unknown) => request("/api/instruments", { method: "POST", body: JSON.stringify(body) }),
  order: (body: unknown) => request<OrderResult>("/api/orders", { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: number) => request(`/api/orders/${id}/cancel`, { method: "POST" }),
  cash: (body: unknown) => request("/api/cashflows", { method: "POST", body: JSON.stringify(body) }),
  preview: () => request<Preview>("/api/rebalance/preview", { method: "POST" }),
  submitPreview: (body: unknown) => request("/api/rebalance/submit", { method: "POST", body: JSON.stringify(body) }),
  sync: () => request<{ messages: string[] }>("/api/market/sync", { method: "POST" }),
  valuate: () => request<{ status: string; as_of: string }>("/api/valuation/run", { method: "POST" }),
  settings: () => request<PracticeSettings>("/api/settings"),
  saveSettings: (body: { fee_rate: string; risk_free_rate: string; dividend_reinvest: boolean }) =>
    request<PracticeSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  classBenchmark: (body: { asset_class: string; instrument_id: number }) =>
    request<PracticeSettings>("/api/class-benchmarks", { method: "POST", body: JSON.stringify(body) }),
  repairBar: (body: { instrument_id: number; trade_date: string; close: string }) =>
    request("/api/market/bars", { method: "POST", body: JSON.stringify(body) }),
  saveFx: (body: { currency: string; trade_date: string; rate: string }) =>
    request("/api/fx", { method: "POST", body: JSON.stringify(body) }),
  attribution: (start: string, end: string) => request<Attribution>(`/api/attribution?start=${start}&end=${end}`),
  tasks: () => request<{ stats: TaskStats; tasks: RebalanceTask[] }>("/api/rebalance/tasks"),
  ignoreTask: (id: number, reason: string) =>
    request(`/api/rebalance/tasks/${id}/ignore`, { method: "POST", body: JSON.stringify({ reason }) }),
  executeTask: (id: number, reason: string, checklist: string[]) =>
    request(`/api/rebalance/tasks/${id}/execute`, { method: "POST", body: JSON.stringify({ log: { reason, emotion_tag: "平静" }, checklist }) }),
  backtest: (body: unknown) => request<BacktestResult>("/api/backtests", { method: "POST", body: JSON.stringify(body) }),
  checklist: () => request<{ items: CheckItem[] }>("/api/checklist"),
  saveChecklist: (items: string[]) => request<{ items: CheckItem[] }>("/api/checklist", { method: "PUT", body: JSON.stringify({ items }) }),
  report: (period: string, anchor: string) => request<PeriodReport>(`/api/reports?period=${period}&anchor=${anchor}`),
  comparePlans: (left: number, right: number) => request<PlanCompare>(`/api/plans/compare?left=${left}&right=${right}`),
  calendar: (year: number, month: number) => request<MonthCalendar>(`/api/calendar?year=${year}&month=${month}`),
  scenarios: () => request<{ scenarios: Scenario[] }>("/api/stress/scenarios"),
  stress: (body: { start: string; end: string; name: string }) => request<StressResult>("/api/stress", { method: "POST", body: JSON.stringify(body) }),
  authStatus: () => request<{ enabled: boolean }>("/api/auth/status"),
  login: (password: string) => request<{ token: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  setPassword: (password: string, current: string) =>
    request<{ enabled: boolean }>("/api/auth/password", { method: "POST", body: JSON.stringify({ password, current }) }),
  restore: async (file: File, confirm: string) => {
    const body = new FormData();
    body.append("confirm", confirm);
    body.append("file", file);
    const token = sessionStorage.getItem("sandbox-token");
    const response = await fetch("/api/backup/restore", { method: "POST", body, headers: token ? { "X-Sandbox-Token": token } : {} });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(typeof data.detail === "string" ? data.detail : "恢复失败");
    }
    return data as { ok: boolean; saved_previous: string };
  },
};

export type Instrument = {
  id: number;
  symbol: string;
  name: string;
  asset_class: string;
  exchange: string;
  tradable: boolean;
  lot_size: number;
};

export type PlanAsset = {
  instrument_id: number | null;
  symbol: string;
  name: string;
  asset_class: string;
  target_weight: string;
};

export type Plan = {
  id: number;
  name: string;
  description: string;
  strategy_tag: string;
  benchmark: string;
  rebalance_threshold: string;
  status: string;
  is_primary: boolean;
  effective_on: string | null;
  assets: PlanAsset[];
};

export type Bootstrap = {
  disclaimer: string;
  now: string;
  next_session: string;
  account: { id: number; name: string; created_on: string; base_currency: string } | null;
  plans: Plan[];
  instruments: Instrument[];
};

export type WeightRow = {
  instrument_id: number | null;
  symbol: string;
  name: string;
  asset_class: string;
  quantity?: number | string;
  market_value?: string;
  target_weight: string;
  actual_weight: string;
  deviation: string;
  breached: boolean;
  pnl?: string;
  price?: string | null;
  avg_cost?: string;
  sellable?: number;
};

export type Dashboard = {
  estimate: {
    account?: null;
    cash?: string;
    market_value?: string;
    total?: string;
    threshold?: string;
    weights?: WeightRow[];
    warnings?: string[];
    next_session?: string;
  };
  snapshot: { id: number; as_of: string; status: string; error: string; created_at: string } | null;
  latest: { date: string; nav: string; benchmark_nav: string | null; total_value: string; daily_pnl: string; cumulative_return: string } | null;
  series: { date: string; nav: string; benchmark_nav: string | null; daily_pnl: string; cumulative_return: string; total_value: string }[];
  metrics: {
    annualized_return: string | null;
    volatility: string | null;
    max_drawdown: string | null;
    sharpe: string | null;
    calmar: string | null;
    risk_free_rate: string;
    sample_days: number;
    notes: string[];
  } | null;
  metric_notes: Record<string, string>;
};

export type Order = {
  id: number;
  symbol: string;
  name: string;
  side: string;
  status: string;
  trade_date: string;
  quantity_requested: number | null;
  amount_requested: string | null;
  quantity: number | null;
  price: string | null;
  amount: string | null;
  reject_reason: string;
  is_temporary: boolean;
  source: string;
  submitted_at: string;
  log: {
    reason: string;
    hypothesis: string;
    exit_condition: string;
    confidence: number | null;
    emotion_tag: string;
    skipped: boolean;
  } | null;
  outcome?: { latest_price: string; price_date: string; since_return: string } | null;
};

export type OrderResult = { id: number; status: string; trade_date: string; reject_reason: string };

export type Preview = {
  total: string;
  cash: string;
  session_date: string;
  lines: { instrument_id: number; name: string; symbol: string; side: string; quantity: number; estimate_price: string; estimate_amount: string }[];
};

export type Job = { id: number; kind: string; status: string; message: string; started_at: string };

export const CLASS_LABEL: Record<string, string> = {
  equity: "股票",
  bond: "债券",
  commodity: "商品",
  cash: "现金",
  index: "指数",
};

export type PracticeSettings = {
  fee_rate: string;
  risk_free_rate: string;
  dividend_reinvest: boolean;
  reinvest_since: string | null;
  class_benchmarks: { asset_class: string; instrument_id: number | null; symbol: string; name: string }[];
};

export type Attribution = {
  start: string;
  end: string;
  portfolio_return: string | null;
  benchmark_return: string | null;
  excess: string | null;
  allocation: string | null;
  selection: string | null;
  residual: string | null;
  note: string;
  rows: {
    asset_class: string;
    actual_weight: string;
    target_weight: string;
    actual_return: string;
    benchmark_return: string;
    benchmark_name: string;
    allocation: string;
    selection: string;
  }[];
};

export type TaskStats = { open: number; executed: number; ignored: number };

export type RebalanceTask = {
  id: number;
  name: string;
  asset_class: string;
  instrument_id: number | null;
  opened_on: string;
  deviation: string;
  suggested_amount: string;
  status: string;
  ignore_reason: string;
  extra_threshold: string;
};

export type BacktestResult = {
  name: string;
  start: string;
  end: string;
  frequency: string;
  disclaimer: string;
  metrics: (NonNullable<Dashboard["metrics"]> & { cumulative_return?: string | null }) | null;
  series: { date: string; nav: string }[];
};

export type CheckItem = { id: string; text: string };

export type PeriodReport = {
  period: string;
  start: string;
  end: string;
  portfolio_return: string | null;
  benchmark_return: string | null;
  excess: string | null;
  max_drawdown: string | null;
  snapshot_days: number;
  note: string;
  trades: { date: string; name: string; side: string; status: string; reason: string; temporary: boolean }[];
  rebalance: { opened: number; executed: number; ignored: number };
  attribution: { allocation: string | null; selection: string | null; residual: string | null } | null;
};

export type PlanCompare = {
  left: { id: number; name: string; primary: boolean };
  right: { id: number; name: string; primary: boolean };
  note: string;
  rows: { name: string; asset_class: string; left_weight: string; right_weight: string; delta: string }[];
};

export type MonthCalendar = {
  year: number;
  month: number;
  days: { date: string; weekday: number; trading: boolean; events: { kind: string; text: string }[] }[];
};

export type Scenario = { id: string; name: string; start: string; end: string };

export type StressResult = {
  name: string;
  start: string;
  end: string;
  current_total: string;
  stressed_total: string;
  change: string;
  disclaimer: string;
  rows: { name: string; current: string; shock: string; stressed: string; source: string; covered: boolean }[];
};

export function money(value?: string | number | null) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return "—";
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function pct(value?: string | number | null, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value) * 100;
  if (Number.isNaN(number)) return "—";
  return `${number.toFixed(digits)}%`;
}

export function signed(value?: string | null) {
  if (!value) return "—";
  const number = Number(value);
  const text = money(value);
  return number > 0 ? `+${text}` : text;
}
