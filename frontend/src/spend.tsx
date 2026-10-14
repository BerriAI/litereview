import React, { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { api, ApiError } from "./api";
import { LoadingState } from "./request-state";
import { SearchPicker } from "./ui";
import { sortSpendRows, type SpendSort, type SpendSortKey } from "./spend-sort";

type Cost = { cost_usd: number | null; complete: boolean; estimated: boolean };
type Summary = { runs: number; cost_usd: number | null; average_cost_usd: number | null; priced_runs: number; unpriced_runs: number; estimated: boolean };
type Breakdown = Summary & { name: string; id?: string; deleted?: boolean; repo?: string; pr?: number; actor?: string; pull_requests?: number; priced_pull_requests?: number; average_pr_cost_usd?: number | null; average_runs_per_pr?: number };
type SpendData = { total: Summary; users: Breakdown[]; repositories: Breakdown[]; agents: Breakdown[]; pull_requests: Breakdown[]; user_pull_requests: Breakdown[] };
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
  const [group, setGroup] = useState<"users" | "repositories" | "agents" | "pull_requests">("agents");
  const [revision, setRevision] = useState(0);
  const [prUser, setPrUser] = useState("");
  const [sort, setSort] = useState<SpendSort>({ key: "cost_usd", direction: "descending" });
  const retry = () => { setError(""); setRevision(v => v + 1); };
  useEffect(() => {
    let active = true, inFlight = false, loaded = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      if (!active || inFlight) return;
      clearTimeout(timer);
      if (document.visibilityState !== "visible") return;
      inFlight = true;
      try {
        const next = await api("/spend", { signal: controller.signal, background: loaded });
        if (active) { setData(next); setError(""); loaded = true; }
      } catch (err) {
        if (active) {
          if (err instanceof ApiError && [401, 403].includes(err.status)) setData(null);
          setError((err as Error).message);
        }
      }
      finally { inFlight = false; if (active) timer = setTimeout(load, 15000); }
    };
    load();
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; clearTimeout(timer); controller.abort(); document.removeEventListener("visibilitychange", visible); };
  }, [revision]);
  if (!data) return <LoadingState label="Loading spend…" error={error} retry={retry} />;
  const total = data.total;
  const label = { users: "User", repositories: "Repository", agents: "Agent", pull_requests: "Pull request" }[group];
  const columns: { key: SpendSortKey; label: string }[] = [
    { key: "name", label },
    ...(group === "users" ? [{ key: "pull_requests" as const, label: "PRs" }] : []),
    { key: "runs", label: "Runs" },
    ...(group === "users" ? [{ key: "average_runs_per_pr" as const, label: "Runs per PR" }] : []),
    { key: "cost_usd", label: "Total cost" },
    { key: group === "users" ? "average_pr_cost_usd" : "average_cost_usd", label: group === "users" ? "Cost per PR" : "Cost per run" },
  ];
  const activeSort: SpendSort = columns.some(column => column.key === sort.key) ? sort : { key: "cost_usd", direction: "descending" };
  const rows = sortSpendRows(group === "pull_requests" && prUser ? data.user_pull_requests.filter(row => row.actor === prUser) : data[group], activeSort);
  return <div className="spend-page">
    {error && <div className="error" role="alert">{error} <button onClick={retry}>Retry</button></div>}
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
      <button aria-pressed={group === "pull_requests"} className={group === "pull_requests" ? "active" : ""} onClick={() => setGroup("pull_requests")}>By pull request</button>
    </div>
    {group === "agents" && <p className="spend-note">Each agent counts a PR run once, including use through groups. Costs include all instances and fusion stages. A run with multiple agents appears in each agent’s count.</p>}
    {group === "pull_requests" && <div className="spend-user-filter"><SearchPicker label="Requested by" value={prUser} onChange={setPrUser}
      options={[{value: "", label: "All users"}, ...data.users.map(user => ({value: user.name, label: user.name}))]} /></div>}
    {rows.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label={`Spend by ${label.toLowerCase()}`}>
      <table><thead><tr>{columns.map(column => {
        const active = activeSort.key === column.key;
        const direction = active && activeSort.direction === "descending" ? "ascending" : active ? "descending" : column.key === "name" ? "ascending" : "descending";
        const Icon = active ? activeSort.direction === "ascending" ? ArrowUp : ArrowDown : ArrowUpDown;
        return <th key={column.key} aria-sort={active ? activeSort.direction : "none"}>
          <button className="spend-sort" onClick={() => setSort({ key: column.key, direction })}
            aria-label={`Sort by ${column.label.toLowerCase()}, ${direction}`}>{column.label}<Icon size={13} aria-hidden="true" /></button>
        </th>;
      })}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id || row.name}>
          <td>{row.repo ? <a href={`https://github.com/${row.repo}/pull/${row.pr}`} target="_blank" rel="noreferrer">{row.name}</a> : group === "users" ? <button className="text-button" onClick={() => { setPrUser(row.name); setGroup("pull_requests"); }}>{row.name} · View PRs</button> : row.name}{row.deleted && <small>Deleted agent</small>}{row.unpriced_runs > 0 && <small>{row.unpriced_runs} {row.unpriced_runs === 1 ? "run has" : "runs have"} incomplete cost data</small>}</td>
          {group === "users" && <td>{row.pull_requests}</td>}
          <td>{row.repo ? <a href={`/runs?repo=${encodeURIComponent(row.repo)}&pr=${row.pr}${row.actor ? `&actor=${encodeURIComponent(row.actor)}` : ""}`}>{row.runs.toLocaleString()}</a> : row.runs.toLocaleString()}</td>
          {group === "users" && <td>{row.average_runs_per_pr?.toLocaleString("en-US", {maximumFractionDigits: 1})}</td>}
          <td>{dollars(row.cost_usd)}{row.unpriced_runs > 0 && row.cost_usd != null ? "+" : ""}{row.estimated && <span className="cost-estimate"> est.</span>}</td>
          <td>{dollars(group === "users" ? row.average_pr_cost_usd : row.average_cost_usd)}<small>{group === "users" ? `${row.priced_pull_requests} fully priced PRs` : `${row.priced_runs} priced ${row.priced_runs === 1 ? "run" : "runs"}`}</small></td>
        </tr>)}</tbody>
      </table>
    </div> : <div className="empty-state"><h2>{group === "agents" ? "No agent runs yet" : "No runs yet"}</h2><p>Spend appears here after your first PR review.</p></div>}
  </div>;
}
