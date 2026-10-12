import React, { useEffect, useState } from "react";
import { api, ApiError } from "./api";

type Cost = { cost_usd: number | null; complete: boolean; estimated: boolean };
type Summary = { runs: number; cost_usd: number | null; average_cost_usd: number | null; priced_runs: number; unpriced_runs: number; estimated: boolean };
type Breakdown = Summary & { name: string; id?: string; deleted?: boolean };
type SpendData = { total: Summary; users: Breakdown[]; repositories: Breakdown[]; agents: Breakdown[] };
export const dollars = (value: number | null | undefined) => value == null ? "Unavailable" :
  value > 0 && value < 0.01 ? "$" + value.toFixed(4) : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);

export function RunCost({ value }: { value?: Cost }) {
  const partial = value && !value.complete && value.cost_usd != null;
  return <span className="run-cost" title={!value || value.cost_usd == null ? "Cost was not recorded for this run." :
    `${value.estimated ? "Estimated model cost" : "Reported model cost"}${partial ? "; some requests have no cost data" : ""}. Excludes sandbox and hosting charges.`}>
    {dollars(value?.cost_usd)}{partial ? "+" : ""}{value?.estimated && <span className="cost-estimate"> est.</span>}
  </span>;
}

export function SpendPage() {
  const [data, setData] = useState<SpendData | null>(null);
  const [error, setError] = useState("");
  const [group, setGroup] = useState<"users" | "repositories" | "agents">("agents");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const next = await api("/spend");
        if (active) { setData(next); setError(""); }
      } catch (err) {
        if (active) {
          if (err instanceof ApiError && [401, 403].includes(err.status)) setData(null);
          setError((err as Error).message);
        }
      }
      finally { if (active) timer = setTimeout(load, 15000); }
    };
    load();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  if (!data) return error ? <div className="error" role="alert">{error}</div> : <p>Loading spend…</p>;
  const total = data.total;
  const label = { users: "User", repositories: "Repository", agents: "Agent" }[group];
  return <div className="spend-page">
    {error && <div className="error" role="alert">{error}</div>}
    <dl className="spend-summary">
      <div><dt>Total cost</dt><dd>{dollars(total.cost_usd)}{total.unpriced_runs > 0 && total.cost_usd != null ? "+" : ""}</dd><small>Model spend · USD · All time</small></div>
      <div><dt>Total runs</dt><dd>{total.runs.toLocaleString()}</dd><small>PR reviews, including failed runs</small></div>
      <div><dt>Cost per run</dt><dd>{dollars(total.average_cost_usd)}</dd><small>{total.priced_runs.toLocaleString()} finished runs with complete cost data</small></div>
    </dl>
    <p className="spend-note">Includes every reviewer, fusion model, and final review pass. Excludes agent checks, sandbox charges, and hosting.
      {total.estimated && " Some costs are estimated from token usage and model rates."}
      {total.unpriced_runs > 0 && ` ${total.unpriced_runs} ${total.unpriced_runs === 1 ? "run has" : "runs have"} missing or incomplete cost data; totals include only recorded costs. Older runs were not metered.`}
    </p>
    <div className="tabs" aria-label="Spend breakdown">
      <button aria-pressed={group === "agents"} className={group === "agents" ? "active" : ""} onClick={() => setGroup("agents")}>By agent</button>
      <button aria-pressed={group === "users"} className={group === "users" ? "active" : ""} onClick={() => setGroup("users")}>By user</button>
      <button aria-pressed={group === "repositories"} className={group === "repositories" ? "active" : ""} onClick={() => setGroup("repositories")}>By repository</button>
    </div>
    {group === "agents" && <p className="spend-note">Each agent counts a PR run once, including use through groups. Costs include all instances and fusion stages. A run with multiple agents appears in each agent’s count.</p>}
    {data[group].length ? <div className="table-wrap" tabIndex={0} role="region" aria-label={`Spend by ${label.toLowerCase()}`}>
      <table><thead><tr><th>{label}</th><th>Runs</th><th>Total cost</th><th>Cost per run</th></tr></thead>
        <tbody>{data[group].map(row => <tr key={row.id || row.name}>
          <td>{row.name}{row.deleted && <small>Deleted agent</small>}{row.unpriced_runs > 0 && <small>{row.unpriced_runs} {row.unpriced_runs === 1 ? "run has" : "runs have"} incomplete cost data</small>}</td>
          <td>{row.runs.toLocaleString()}</td>
          <td>{dollars(row.cost_usd)}{row.unpriced_runs > 0 && row.cost_usd != null ? "+" : ""}{row.estimated && <span className="cost-estimate"> est.</span>}</td>
          <td>{dollars(row.average_cost_usd)}<small>{row.priced_runs} priced {row.priced_runs === 1 ? "run" : "runs"}</small></td>
        </tr>)}</tbody>
      </table>
    </div> : <div className="empty-state"><h2>{group === "agents" ? "No agent runs yet" : "No runs yet"}</h2><p>Spend appears here after your first PR review.</p></div>}
  </div>;
}
