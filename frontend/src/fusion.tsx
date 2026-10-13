import React, { useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { api, send } from "./api";
import { Field, SearchPicker } from "./ui";
import { ReviewResult } from "./review-result";

import { BranchSettings, type Model, type Branch, type Selection } from "./fusion-settings";

export function AgentModels({ value, onChange, connections, skills, mcps, disabled }: {
  value: Selection; onChange: (value: Selection) => void;
  connections: { id: string; name: string }[]; disabled: boolean;
  skills: { id: string; name: string; description?: string }[];
  mcps: { id: string; name: string; tools?: { name: string; description?: string }[] }[];
}) {
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const selected: Branch[] = value.fusion?.branches || [value];
  const ids = [...new Set([...selected, value, ...selected.flatMap(b => b.condition ? [b.condition] : [])].map(b => b.connection_id).filter(Boolean))].join(",");
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
  const updateModels = (next: Branch[]) => onChange({ ...selection(value), fusion: { branches: next } });
  const updateBranch = (index: number, patch: Partial<Branch>) => value.fusion
    ? updateModels(selected.map((m, i) => i === index ? { ...m, ...patch } : m))
    : onChange({ ...selection(value), ...patch, fusion: null });
  const fields = (model: Model, label: string, change: (patch: Partial<Model>) => void, reasoning = true) => <>
    <div className="form-grid">
      <SearchPicker label={label === "Model" ? "Model connection" : `${label} connection`} disabled={disabled} value={model.connection_id}
        options={connections.map(c => ({ value: c.id, label: c.name }))}
        onChange={connection_id => change({ connection_id, model: "" })} />
      <SearchPicker label={label} disabled={disabled} value={model.model} allowCustom
        options={(models[model.connection_id] || []).map(id => ({ value: id, label: id }))}
        onChange={model => change({ model })} />
    </div>
    {errors[model.connection_id] && <small role="status">{errors[model.connection_id]}</small>}
    {reasoning && <details className="model-settings"><summary>Reasoning effort</summary>
      <Field label={`${label} reasoning effort`}>
        <select disabled={disabled} value={model.reasoning_effort || ""} onChange={e => change({ reasoning_effort: e.target.value || null })}>
          <option value="">Model default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option>
        </select>
      </Field>
    </details>}
  </>;
  return <>
    {value.fusion && <p className="meta">Parallel reviewers work independently, then the final reviewer checks and combines their findings. Each stage uses model and sandbox credits.</p>}
    {selected.map((model, index) => <fieldset className="agent-model" key={index} disabled={disabled}>
      {value.fusion && <legend>Reviewer {index + 1}{model.name ? ` · ${model.name}` : ""}</legend>}
      {fields(model, value.fusion ? `Reviewer ${index + 1} model` : "Model", patch => updateBranch(index, patch))}
      {value.fusion && <>
        <BranchSettings branch={model} defaults={value} change={patch => updateBranch(index, patch)} skills={skills} mcps={mcps} />
        <details className="fusion-role-settings">
          <summary>Run condition · {model.paths != null ? (model.condition ? "Paths + decision model" : "Changed paths") : model.condition ? "Decision model" : "Always run"}</summary>
          <label className="check"><input type="checkbox" checked={model.paths != null} onChange={e => updateBranch(index, { paths: e.target.checked ? [""] : null })} />Run only for matching files or directories</label>
          {model.paths != null && <>
            <Field label="Changed paths"><textarea rows={3} required value={model.paths.join("\n")}
              placeholder={"litellm/llms/**\nlitellm/litellm_core_utils/prompt_templates/"}
              onChange={e => updateBranch(index, { paths: e.target.value.split("\n") })} /></Field>
            <small>One repository-relative path per line. Any match qualifies. Use an exact file, a directory ending in /, * within a directory, or ** across directories. Checks the full PR diff, including both names of renamed files. Matching is case-sensitive.</small>
            <p className="meta">No matching files skips this reviewer before any model call. If a decision criterion is also enabled, both must match. Unreadable paths run the reviewer with a warning.</p>
          </>}
          <label className="check"><input type="checkbox" checked={!!model.condition} onChange={e => updateBranch(index, { condition: e.target.checked
            ? { connection_id: model.connection_id, model: "", instructions: "", threshold: 0.5 } : null })} />Run only when a criterion is true</label>
          {model.condition && <>
            <p className="meta">A decision model, such as Jev, checks this PR’s title and body before the reviewer starts. False skips it. An unavailable decision runs the reviewer and shows a warning.</p>
            {fields(model.condition, `Reviewer ${index + 1} decision model`, patch => updateBranch(index, { condition: { ...model.condition!, ...patch } }), false)}
            <Field label="Run this reviewer when"><textarea rows={3} required value={model.condition.instructions}
              placeholder="Does this PR change LLM request or response translation?" onChange={e => updateBranch(index, { condition: { ...model.condition!, instructions: e.target.value } })} /></Field>
            <Field label="Minimum probability for true"><input type="number" min="0" max="1" step="0.01" required value={model.condition.threshold}
              onChange={e => updateBranch(index, { condition: { ...model.condition!, threshold: Number(e.target.value) } })} /></Field>
            <small>0.5 runs the reviewer when true is at least as likely as false. Requires a model that supports the Decisions API; the decision call also uses credits. PR text can be incomplete, so use conditions for optional specialist reviews.</small>
          </>}
        </details>
        {selected.length > 1 && <button type="button" className="text-button" onClick={() => updateModels(selected.filter((_, i) => i !== index))}><X size={13} /> Remove reviewer {index + 1}</button>}
      </>}
    </fieldset>)}
    <span className="add-model-action">
      <button type="button" className="text-button" disabled={disabled || selected.length >= 16}
        onClick={() => updateModels([...(value.fusion ? selected : [selection(value)]), { ...selection(selected[selected.length - 1]), model: "" }])}>
        <Plus size={12} /> {value.fusion ? "Add parallel reviewer" : "Add model"}
      </button>
      {value.fusion && <button type="button" className="text-button" disabled={disabled} onClick={() => onChange({ ...selection(value), fusion: null })}>Use a single reviewer</button>}
    </span>
    {value.fusion && <div className="final-review-model">
      <h4>Final reviewer</h4>
      {fields(value, "Final review model", patch => onChange({ ...selection(value), ...patch, fusion: { branches: selected } }))}
      <small>Uses the agent instructions, skills, rules, and tools below to verify and combine the parallel reports into one review. Returning to a single reviewer keeps these settings.</small>
    </div>}
  </>;
}

export function FusionResults({ job, repo, head }: { job: any; repo: string; head: string }) {
  if (!job.branches?.length) return null;
  return <details className="fusion-results"><summary>Fusion · {job.branches.length} branches · {job.branches.filter((b: any) => b.status === "completed" && !b.result?.skipped).length} completed · {job.branches.filter((b: any) => b.result?.skipped).length} skipped</summary>
    <p className="meta">Aggregator: <code>{job.config.model}</code> · {job.aggregator_model_calls} model calls</p>
    {job.branches.map((branch: any, index: number) => <details key={branch.id}>
      <summary>{branch.config.fusion_role || `Branch ${index + 1}`} · {branch.config.model} · {branch.result?.skipped ? "skipped" : branch.status}</summary>
      {branch.config.branch_decision?.paths && <p className="meta">{branch.config.branch_decision.paths.error || (branch.config.branch_decision.paths.run ? `Path gate matched ${branch.config.branch_decision.paths.matched.length} changed files` : "Path gate: no matching files")}<br />{branch.config.branch_decision.paths.patterns.join(", ")}</p>}
      {branch.config.branch_decision?.model && <p className="meta">{branch.config.branch_decision.error || `Condition ${branch.config.branch_decision.run ? "true" : "false"} · Probability ${branch.config.branch_decision.probability} · Threshold ${branch.config.branch_decision.threshold}`}<br />{branch.config.branch_decision.criterion}</p>}
      <ReviewResult job={branch} repo={repo} head={head} />
      <details><summary>Activity · {branch.model_calls} model calls</summary>
        {branch.sandbox_id && <p className="meta">Sandbox <code>{branch.sandbox_id}</code></p>}
        <div className="logs">{branch.logs.map((log: any, i: number) => <div key={i}><time>{new Date(log.time * 1000).toLocaleTimeString()}</time><span>{log.message}</span></div>)}</div>
      </details>
      {branch.artifacts?.map((artifact: any) => <p key={artifact.id}><a href={"/api/artifacts/" + artifact.id} target="_blank" rel="noreferrer">{artifact.name}</a></p>)}
    </details>)}
  </details>;
}
