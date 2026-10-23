import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import {
  Bootstrap,
  CLASS_LABEL,
  Dashboard,
  Job,
  Order,
  Plan,
  Preview,
  api,
  apiPath,
  money,
  pct,
  signed,
} from "./api";
import { NavChart } from "./chart";
import { PracticeControls, Review, Sandbox } from "./phase2";
import { CalendarPage, LockScreen, PasswordCard, PlanCompareCard, Reports, TradeChecklist } from "./phase3";

export function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState<boolean | null>(null);
  const reload = () => {
    api.bootstrap().then(setBoot).catch((reason: Error) => setError(reason.message));
  };
  useEffect(() => {
    api.authStatus().then((status) => {
      setLocked(status.enabled && !sessionStorage.getItem("sandbox-token"));
    }).catch((reason: Error) => setError(reason.message));
    const onLock = () => setLocked(true);
    window.addEventListener("sandbox-locked", onLock);
    return () => window.removeEventListener("sandbox-locked", onLock);
  }, []);
  useEffect(() => {
    if (locked === false) reload();
  }, [locked]);
  if (locked === null) return <div className="banner">模拟练习，不构成投资建议。</div>;
  if (locked) {
    return (
      <>
        <div className="banner">模拟练习，不构成投资建议。</div>
        <LockScreen onUnlock={() => setLocked(false)} />
      </>
    );
  }
  return (
    <>
      <div className="banner">{boot?.disclaimer || "模拟练习，不构成投资建议。"}</div>
      <div className="shell">
        <nav>
          <div className="mark">
            <div className="seal">练</div>
            <div>
              <strong>配置沙盘</strong>
              <span>个人资产配置练习</span>
            </div>
          </div>
          <NavLink to="/" end>总览</NavLink>
          <NavLink to="/plans">方案</NavLink>
          <NavLink to="/trades">交易</NavLink>
          <NavLink to="/journal">日志</NavLink>
          <NavLink to="/review">复盘</NavLink>
          <NavLink to="/reports">报告</NavLink>
          <NavLink to="/calendar">日历</NavLink>
          <NavLink to="/sandbox">沙盘</NavLink>
          <NavLink to="/settings">设置</NavLink>
        </nav>
        <main>
          {error && <p className="error">{error}</p>}
          {boot && (
            <Routes>
              <Route path="/" element={<Overview boot={boot} reload={reload} />} />
              <Route path="/plans" element={<Plans boot={boot} reload={reload} />} />
              <Route path="/trades" element={<Trades boot={boot} />} />
              <Route path="/journal" element={<Journal />} />
              <Route path="/review" element={<Review />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/calendar" element={<CalendarPage />} />
              <Route path="/sandbox" element={<Sandbox boot={boot} />} />
              <Route path="/settings" element={<Settings boot={boot} />} />
            </Routes>
          )}
        </main>
      </div>
    </>
  );
}

