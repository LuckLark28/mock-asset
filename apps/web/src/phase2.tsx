import { useEffect, useState } from "react";
import {
  Attribution,
  BacktestResult,
  Bootstrap,
  CLASS_LABEL,
  PracticeSettings,
  RebalanceTask,
  TaskStats,
  api,
  apiPath,
  pct,
} from "./api";
import { NavChart } from "./chart";
import { TradeChecklist } from "./phase3";

export function Review() {
  const [start, setStart] = useState("2026-01-01");
  const [end, setEnd] = useState("2026-10-19");
  const [report, setReport] = useState<Attribution | null>(null);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [tasks, setTasks] = useState<RebalanceTask[]>([]);
  const [reason, setReason] = useState("");
  const [checks, setChecks] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const loadTasks = () => api.tasks().then((payload) => { setStats(payload.stats); setTasks(payload.tasks); });
  useEffect(() => { loadTasks().catch((reason: Error) => setError(reason.message)); }, []);
  const open = tasks.filter((task) => task.status === "open");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>复盘</h1>
          <p>归因使用目标权重，不是基准里的权重。提醒忽略后，偏离再扩大 2 个百分点才会重新出现。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <section className="card">
        <h2>简化 Brinson</h2>
        <form
          className="row"
          onSubmit={(event) => {
            event.preventDefault();
            setError("");
            api.attribution(start, end).then(setReport).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>开始<input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
          <label>结束<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
          <button className="primary">计算</button>
          <a className="button ghost" href={apiPath(`/api/attribution.csv?start=${start}&end=${end}`)}>导出</a>
        </form>
        {report && (
          <>
            <p className="hint" style={{ marginTop: 12 }}>{report.start} 至 {report.end}。组合 {pct(report.portfolio_return)}，基准 {pct(report.benchmark_return)}，超额 {pct(report.excess)}。残差 {pct(report.residual)}。</p>
            <table>
              <thead><tr><th>类别</th><th>实际权重</th><th>目标权重</th><th>类别收益</th><th>类别基准</th><th>配置</th><th>选择</th></tr></thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.asset_class}>
                    <td>{CLASS_LABEL[row.asset_class] || row.asset_class}<div className="tag">{row.benchmark_name}</div></td>
                    <td>{pct(row.actual_weight)}</td>
                    <td>{pct(row.target_weight)}</td>
                    <td>{pct(row.actual_return)}</td>
                    <td>{pct(row.benchmark_return)}</td>
                    <td>{pct(row.allocation)}</td>
                    <td>{pct(row.selection)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint">{report.note}</p>
          </>
        )}
      </section>
      <section className="card">
        <h2>再平衡提醒</h2>
        <p className="hint">未处理 {stats?.open || 0}，已生成委托 {stats?.executed || 0}，已忽略 {stats?.ignored || 0}。生成的是下一交易日收盘才成交的委托。</p>
        <label className="grow" style={{ marginTop: 12 }}>处理说明
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="忽略时这是理由；执行时这是这笔委托的决策理由。" />
        </label>
        <div style={{ marginTop: 10 }}><TradeChecklist onChange={(ids, ok) => { setChecks(ids); setReady(ok); }} /></div>
        {open.map((task) => (
          <article key={task.id} style={{ marginTop: 14 }}>
            <strong>{task.name}</strong>
            <p className="tag">{task.opened_on} · 偏离 {pct(task.deviation)} · {task.instrument_id === null ? "请用出入金处理" : `${Number(task.suggested_amount) > 0 ? "建议买入" : "建议卖出"} ${Math.abs(Number(task.suggested_amount)).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}</p>
            <div className="actions" style={{ marginTop: 8 }}>
              {task.instrument_id !== null && <button className="primary" disabled={!ready} onClick={() => api.executeTask(task.id, reason, checks).then(loadTasks).catch((item: Error) => setError(item.message))}>生成委托</button>}
              <button className="ghost" onClick={() => api.ignoreTask(task.id, reason).then(loadTasks).catch((item: Error) => setError(item.message))}>忽略</button>
            </div>
          </article>
        ))}
        {!open.length && <p className="hint" style={{ marginTop: 12 }}>当前没有待处理的偏离。</p>}
      </section>
    </>
  );
}

export function Sandbox({ boot }: { boot: Bootstrap }) {
  const tradable = boot.instruments.filter((item) => item.tradable);
  const [weights, setWeights] = useState<Record<number, string>>(() => {
    const preset: Record<string, string> = { "510300": "60", "511010": "30", "518880": "10" };
    return Object.fromEntries(tradable.map((item) => [item.id, preset[item.symbol] || "0"]));
  });
  const [form, setForm] = useState({ name: "60/30/10 草稿", start: "2025-01-02", end: "2026-10-16", frequency: "monthly" });
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>沙盘</h1>
          <p>草稿回测只读历史收盘价。它不会写入主账户的成交和快照。</p>
        </div>
      </div>
      <section className="card">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            api.backtest({
              name: form.name,
              start: form.start,
              end: form.end,
              frequency: form.frequency,
              assets: tradable.map((item) => ({
                instrument_id: item.id,
                asset_class: item.asset_class,
                target_weight: String(Number(weights[item.id] || 0) / 100),
              })),
            }).then(setResult).catch((reason: Error) => setError(reason.message)).finally(() => setBusy(false));
          }}
        >
          <div className="row">
            <label className="grow">名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
            <label>开始<input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} /></label>
            <label>结束<input type="date" value={form.end} onChange={(event) => setForm({ ...form, end: event.target.value })} /></label>
            <label>再平衡
              <select value={form.frequency} onChange={(event) => setForm({ ...form, frequency: event.target.value })}>
                <option value="monthly">每月</option>
                <option value="none">不调整</option>
              </select>
            </label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            {tradable.map((item) => (
              <label key={item.id}>{item.name}
                <input value={weights[item.id] || ""} onChange={(event) => setWeights({ ...weights, [item.id]: event.target.value })} />
              </label>
            ))}
          </div>
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="primary" disabled={busy}>跑回测</button>
          </div>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
      {result && (
        <section className="card">
          <h2>{result.name}</h2>
          <p className="hint">{result.disclaimer} {result.start} 至 {result.end}，累计 {pct(result.metrics?.cumulative_return)}。</p>
          <div className="stats" style={{ marginTop: 12 }}>
            <div className="stat"><span>年化</span><b>{pct(result.metrics?.annualized_return)}</b></div>
            <div className="stat"><span>波动率</span><b>{pct(result.metrics?.volatility)}</b></div>
            <div className="stat"><span>最大回撤</span><b>{pct(result.metrics?.max_drawdown)}</b></div>
            <div className="stat"><span>夏普</span><b>{result.metrics?.sharpe ?? "—"}</b></div>
          </div>
          <NavChart series={result.series.map((item) => ({ date: item.date, nav: item.nav, benchmark_nav: null, daily_pnl: "0", cumulative_return: "0", total_value: "0" }))} />
        </section>
      )}
    </>
  );
}

export function PracticeControls({ boot }: { boot: Bootstrap }) {
  const [settings, setSettings] = useState<PracticeSettings | null>(null);
  const [fee, setFee] = useState("0");
  const [rf, setRf] = useState("0");
  const [reinvest, setReinvest] = useState(false);
  const [bar, setBar] = useState({ instrument_id: boot.instruments[0]?.id || 0, trade_date: "2026-10-16", close: "" });
  const [fx, setFx] = useState({ currency: "USD", trade_date: "2026-10-16", rate: "" });
  const [confirm, setConfirm] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api.settings().then((payload) => {
      setSettings(payload);
      setFee(String(Number(payload.fee_rate) * 100));
      setRf(String(Number(payload.risk_free_rate) * 100));
      setReinvest(payload.dividend_reinvest);
    }).catch((reason: Error) => setError(reason.message));
  }, []);
  return (
    <>
      <section className="card">
        <h2>费用、分红与指标</h2>
        <p className="hint">手续费只影响之后的成交。分红再投资从打开的当天起生效，现金分红仍是默认。无风险利率用于夏普，默认 0。</p>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.saveSettings({
              fee_rate: String(Number(fee) / 100),
              risk_free_rate: String(Number(rf) / 100),
              dividend_reinvest: reinvest,
            }).then((payload) => { setSettings(payload); setMessage(reinvest ? `再投资从 ${payload.reinvest_since} 起，下一次估值生效。` : "已保存。之后的成交按这个费率计算。"); }).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>手续费 %<input value={fee} onChange={(event) => setFee(event.target.value)} /></label>
          <label>无风险利率 %<input value={rf} onChange={(event) => setRf(event.target.value)} /></label>
          <label>分红再投资
            <select value={reinvest ? "yes" : "no"} onChange={(event) => setReinvest(event.target.value === "yes")}>
              <option value="no">记现金</option>
              <option value="yes">再投资</option>
            </select>
          </label>
          <button className="primary">保存</button>
        </form>
        {message && <p style={{ marginTop: 10 }}>{message}</p>}
      </section>
      <section className="card">
        <h2>类别基准</h2>
        <p className="hint">归因里的类别收益对照这里的标的。组合净值图仍使用方案自己的基准。</p>
        {settings?.class_benchmarks.map((item) => (
          <label key={item.asset_class} style={{ marginTop: 10 }}>{CLASS_LABEL[item.asset_class]}
            <select
              value={item.instrument_id || ""}
              onChange={(event) => api.classBenchmark({ asset_class: item.asset_class, instrument_id: Number(event.target.value) }).then(setSettings).catch((reason: Error) => setError(reason.message))}
            >
              {boot.instruments.map((instrument) => <option key={instrument.id} value={instrument.id}>{instrument.name}</option>)}
            </select>
          </label>
        ))}
      </section>
      <section className="card">
        <h2>补录与汇率</h2>
        <p className="hint">只能补缺失的交易日。补上的价格要重新生成快照才会进入净值。外币按兑人民币汇率折算，汇率会留档。</p>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.repairBar({ instrument_id: bar.instrument_id, trade_date: bar.trade_date, close: bar.close }).then(() => setMessage("已补录。请重新生成快照。")).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>标的
            <select value={bar.instrument_id} onChange={(event) => setBar({ ...bar, instrument_id: Number(event.target.value) })}>
              {boot.instruments.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>日期<input type="date" value={bar.trade_date} onChange={(event) => setBar({ ...bar, trade_date: event.target.value })} /></label>
          <label>收盘价<input value={bar.close} onChange={(event) => setBar({ ...bar, close: event.target.value })} /></label>
          <button className="ghost">补录</button>
        </form>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            api.saveFx(fx).then(() => setMessage("汇率已保存。")).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>外币<input value={fx.currency} onChange={(event) => setFx({ ...fx, currency: event.target.value })} /></label>
          <label>日期<input type="date" value={fx.trade_date} onChange={(event) => setFx({ ...fx, trade_date: event.target.value })} /></label>
          <label>兑人民币<input value={fx.rate} onChange={(event) => setFx({ ...fx, rate: event.target.value })} /></label>
          <button className="ghost">保存汇率</button>
        </form>
      </section>
      <section className="card">
        <h2>备份与恢复</h2>
        <p className="hint">恢复会覆盖当前练习账本。覆盖前会先留下一份当日副本。</p>
        <div className="actions" style={{ marginTop: 12 }}>
          <a className="button ghost" href={apiPath("/api/backup")}>下载备份</a>
        </div>
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(event) => {
            event.preventDefault();
            if (!file) return;
            api.restore(file, confirm).then((payload) => setMessage(`已恢复。原账本留在 ${payload.saved_previous}。请刷新页面。`)).catch((reason: Error) => setError(reason.message));
          }}
        >
          <label>数据库文件<input type="file" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
          <label>确认<input value={confirm} placeholder="输入恢复" onChange={(event) => setConfirm(event.target.value)} /></label>
          <button className="ghost">恢复</button>
        </form>
        {message && <p style={{ marginTop: 10 }}>{message}</p>}
        {error && <p className="error">{error}</p>}
      </section>
    </>
  );
}
