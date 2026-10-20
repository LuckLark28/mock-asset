import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { Dashboard } from "./api";

export function NavChart({ series }: { series: Dashboard["series"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      grid: { left: 48, right: 16, top: 28, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { data: ["组合净值", "基准净值"], top: 0 },
      xAxis: { type: "category", data: series.map((item) => item.date), axisLabel: { color: "#6f6458" } },
      yAxis: { type: "value", scale: true, axisLabel: { color: "#6f6458" }, splitLine: { lineStyle: { color: "#e4d9c8" } } },
      series: [
        { name: "组合净值", type: "line", showSymbol: series.length < 2, data: series.map((item) => Number(item.nav)), lineStyle: { color: "#7d3b24", width: 2 }, itemStyle: { color: "#7d3b24" } },
        { name: "基准净值", type: "line", showSymbol: false, data: series.map((item) => (item.benchmark_nav ? Number(item.benchmark_nav) : null)), lineStyle: { color: "#241c16", width: 1.5 }, itemStyle: { color: "#241c16" } },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [series]);
  if (!series.length) return <p className="hint" style={{ marginTop: 24 }}>收盘并生成快照之后，这里会出现净值曲线。</p>;
  return <div ref={ref} className="chart" />;
}
