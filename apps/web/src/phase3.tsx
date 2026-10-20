import { useEffect, useState } from "react";
import {
  CheckItem,
  CLASS_LABEL,
  MonthCalendar,
  PeriodReport,
  Plan,
  PlanCompare,
  Scenario,
  StressResult,
  api,
  money,
  pct,
} from "./api";

export function TradeChecklist({ onChange }: { onChange: (ids: string[], ready: boolean) => void }) {
  const [items, setItems] = useState<CheckItem[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  useEffect(() => {
    api.checklist().then((payload) => {
      setItems(payload.items);
      onChange([], false);
    }).catch(() => undefined);
  }, []);
  const toggle = (id: string) => {
    const next = checked.includes(id) ? checked.filter((item) => item !== id) : [...checked, id];
    setChecked(next);
    onChange(next, items.length > 0 && items.every((item) => next.includes(item.id)));
  };
  return (
    <div>
      <p className="hint">提交前勾选全部检查项。这些确认会留在这笔委托上。</p>
      {items.map((item) => (
        <label key={item.id} style={{ marginTop: 6 }}>
          <span><input type="checkbox" checked={checked.includes(item.id)} onChange={() => toggle(item.id)} /> {item.text}</span>
        </label>
      ))}
    </div>
  );
}

export function PlanCompareCard({ plans }: { plans: Plan[] }) {
  const active = plans.filter((plan) => plan.status === "active");
  const [left, setLeft] = useState(active[0]?.id || 0);
  const [right, setRight] = useState(active[1]?.id || active[0]?.id || 0);
  const [result, setResult] = useState<PlanCompare | null>(null);
  const [error, setError] = useState("");
  if (active.length < 2) return null;
  return (
    <section className="card">
      <h2>方案并排</h2>
      <p className="hint">对照两套目标权重。这里不会切换正在生效的方案。</p>
      <form
        className="row"
        style={{ marginTop: 12 }}
        onSubmit={(event) => {
          event.preventDefault();
          api.comparePlans(left, right).then(setResult).catch((reason: Error) => setError(reason.message));
        }}
      >
        <label>左侧
          <select value={left} onChange={(event) => setLeft(Number(event.target.value))}>
            {active.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}
          </select>
        </label>
        <label>右侧
          <select value={right} onChange={(event) => setRight(Number(event.target.value))}>
            {active.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}
          </select>
        </label>
        <button className="primary">对照</button>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <p className="hint" style={{ marginTop: 12 }}>{result.note}</p>
          <table>
            <thead><tr><th>资产</th><th>{result.left.name}</th><th>{result.right.name}</th><th>差值</th></tr></thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={`${row.name}-${row.asset_class}`}>
                  <td>{row.name}<div className="tag">{CLASS_LABEL[row.asset_class]}</div></td>
                  <td>{pct(row.left_weight)}</td>
                  <td>{pct(row.right_weight)}</td>
                  <td>{pct(row.delta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

export function Reports() {
  const [period, setPeriod] = useState("month");
  const [anchor, setAnchor] = useState("2026-10-19");
  const [report, setReport] = useState<PeriodReport | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [stress, setStress] = useState<StressResult | null>(null);
  const [custom, setCustom] = useState({ start: "2020-01-20", end: "2020-03-23", name: "自定义情景" });
  const [error, setError] = useState("");
  useEffect(() => { api.scenarios().then((payload) => setScenarios(payload.scenarios)).catch(() => undefined); }, []);
  const runStress = (name: string, start: string, end: string) => {
    setError("");
    api.stress({ name, start, end }).then(setStress).catch((reason: Error) => setError(reason.message));
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>报告</h1>
          <p>周报和月报回顾这段时间的收益、基准、交易、再平衡和归因。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <section className="card">
        <form
          className="row"
          onSubmit={(event) => {
            event.preventDefault();
            api.report(period, anchor).then(setReport).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>周期
            <select value={period} onChange={(event) => setPeriod(event.target.value)}>
              <option value="week">周报</option>
              <option value="month">月报</option>
            </select>
          </label>
          <label>日期<input type="date" value={anchor} onChange={(event) => setAnchor(event.target.value)} /></label>
          <button className="primary">生成</button>
        </form>
        {report && (
          <>
            <p className="hint" style={{ marginTop: 12 }}>{report.start} 至 {report.end}。{report.note}</p>
            <div className="stats" style={{ marginTop: 12 }}>
              <div className="stat"><span>组合</span><b>{pct(report.portfolio_return)}</b></div>
              <div className="stat"><span>基准</span><b>{pct(report.benchmark_return)}</b></div>
              <div className="stat"><span>超额</span><b>{pct(report.excess)}</b></div>
              <div className="stat"><span>区间回撤</span><b>{pct(report.max_drawdown)}</b></div>
            </div>
            <p className="hint">再平衡：新开 {report.rebalance.opened}，执行 {report.rebalance.executed}，忽略 {report.rebalance.ignored}。快照 {report.snapshot_days} 天。</p>
            {report.attribution && <p className="hint">归因：配置 {pct(report.attribution.allocation)}，选择 {pct(report.attribution.selection)}，残差 {pct(report.attribution.residual)}。</p>}
            {report.trades.map((trade) => (
              <p key={`${trade.date}-${trade.name}-${trade.side}`} style={{ marginTop: 8 }}>{trade.date} · {trade.side === "buy" ? "买入" : "卖出"} {trade.name} · {trade.reason || (trade.temporary ? "临时决策" : "没有理由")}</p>
            ))}
            {!report.trades.length && <p className="hint">这段时间没有委托。</p>}
          </>
        )}
      </section>
      <section className="card">
        <h2>历史情景</h2>
        <p className="hint">用一段历史涨跌重放当前持仓。结果不写入成交和快照。</p>
        <div className="actions" style={{ marginTop: 12 }}>
          {scenarios.map((item) => (
            <button key={item.id} className="ghost" onClick={() => runStress(item.name, item.start, item.end)}>{item.name}</button>
          ))}
        </div>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            runStress(custom.name, custom.start, custom.end);
          }}
        >
          <label>名称<input value={custom.name} onChange={(event) => setCustom({ ...custom, name: event.target.value })} /></label>
          <label>开始<input type="date" value={custom.start} onChange={(event) => setCustom({ ...custom, start: event.target.value })} /></label>
          <label>结束<input type="date" value={custom.end} onChange={(event) => setCustom({ ...custom, end: event.target.value })} /></label>
          <button className="primary">重放</button>
        </form>
        {stress && (
          <>
            <p className="hint" style={{ marginTop: 12 }}>{stress.disclaimer}</p>
            <div className="stats" style={{ marginTop: 12 }}>
              <div className="stat"><span>当前总资产</span><b>{money(stress.current_total)}</b></div>
              <div className="stat"><span>情景后</span><b>{money(stress.stressed_total)}</b></div>
              <div className="stat"><span>变动</span><b>{pct(stress.change)}</b></div>
            </div>
            <table>
              <thead><tr><th>资产</th><th>当前</th><th>冲击</th><th>情景后</th><th>依据</th></tr></thead>
              <tbody>
                {stress.rows.map((row) => (
                  <tr key={row.name}><td>{row.name}</td><td>{money(row.current)}</td><td>{pct(row.shock)}</td><td>{money(row.stressed)}</td><td>{row.source}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </>
  );
}

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

export function CalendarPage() {
  const today = new Date();
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() + 1 });
  const [data, setData] = useState<MonthCalendar | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.calendar(cursor.year, cursor.month).then(setData).catch((reason: Error) => setError(reason.message));
  }, [cursor.year, cursor.month]);
  const shift = (step: number) => {
    const next = new Date(cursor.year, cursor.month - 1 + step, 1);
    setCursor({ year: next.getFullYear(), month: next.getMonth() + 1 });
  };
  const leading = data ? (data.days[0]?.weekday ?? 0) : 0;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>投资日历</h1>
          <p>交易日、委托、除息、快照和偏离提醒放在同一个月里。</p>
        </div>
        <div className="actions">
          <button className="ghost" onClick={() => shift(-1)}>上个月</button>
          <button className="ghost" onClick={() => shift(1)}>下个月</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <section className="card">
        <h2>{cursor.year} 年 {cursor.month} 月</h2>
        <div className="month" style={{ marginTop: 12 }}>
          {WEEKDAYS.map((day) => <div key={day} className="tag">{day}</div>)}
          {Array.from({ length: leading }, (_, index) => <div key={`pad-${index}`} />)}
          {data?.days.map((day) => (
            <article key={day.date} className={day.trading ? "day" : "day off"}>
              <strong>{Number(day.date.slice(-2))}</strong>
              {day.events.map((event) => <p key={`${day.date}-${event.kind}-${event.text}`}>{event.text}</p>)}
            </article>
          ))}
        </div>
      </section>
    </>
  );
}

export function PasswordCard() {
  const [enabled, setEnabled] = useState(false);
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [items, setItems] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api.authStatus().then((status) => setEnabled(status.enabled)).catch(() => undefined);
    api.checklist().then((payload) => setItems(payload.items.map((item) => item.text).join("\n"))).catch(() => undefined);
  }, []);
  return (
    <>
      <section className="card">
        <h2>访问密码</h2>
        <p className="hint">密码是可选项。不设置时，本机打开就能用。设置后，这次浏览器会话需要先输入密码。服务仍然只给本机用。</p>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.setPassword(password, current).then((status) => {
              setEnabled(status.enabled);
              setCurrent("");
              setPassword("");
              if (status.enabled) {
                sessionStorage.removeItem("sandbox-token");
                window.dispatchEvent(new Event("sandbox-locked"));
              }
              setMessage(status.enabled ? "密码已更新，请用新密码重新进入。" : "访问密码已关闭。");
            }).catch((reason: Error) => setError(reason.message));
          }}
        >
          {enabled && <label>当前密码<input type="password" value={current} onChange={(event) => setCurrent(event.target.value)} /></label>}
          <label>{enabled ? "新密码，留空则关闭" : "设置密码"}<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <button className="primary" disabled={!enabled && password.length < 4}>{password || !enabled ? "保存密码" : "关闭密码"}</button>
        </form>
      </section>
      <section className="card">
        <h2>交易前检查</h2>
        <p className="hint">一行一条。下单、按目标建仓和执行再平衡提醒时都要全部勾选。</p>
        <form
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.saveChecklist(items.split("\n")).then((payload) => {
              setItems(payload.items.map((item) => item.text).join("\n"));
              setMessage("检查项已保存。");
            }).catch((reason: Error) => setError(reason.message));
          }}
        >
          <textarea value={items} onChange={(event) => setItems(event.target.value)} />
          <button className="ghost" style={{ marginTop: 8 }}>保存检查项</button>
        </form>
        {message && <p style={{ marginTop: 10 }}>{message}</p>}
        {error && <p className="error">{error}</p>}
      </section>
    </>
  );
}

export function LockScreen({ onUnlock }: { onUnlock: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  return (
    <main>
      <section className="card" style={{ maxWidth: 420, margin: "12vh auto" }}>
        <h2>配置沙盘</h2>
        <p className="hint">这台练习账本设了访问密码。</p>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.login(password).then((result) => {
              sessionStorage.setItem("sandbox-token", result.token);
              onUnlock();
            }).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label className="grow">密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <button className="primary">进入</button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
    </main>
  );
}