function Overview({ boot, reload }: { boot: Bootstrap; reload: () => void }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const [form, setForm] = useState({ name: "主练习账户", cash: "100000", plan_id: boot.plans.find((plan) => plan.is_primary)?.id || boot.plans[0]?.id });
  const load = () => api.dashboard().then(setData).catch((reason: Error) => setError(reason.message));
  useEffect(() => {
    if (boot.account) load();
  }, [boot.account]);

  if (!boot.account) {
    return (
      <section className="card">
        <h2>建立练习账户</h2>
        <p className="hint">用一笔虚拟资金开始。示例方案是 60% 沪深300ETF、30% 国债ETF、10% 黄金ETF，基准为沪深300。</p>
        <form
          className="row"
          style={{ marginTop: 16 }}
          onSubmit={(event) => {
            event.preventDefault();
            setOpening(true);
            api.createAccount({ name: form.name, initial_cash: form.cash, plan_id: Number(form.plan_id) })
              .then(() => reload())
              .catch((reason: Error) => setError(reason.message))
              .finally(() => setOpening(false));
          }}
        >
          <label>账户名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>初始资金<input value={form.cash} onChange={(event) => setForm({ ...form, cash: event.target.value })} /></label>
          <label>生效方案
            <select value={form.plan_id} onChange={(event) => setForm({ ...form, plan_id: Number(event.target.value) })}>
              {boot.plans.filter((plan) => plan.status === "active").map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}
            </select>
          </label>
          <button className="primary" disabled={opening}>建立</button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
    );
  }

  const latest = data?.latest;
  const pnl = Number(latest?.daily_pnl || 0);
  const ret = Number(latest?.cumulative_return || 0);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{boot.account.name}</h1>
          <p>下一笔委托的成交日是 {boot.next_session}。价格由收盘价决定，不需要手填。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="stats">
        <div className="stat"><span>总资产</span><b>{money(data?.estimate.total)}</b></div>
        <div className="stat"><span>现金</span><b>{money(data?.estimate.cash)}</b></div>
        <div className="stat"><span>最近一日盈亏</span><b className={pnl > 0 ? "up" : pnl < 0 ? "down" : ""}>{signed(latest?.daily_pnl)}</b></div>
        <div className="stat"><span>累计收益</span><b className={ret > 0 ? "up" : ret < 0 ? "down" : ""}>{pct(latest?.cumulative_return)}</b></div>
      </div>
      {data?.metrics && (
        <div className="stats">
          <div className="stat"><span>年化收益</span><b>{pct(data.metrics.annualized_return)}</b></div>
          <div className="stat"><span>波动率</span><b>{pct(data.metrics.volatility)}</b></div>
          <div className="stat"><span>最大回撤</span><b>{pct(data.metrics.max_drawdown)}</b></div>
          <div className="stat"><span>夏普 / Calmar</span><b>{data.metrics.sharpe ?? "—"} / {data.metrics.calmar ?? "—"}</b></div>
        </div>
      )}
      {!!data?.metrics?.notes.length && <p className="hint" style={{ marginBottom: 12 }}>{data.metrics.notes.join("。")}。无风险利率 {pct(data.metrics.risk_free_rate)}。{data.metric_notes.sharpe}</p>}
      <div className="grid-2">
        <section className="card">
          <h2>净值</h2>
          <p className="hint">从账户起始日的 1 开始，按每日收益率连乘。基准同一天对齐。{data?.snapshot ? `最近快照 ${data.snapshot.as_of}。` : "还没有已收盘的练习日。"}</p>
          <NavChart series={data?.series || []} />
        </section>
        <section className="card">
          <h2>权重偏离</h2>
          <p className="hint">超过 ±{pct(data?.estimate.threshold, 0)} 会标出。现金也计入。</p>
          <WeightTable rows={data?.estimate.weights || []} />
          {!!data?.estimate.warnings?.length && <ul className="warn-list">{data.estimate.warnings.map((item) => <li key={item}>{item}</li>)}</ul>}
        </section>
      </div>
    </>
  );
}

