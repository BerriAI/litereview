import React, { useEffect, useRef, useState } from "react";
import { CircleHelp, Plus, X } from "lucide-react";
import { api, send } from "./api";
import { Field, SearchPicker } from "./ui";
import { ReviewResult } from "./review-result";

type Model = { connection_id: string; model: string; reasoning_effort?: string | null };
type Selection = Model & { fusion?: { branches: Model[] } | null };

export function AgentModels({ value, onChange, connections, disabled }: {
  value: Selection; onChange: (value: Selection) => void;
  connections: { id: string; name: string }[]; disabled: boolean;
}) {
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const selected = value.fusion?.branches || [value];
  const ids = [...new Set([...selected, value].map(b => b.connection_id).filter(Boolean))].join(",");
  useEffect(() => {
    for (const id of ids.split(",").filter(Boolean)) {
      if (pending.current.has(id)) continue;
      pending.current.add(id);
      api("/connections/" + id + "/test", send("POST"))
        .then(r => setModels(current => ({ ...current, [id]: r.models || [] })))
        .catch(() => setErrors(current => ({ ...current, [id]: "Could not load models. Enter the model ID." })));
    }
  }, [ids]);
  const selection = (model: Model): Model => ({ connection_id: model.connection_id, model: model.model, reasoning_effort: model.reasoning_effort || null });
  const updateModels = (next: Model[]) => onChange(next.length === 1
    ? { ...selection(next[0]), fusion: null }
    : { ...selection(value), fusion: { branches: next.map(selection) } });
  const fields = (model: Model, label: string, change: (patch: Partial<Model>) => void) => <>
    <div className="form-grid">
      <SearchPicker label={label === "Model" ? "Model connection" : `${label} connection`} disabled={disabled} value={model.connection_id}
        options={connections.map(c => ({ value: c.id, label: c.name }))}
        onChange={connection_id => change({ connection_id, model: "" })} />
      <SearchPicker label={label} disabled={disabled} value={model.model} allowCustom
        options={(models[model.connection_id] || []).map(id => ({ value: id, label: id }))}
        onChange={model => change({ model })} />
    </div>
    {errors[model.connection_id] && <small role="status">{errors[model.connection_id]}</small>}
    <details className="model-settings"><summary>Reasoning effort</summary>
      <Field label={`${label} reasoning effort`}>
        <select disabled={disabled} value={model.reasoning_effort || ""} onChange={e => change({ reasoning_effort: e.target.value || null })}>
          <option value="">Model default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option>
        </select>
      </Field>
    </details>
  </>;
  return <>
    {selected.map((model, index) => <div className="agent-model" key={index}>
      {fields(model, selected.length === 1 ? "Model" : `Model ${index + 1}`, patch => updateModels(selected.map((m, i) => i === index ? { ...m, ...patch } : m)))}
      {selected.length > 1 && <button type="button" className="icon remove-model" aria-label={`Remove model ${index + 1}`} disabled={disabled}
        onClick={() => updateModels(selected.filter((_, i) => i !== index))}><X size={13} /></button>}
    </div>)}
    <span className="add-model-action">
      <button type="button" className="text-button" disabled={disabled || selected.length >= 16}
        onClick={() => updateModels([...selected, { ...selection(selected[selected.length - 1]), model: "" }])}>
        <Plus size={12} /> Add model
      </button>
      <span className="model-help">
        <button type="button" className="icon" aria-label="About adding models" aria-describedby="add-model-help"><CircleHelp size={12} /></button>
        <span role="tooltip" id="add-model-help">Models run in parallel as a fusion, using the same instructions, skills, and tools. Their findings are combined into one review. Each model adds usage costs.</span>
      </span>
    </span>
    {value.fusion && <div className="final-review-model">
      {fields(value, "Final review model", patch => onChange({ ...selection(value), ...patch, fusion: { branches: selected.map(selection) } }))}
      <small>Combines and verifies the findings into one review. Costs include all models and this final pass.</small>
    </div>}
  </>;
}

export function FusionResults({ job, repo, head }: { job: any; repo: string; head: string }) {
  if (!job.branches?.length) return null;
  return <details className="fusion-results"><summary>Fusion · {job.branches.length} branches · {job.branches.filter((b: any) => b.status === "completed").length} completed</summary>
    <p className="meta">Aggregator: <code>{job.config.model}</code> · {job.aggregator_model_calls} model calls</p>
    {job.branches.map((branch: any, index: number) => <details key={branch.id}>
      <summary>Branch {index + 1} · {branch.config.model} · {branch.status}</summary>
      <ReviewResult job={branch} repo={repo} head={head} />
      <details><summary>Activity · {branch.model_calls} model calls</summary>
        {branch.sandbox_id && <p className="meta">Sandbox <code>{branch.sandbox_id}</code></p>}
        <div className="logs">{branch.logs.map((log: any, i: number) => <div key={i}><time>{new Date(log.time * 1000).toLocaleTimeString()}</time><span>{log.message}</span></div>)}</div>
      </details>
      {branch.artifacts?.map((artifact: any) => <p key={artifact.id}><a href={"/api/artifacts/" + artifact.id} target="_blank" rel="noreferrer">{artifact.name}</a></p>)}
    </details>)}
  </details>;
}
