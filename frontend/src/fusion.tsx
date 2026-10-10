import React, { useEffect, useRef, useState } from "react";
import { api, send } from "./api";
import { Field, FormSection, SearchPicker } from "./ui";
import { ReviewResult } from "./review-result";

type Model = { connection_id: string; model: string; reasoning_effort?: string | null; focus?: string };
type Fusion = { branches: Model[]; instructions: string };

export function FusionFields({ value, onChange, connections, disabled }: {
  value: Fusion; onChange: (value: Fusion) => void;
  connections: { id: string; name: string }[]; disabled: boolean;
}) {
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const ids = [...new Set(value.branches.map(b => b.connection_id).filter(Boolean))].join(",");
  useEffect(() => {
    for (const id of ids.split(",").filter(Boolean)) {
      if (pending.current.has(id)) continue;
      pending.current.add(id);
      api("/connections/" + id + "/test", send("POST"))
        .then(r => setModels(current => ({ ...current, [id]: r.models || [] })))
        .catch(() => setErrors(current => ({ ...current, [id]: "Could not load models. Enter the model ID." })));
    }
  }, [ids]);
  const update = (index: number, patch: Partial<Model>) => onChange({
    ...value, branches: value.branches.map((b, i) => i === index ? { ...b, ...patch } : b),
  });
  return <FormSection title={`Fusion branches · ${value.branches.length}`} description="Branches review independently in separate sandboxes. All use this agent’s instructions, skills, and tools. The aggregator then verifies their reports and publishes one review.">
    {value.branches.map((branch, index) => <fieldset key={index} className="fusion-branch">
      <legend>Branch {index + 1}</legend>
      <div className="form-grid">
        <SearchPicker label={`Branch ${index + 1} connection`} disabled={disabled} value={branch.connection_id}
          options={connections.map(c => ({ value: c.id, label: c.name }))}
          onChange={connection_id => update(index, { connection_id, model: "" })} />
        <SearchPicker label={`Branch ${index + 1} model`} disabled={disabled} value={branch.model} allowCustom
          options={(models[branch.connection_id] || []).map(model => ({ value: model, label: model }))}
          onChange={model => update(index, { model })} />
      </div>
      {errors[branch.connection_id] && <small role="status">{errors[branch.connection_id]}</small>}
      <Field label={`Branch ${index + 1} reasoning effort`}>
        <select disabled={disabled} value={branch.reasoning_effort || ""} onChange={e => update(index, { reasoning_effort: e.target.value || null })}>
          <option value="">Model default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option>
        </select>
      </Field>
      <Field label={`Branch ${index + 1} focus (optional)`} hint="Give a branch a useful perspective, such as configuration contracts or state and lifecycle bugs.">
        <textarea disabled={disabled} rows={2} value={branch.focus || ""} onChange={e => update(index, { focus: e.target.value })} />
      </Field>
      <button type="button" className="text-button" disabled={disabled || value.branches.length === 1}
        onClick={() => onChange({ ...value, branches: value.branches.filter((_, i) => i !== index) })}>Remove branch {index + 1}</button>
    </fieldset>)}
    <button type="button" className="text-button" disabled={disabled || value.branches.length >= 16}
      onClick={() => onChange({ ...value, branches: [...value.branches, { ...value.branches[value.branches.length - 1], focus: "" }] })}>Add branch</button>
    <Field label="Aggregator guidance (optional)" hint="The aggregator already verifies evidence, merges duplicates, and preserves distinct defects. Add any task-specific guidance here.">
      <textarea rows={3} disabled={disabled} value={value.instructions} onChange={e => onChange({ ...value, instructions: e.target.value })} />
    </Field>
    <p className="meta">Runs {value.branches.length} branches plus one aggregator. Model and sandbox costs add together. Time and call limits apply to each stage; the minimum review time applies to branches. Up to 16 branches.</p>
  </FormSection>;
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
