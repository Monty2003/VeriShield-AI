/**
 * Where the score came from, signal by signal.
 *
 * The risk engine is additive and every point is attributable, so this chart
 * is not a visualisation of the score -- it IS the score, drawn. A reviewer
 * who has to justify a decision can point at a bar.
 */

import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  Tooltip,
} from 'chart.js';
import type { TooltipItem } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import type { RiskContribution } from '../types/api';
import { STATUS_META } from '../lib/format';
import type { SignalStatus } from '../types/api';

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip);

interface Row {
  title: string;
  points: number;
  status: SignalStatus;
  reason: string;
  code: string;
}

function toRows(contributions: RiskContribution[]): Row[] {
  return contributions.map((c) => ({
    title: String(c.title ?? c.code ?? 'signal'),
    points: Number(c.points ?? 0),
    status: (c.status as SignalStatus) ?? 'fail',
    reason: String(c.reason ?? ''),
    code: String(c.code ?? ''),
  }));
}

export default function ContributionChart({
  contributions,
}: {
  contributions: RiskContribution[];
}) {
  const rows = toRows(contributions).slice(0, 12);

  if (rows.length === 0) {
    return (
      <div className="card p-6 text-center">
        <p className="section-title">Score derivation</p>
        <p className="mt-3 text-sm text-slate-400">
          No signal contributed risk points. The score is zero because nothing
          adverse was found -- not because nothing was checked.
        </p>
      </div>
    );
  }

  const data = {
    labels: rows.map((r) => r.title),
    datasets: [
      {
        data: rows.map((r) => r.points),
        backgroundColor: rows.map((r) => `${STATUS_META[r.status].hex}cc`),
        hoverBackgroundColor: rows.map((r) => STATUS_META[r.status].hex),
        borderRadius: 4,
        barThickness: 14,
      },
    ],
  };

  return (
    <div className="card p-4">
      <div className="mb-1 flex items-baseline justify-between">
        <p className="section-title">Score derivation</p>
        <p className="font-mono text-xs text-slate-500">
          {rows.reduce((a, r) => a + r.points, 0).toFixed(1)} pts
        </p>
      </div>
      <p className="mb-4 text-[11px] text-slate-500">
        severity weight &times; confidence &times; status multiplier, summed
      </p>

      <div style={{ height: Math.max(140, rows.length * 30) }}>
        <Bar
          data={data}
          options={{
            indexAxis: 'y' as const,
            maintainAspectRatio: false,
            responsive: true,
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#111827',
                borderColor: '#243044',
                borderWidth: 1,
                padding: 10,
                titleFont: { family: 'Inter', size: 12 },
                bodyFont: { family: 'Inter', size: 11 },
                bodyColor: '#cbd5e1',
                callbacks: {
                  label: (item: TooltipItem<'bar'>) => {
                    const row = rows[item.dataIndex];
                    return `${row.points.toFixed(2)} pts (${row.status})`;
                  },
                  afterLabel: (item: TooltipItem<'bar'>) => {
                    const reason = rows[item.dataIndex].reason;
                    // Wrap by hand -- Chart.js tooltips do not wrap text.
                    return reason.match(/.{1,52}(\s|$)/g) ?? [];
                  },
                },
              },
            },
            scales: {
              x: {
                grid: { color: '#1a2233' },
                ticks: { color: '#64748b', font: { size: 10 } },
                border: { display: false },
              },
              y: {
                grid: { display: false },
                ticks: {
                  color: '#94a3b8',
                  font: { size: 11 },
                  callback(_value, index) {
                    const label = rows[index]?.title ?? '';
                    return label.length > 30 ? `${label.slice(0, 29)}...` : label;
                  },
                },
                border: { display: false },
              },
            },
          }}
        />
      </div>
    </div>
  );
}