function WeightTable({ rows }: { rows: Dashboard["estimate"]["weights"] }) {
  if (!rows?.length) return <p className="hint">建立账户后这里会对比目标和实际权重。</p>;
  return (
    <table>
      <thead><tr><th>资产</th><th>实际</th><th>目标</th><th>偏离</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.symbol}-${row.name}`}>
            <td>{row.name}<div className="bar"><i style={{ width: `${Math.min(100, Math.abs(Number(row.actual_weight)) * 100)}%` }} /></div></td>
            <td>{pct(row.actual_weight)}</td>
            <td>{pct(row.target_weight)}</td>
            <td className={row.breached ? "breach" : ""}>{pct(row.deviation)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Plans({ boot, reload }: { boot: Bootstrap; reload: () => void }) {
  const primary = boot.plans.find((plan) => plan.is_primary) || boot.plans[0];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>配置方案</h1>
          <p>同一时间只有一个生效方案。修改权重从今天起生效，不改写已经生成的快照。</p>
        </div>
      </div>
      <div className="split">
        <section>
          {boot.plans.map((plan) => (
            <article className="card" key={plan.id}>
              <h2>{plan.name}</h2>
              <p className="hint">{plan.strategy_tag} · 基准 {plan.benchmark} · 阈值 {pct(plan.rebalance_threshold, 0)}{plan.is_primary ? " · 生效中" : ""}{plan.status === "archived" ? " · 已归档" : ""}</p>
              <p style={{ margin: "8px 0" }}>{plan.description}</p>
              <table>
                <tbody>
                  {plan.assets.map((asset) => (
                    <tr key={`${plan.id}-${asset.symbol}`}><td>{asset.name}</td><td>{CLASS_LABEL[asset.asset_class]}</td><td>{pct(asset.target_weight, 1)}</td></tr>
                  ))}
                </tbody>
              </table>
              {plan.status === "active" && !plan.is_primary && (
                <div className="actions" style={{ marginTop: 10 }}>
                  <button className="ghost" onClick={() => api.activate(plan.id).then(reload)}>设为生效</button>
                  <button className="ghost" onClick={() => api.archive(plan.id).then(reload)}>归档</button>
                </div>
              )}
            </article>
          ))}
        </section>
        {primary && <PlanEditor boot={boot} plan={primary} reload={reload} />}
      </div>
      <PlanCompareCard plans={boot.plans} />
    </>
  );
}

function PlanEditor({ boot, plan, reload }: { boot: Bootstrap; plan: Plan; reload: () => void }) {
  const tradable = boot.instruments.filter((item) => item.tradable);
  const [name, setName] = useState(plan.name);
  const [tag, setTag] = useState(plan.strategy_tag);
  const [description, setDescription] = useState(plan.description);
  const [threshold, setThreshold] = useState(String(Number(plan.rebalance_threshold) * 100));
  const [rows, setRows] = useState(plan.assets.filter((asset) => asset.asset_class !== "cash").map((asset) => ({ instrument_id: asset.instrument_id || tradable[0].id, percent: String(Number(asset.target_weight) * 100) })));
  const [cash, setCash] = useState(String(Number(plan.assets.find((asset) => asset.asset_class === "cash")?.target_weight || 0) * 100));
  const [error, setError] = useState("");
  const [symbol, setSymbol] = useState("");
  const [instrumentName, setInstrumentName] = useState("");
  const [assetClass, setAssetClass] = useState("equity");
  const sum = rows.reduce((total, row) => total + Number(row.percent || 0), 0) + Number(cash || 0);

  useEffect(() => {
    setName(plan.name);
    setTag(plan.strategy_tag);
    setDescription(plan.description);
    setThreshold(String(Number(plan.rebalance_threshold) * 100));
    setRows(plan.assets.filter((asset) => asset.asset_class !== "cash").map((asset) => ({ instrument_id: asset.instrument_id || tradable[0].id, percent: String(Number(asset.target_weight) * 100) })));
    setCash(String(Number(plan.assets.find((asset) => asset.asset_class === "cash")?.target_weight || 0) * 100));
  }, [plan.id, plan.effective_on]);

  const payload = () => ({
    name,
    description,
    strategy_tag: tag,
    rebalance_threshold: String(Number(threshold) / 100),
    assets: [
      ...rows.map((row) => ({ instrument_id: Number(row.instrument_id), asset_class: "equity", target_weight: String(Number(row.percent) / 100) })),
      ...(Number(cash) > 0 ? [{ instrument_id: null, asset_class: "cash", target_weight: String(Number(cash) / 100) }] : []),
    ],
  });

  return (
    <section className="card">
      <h2>编辑生效方案</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          api.updatePlan(plan.id, payload()).then(() => reload()).catch((reason: Error) => setError(reason.message));
        }}
      >
        <div className="row">
          <label className="grow">名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>标签<input value={tag} onChange={(event) => setTag(event.target.value)} /></label>
          <label>偏离阈值 %<input value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
        </div>
        <label style={{ marginTop: 10 }}>说明<textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {rows.map((row, index) => (
          <div className="row" key={index} style={{ marginTop: 8 }}>
            <label className="grow">标的
              <select value={row.instrument_id} onChange={(event) => setRows(rows.map((item, itemIndex) => itemIndex === index ? { ...item, instrument_id: Number(event.target.value) } : item))}>
                {tradable.map((item) => <option key={item.id} value={item.id}>{item.name} {item.symbol}</option>)}
              </select>
            </label>
            <label>权重 %<input value={row.percent} onChange={(event) => setRows(rows.map((item, itemIndex) => itemIndex === index ? { ...item, percent: event.target.value } : item))} /></label>
          </div>
        ))}
        <div className="row" style={{ marginTop: 8 }}>
          <label>现金权重 %<input value={cash} onChange={(event) => setCash(event.target.value)} /></label>
          <button type="button" className="ghost" onClick={() => setRows([...rows, { instrument_id: tradable[0].id, percent: "0" }])}>加一行</button>
        </div>
        <p className={Math.abs(sum - 100) > 0.01 ? "error" : "hint"} style={{ marginTop: 8 }}>权重合计 {sum.toFixed(2)}%</p>
        {error && <p className="error">{error}</p>}
        <div className="actions" style={{ marginTop: 8 }}>
          <button className="primary">保存为今日起生效</button>
          <button type="button" className="ghost" onClick={() => api.createPlan({ ...payload(), name: `${name} 副本` }).then(() => reload()).catch((reason: Error) => setError(reason.message))}>另存为新方案</button>
        </div>
      </form>
      <form
        className="row"
        style={{ marginTop: 18 }}
        onSubmit={(event) => {
          event.preventDefault();
          api.addInstrument({ symbol, name: instrumentName, asset_class: assetClass, exchange: symbol.startsWith("159") || symbol.startsWith("16") ? "SZ" : "SH" })
            .then(() => { setSymbol(""); setInstrumentName(""); reload(); })
            .catch((reason: Error) => setError(reason.message));
        }}
      >
        <label>新代码<input value={symbol} onChange={(event) => setSymbol(event.target.value)} /></label>
        <label>名称<input value={instrumentName} onChange={(event) => setInstrumentName(event.target.value)} /></label>
        <label>类别
          <select value={assetClass} onChange={(event) => setAssetClass(event.target.value)}>
            <option value="equity">股票</option>
            <option value="bond">债券</option>
            <option value="commodity">商品</option>
          </select>
        </label>
        <button className="ghost">加入标的</button>
      </form>
    </section>
  );
}

function Trades({ boot }: { boot: Bootstrap }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [log, setLog] = useState({ reason: "", hypothesis: "", exit_condition: "", confidence: "", emotion_tag: "", skip: false, confirm_skip: false });
  const [order, setOrder] = useState({ instrument_id: boot.instruments.find((item) => item.tradable)?.id || 0, side: "buy", mode: "quantity", quantity: "100", amount: "" });
  const [checks, setChecks] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [cash, setCash] = useState({ direction: "in", amount: "", note: "" });
  const load = () => {
    api.orders().then(setOrders).catch((reason: Error) => setError(reason.message));
    api.dashboard().then(setDash).catch(() => undefined);
  };
  useEffect(() => { load(); }, []);
  const logBody = {
    skip: log.skip,
    confirm_skip: log.confirm_skip,
    reason: log.reason,
    hypothesis: log.hypothesis,
    exit_condition: log.exit_condition,
    confidence: log.confidence ? Number(log.confidence) : null,
    emotion_tag: log.emotion_tag,
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>交易与持仓</h1>
          <p>15:00 前提交，当晚按收盘价成交。15:00 之后的单进入下一交易日。只做多，100 股一手，当天买入不能当天卖出。</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <section className="card">
        <h2>持仓</h2>
        <table>
          <thead><tr><th>标的</th><th>数量</th><th>可卖</th><th>成本</th><th>市值</th><th>浮动盈亏</th></tr></thead>
          <tbody>
            {(dash?.estimate.weights || []).filter((row) => row.asset_class !== "cash" && Number(row.quantity || 0) > 0).map((row) => (
              <tr key={row.symbol}><td>{row.name}</td><td>{row.quantity}</td><td>{row.sellable}</td><td>{money(row.avg_cost)}</td><td>{money(row.market_value)}</td><td className={Number(row.pnl) > 0 ? "up" : "down"}>{signed(row.pnl)}</td></tr>
            ))}
          </tbody>
        </table>
        {!dash?.estimate.weights?.some((row) => Number(row.quantity || 0) > 0 && row.asset_class !== "cash") && <p className="hint">还没有持仓。可以按数量下单，或按目标权重一次建仓。</p>}
      </section>
      <div className="split">
        <section className="card">
          <h2>下单</h2>
          <LogFields log={log} setLog={setLog} />
          <div style={{ marginTop: 10 }}><TradeChecklist onChange={(ids, ok) => { setChecks(ids); setReady(ok); }} /></div>
          <form
            className="row"
            style={{ marginTop: 10 }}
            onSubmit={(event) => {
              event.preventDefault();
              api.order({
                instrument_id: Number(order.instrument_id),
                side: order.side,
                quantity: order.mode === "quantity" ? Number(order.quantity) : null,
                amount: order.mode === "amount" ? order.amount : null,
                log: logBody,
                checklist: checks,
              }).then((result) => {
                if (result.status === "rejected") setError(result.reject_reason);
                setLog({ ...log, reason: "" });
                load();
              }).catch((reason: Error) => setError(reason.message));
            }}
          >
            <label>标的
              <select value={order.instrument_id} onChange={(event) => setOrder({ ...order, instrument_id: Number(event.target.value) })}>
                {boot.instruments.filter((item) => item.tradable).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label>方向
              <select value={order.side} onChange={(event) => setOrder({ ...order, side: event.target.value })}>
                <option value="buy">买入</option>
                <option value="sell">卖出</option>
              </select>
            </label>
            <label>方式
              <select value={order.mode} onChange={(event) => setOrder({ ...order, mode: event.target.value })}>
                <option value="quantity">按数量</option>
                <option value="amount">按金额</option>
              </select>
            </label>
            {order.mode === "quantity"
              ? <label>股数<input value={order.quantity} onChange={(event) => setOrder({ ...order, quantity: event.target.value })} /></label>
              : <label>金额<input value={order.amount} onChange={(event) => setOrder({ ...order, amount: event.target.value })} /></label>}
            <button className="primary" disabled={!ready}>提交</button>
          </form>
        </section>
        <section className="card">
          <h2>按目标建仓</h2>
          <p className="hint">用最近收盘价估算整手数量。真正成交仍是成交日的收盘价。</p>
          <button className="ghost" style={{ marginTop: 10 }} onClick={() => api.preview().then(setPreview).catch((reason: Error) => setError(reason.message))}>生成预览</button>
          {preview && (
            <>
              <table>
                <tbody>
                  {preview.lines.map((line) => (
                    <tr key={line.symbol}><td>{line.name}</td><td>{line.side === "buy" ? "买入" : "卖出"}</td><td>{line.quantity}</td><td>{money(line.estimate_amount)}</td></tr>
                  ))}
                </tbody>
              </table>
              {!preview.lines.length && <p className="hint">当前已经接近目标，没有整手可调。</p>}
              <button
                className="primary"
                disabled={!preview.lines.length || !ready}
                onClick={() => api.submitPreview({ log: logBody, checklist: checks }).then(() => { setPreview(null); load(); }).catch((reason: Error) => setError(reason.message))}
              >确认提交到 {preview.session_date}</button>
            </>
          )}
          <form
            className="row"
            style={{ marginTop: 16 }}
            onSubmit={(event) => {
              event.preventDefault();
              api.cash(cash).then(() => { setCash({ ...cash, amount: "" }); load(); }).catch((reason: Error) => setError(reason.message));
            }}
          >
            <label>出入金
              <select value={cash.direction} onChange={(event) => setCash({ ...cash, direction: event.target.value })}>
                <option value="in">入金</option>
                <option value="out">出金</option>
              </select>
            </label>
            <label>金额<input value={cash.amount} onChange={(event) => setCash({ ...cash, amount: event.target.value })} /></label>
            <button className="ghost">记录</button>
          </form>
        </section>
      </div>
      <section className="card">
        <h2>委托</h2>
        <table>
          <thead><tr><th>日期</th><th>标的</th><th>方向</th><th>状态</th><th>数量</th><th>价格</th><th></th></tr></thead>
          <tbody>
            {orders.map((item) => (
              <tr key={item.id}>
                <td>{item.trade_date}</td>
                <td>{item.name}</td>
                <td>{item.side === "buy" ? "买入" : "卖出"}</td>
                <td>{statusLabel(item.status)}{item.reject_reason ? ` · ${item.reject_reason}` : ""}</td>
                <td>{item.quantity || item.quantity_requested || item.amount_requested}</td>
                <td>{money(item.price)}</td>
                <td>{item.status === "pending" && <button className="ghost" onClick={() => api.cancel(item.id).then(load)}>撤销</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function LogFields({ log, setLog }: { log: LogState; setLog: (value: LogState) => void }) {
  return (
    <div>
      <label>理由<textarea value={log.reason} disabled={log.skip} onChange={(event) => setLog({ ...log, reason: event.target.value })} /></label>
      <div className="row" style={{ marginTop: 8 }}>
        <label className="grow">假设<input value={log.hypothesis} disabled={log.skip} onChange={(event) => setLog({ ...log, hypothesis: event.target.value })} /></label>
        <label className="grow">退出条件<input value={log.exit_condition} disabled={log.skip} onChange={(event) => setLog({ ...log, exit_condition: event.target.value })} /></label>
        <label>信心
          <select value={log.confidence} disabled={log.skip} onChange={(event) => setLog({ ...log, confidence: event.target.value })}>
            <option value="">未填</option>
            {[1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>{level}</option>)}
          </select>
        </label>
        <label>情绪
          <select value={log.emotion_tag} disabled={log.skip} onChange={(event) => setLog({ ...log, emotion_tag: event.target.value })}>
            <option value="">未填</option>
            {["平静", "焦虑", "兴奋", "不甘", "恐惧"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <label style={{ marginTop: 8 }}><span><input type="checkbox" checked={log.skip} onChange={(event) => setLog({ ...log, skip: event.target.checked })} /> 跳过日志，记为临时决策</span></label>
      {log.skip && <label><span><input type="checkbox" checked={log.confirm_skip} onChange={(event) => setLog({ ...log, confirm_skip: event.target.checked })} /> 我确认这笔交易不写理由</span></label>}
    </div>
  );
}

type LogState = { reason: string; hypothesis: string; exit_condition: string; confidence: string; emotion_tag: string; skip: boolean; confirm_skip: boolean };

function Journal() {
  const [rows, setRows] = useState<Order[]>([]);
  useEffect(() => { api.logs().then(setRows); }, []);
  return (
    <>
      <div className="page-head"><div><h1>决策日志</h1><p>假设和退出条件只是笔记。成交之后，这里对照的是这笔价格到最近收盘价的涨跌。</p></div></div>
      {rows.map((row) => (
        <article className="card" key={row.id}>
          <h2>{row.trade_date} · {row.side === "buy" ? "买入" : "卖出"} {row.name}</h2>
          <p className="tag">{statusLabel(row.status)}{row.is_temporary ? " · 临时决策" : ""}{row.log?.confidence ? ` · 信心 ${row.log.confidence}` : ""}{row.log?.emotion_tag ? ` · ${row.log.emotion_tag}` : ""}</p>
          <p style={{ marginTop: 8 }}>{row.log?.skipped ? "这笔交易没有写理由。" : row.log?.reason}</p>
          {row.log?.hypothesis && <p className="hint">假设：{row.log.hypothesis}</p>}
          {row.log?.exit_condition && <p className="hint">退出：{row.log.exit_condition}</p>}
          {row.outcome && <p className="hint">{row.side === "buy" ? "买入后" : "卖出后"}至 {row.outcome.price_date}：{pct(row.outcome.since_return)}</p>}
        </article>
      ))}
      {!rows.length && <p className="hint">还没有日志。</p>}
    </>
  );
}

function Settings({ boot }: { boot: Bootstrap }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.jobs().then(setJobs);
  useEffect(() => { load(); }, []);
  return (
    <>
      <div className="page-head"><div><h1>设置</h1><p>行情来自 AKShare，使用不复权收盘价。分红按真实产品在除息日记现金，红利税不计入。</p></div></div>
      <section className="card">
        <h2>日终</h2>
        <p className="hint">工作日 18:30 会自动同步并估值。现在也可以手动跑。账户建立于 {boot.account?.created_on || "尚未建立"}。</p>
        <div className="actions" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy} onClick={() => { setBusy(true); api.sync().then((result) => { setMessage(result.messages.join("\n")); load(); }).catch((reason: Error) => setError(reason.message)).finally(() => setBusy(false)); }}>同步行情与分红</button>
          <button className="ghost" disabled={busy} onClick={() => { setBusy(true); api.valuate().then((result) => { setMessage(`快照状态 ${result.status}，截止 ${result.as_of}`); load(); }).catch((reason: Error) => setError(reason.message)).finally(() => setBusy(false)); }}>生成快照</button>
        </div>
        {message && <p style={{ whiteSpace: "pre-wrap", marginTop: 10 }}>{message}</p>}
        {error && <p className="error">{error}</p>}
      </section>
      <section className="card">
        <h2>导出</h2>
        <div className="actions">
          <a className="button ghost" href={apiPath("/api/export/trades.csv")}>交易流水</a>
          <a className="button ghost" href={apiPath("/api/export/snapshots.csv")}>快照</a>
          <a className="button ghost" href={apiPath("/api/export/logs.csv")}>决策日志</a>
          <a className="button ghost" href={apiPath("/api/export/plans.json")}>方案与权重版本</a>
        </div>
      </section>
      <section className="card">
        <h2>最近任务</h2>
        {jobs.map((job) => <p key={job.id}>{job.started_at} · {job.kind} · {job.status}<br />{job.message}</p>)}
        {!jobs.length && <p className="hint">还没有任务。</p>}
      </section>
      <PracticeControls boot={boot} />
      <PasswordCard />
    </>
  );
}

function statusLabel(status: string) {
  return { pending: "待成交", filled: "已成交", rejected: "已拒绝", cancelled: "已撤销" }[status] || status;
}
