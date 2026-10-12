import React, { useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api, send } from "./api";
import { Field, SearchPicker } from "./ui";
import { LoadingState } from "./request-state";

type Config = { connection_id: string; model: string };

export function AgentFromPrompt({ value, update, onDraft, onBusy, onSettings }: {
  value: any; update: (key: string, value: any) => void; onDraft: (draft: any) => void;
  onBusy: (busy: boolean) => void; onSettings: () => void;
}) {
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    api("/settings/agent-generation").then(data => { if (alive.current) setConfig(data); })
      .catch(err => { if (alive.current) setError(err.message); });
    return () => { alive.current = false; onBusy(false); };
  }, []);
  const generate = async () => {
    setBusy(true); onBusy(true); setError(""); setDone(false);
    try {
      const draft = await api("/agents/generate", send("POST", { prompt: value.generation_prompt || "" }));
      if (alive.current) { onDraft(draft); setDone(true); }
    } catch (err) { if (alive.current) setError((err as Error).message); }
    finally { if (alive.current) { setBusy(false); onBusy(false); } }
  };
  return <section className="agent-prompt" aria-label="Create from prompt">
    <button type="button" className="text-button" disabled={busy} aria-expanded={!!value.from_prompt}
      onClick={() => update("from_prompt", !value.from_prompt)}><Sparkles size={14} /> Create from prompt</button>
    {value.from_prompt && <div className="agent-prompt-fields">
      <Field label="Describe your reviewer" hint="Describe its focus, what it should check, and how it should report findings.">
        <textarea rows={3} maxLength={12000} value={value.generation_prompt || ""} disabled={busy}
          placeholder="Review Python API changes for security bugs. Trace authentication and input validation, verify concrete failures, and keep findings concise."
          onChange={e => update("generation_prompt", e.target.value)} />
      </Field>
      <div className="actions"><button type="button" className="primary" onClick={generate}
        disabled={busy || !config?.connection_id || (value.generation_prompt || "").trim().length < 3}>
        {busy ? <><Loader2 size={14} className="spin" /> Creating draft…</> : "Generate draft"}
      </button><button type="button" className="text-button" disabled={busy} onClick={onSettings}>Generation settings</button></div>
      <p className="meta">{config?.connection_id ? `Uses ${config.model}. One model request; usage charges apply.` : "Choose a model connection in Settings → Agent creation."} Review and edit the draft before saving.</p>
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
    {done && <p className="notice" role="status">Draft ready. Review the name, instructions, and capabilities below, then choose Save agent.</p>}
  </section>;
}

export function AgentGenerationSettings({ connections, onModels }: { connections: any[]; onModels: () => void }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [modelError, setModelError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [revision, setRevision] = useState(0);
  const modelRequest = useRef(0);
  useEffect(() => {
    let active = true;
    api("/settings/agent-generation").then(data => { if (active) setConfig(data); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    const connection = connections.find(c => c.id === config?.connection_id);
    modelRequest.current++;
    setModels(connection?.models || []); setModelError(""); setLoadingModels(false);
    return () => { modelRequest.current++; };
  }, [config?.connection_id, connections]);
  const refreshModels = async () => {
    if (!config?.connection_id || loadingModels) return;
    const request = ++modelRequest.current;
    setLoadingModels(true); setModelError("");
    try { const result = await api(`/connections/${config.connection_id}/test`, send("POST")); if (request === modelRequest.current) setModels(result.models || []); }
    catch { if (request === modelRequest.current) setModelError("Couldn’t load models. Retry, or enter a model ID directly."); }
    finally { if (request === modelRequest.current) setLoadingModels(false); }
  };
  const update = (next: Config) => { setConfig(next); setSaved(false); };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!config?.connection_id || !config.model.trim()) { setError("Choose a connection and enter a model before saving."); return; }
    setBusy(true); setError(""); setSaved(false);
    try { await api("/settings/agent-generation", send("PUT", config)); setSaved(true); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="integration-settings agent-generation-settings">
    <h2>Agent creation</h2><p>Choose the model that drafts instructions for Create from prompt. PR reviews use each agent’s own model.</p>
    {!connections.length ? <button onClick={onModels}>Add a model connection</button> : config ? <form onSubmit={save}>
      <div className="form-grid">
        <SearchPicker label="Generation connection" value={config.connection_id} disabled={busy}
          options={connections.map(c => ({ value: c.id, label: c.name }))}
          onChange={connection_id => update({ connection_id, model: "" })} />
        <SearchPicker label="Generation model" value={config.model} disabled={busy} allowCustom loading={loadingModels}
          options={models.map(id => ({ value: id, label: id }))} onChange={model => update({ ...config, model })} />
      </div>
      <button type="button" className="text-button" disabled={loadingModels || !config.connection_id} onClick={refreshModels}>{loadingModels ? "Loading models…" : "Refresh available models"}</button>
      {modelError && <p className="meta">{modelError}</p>}
      <div className="actions"><button className="primary" disabled={busy}>{busy ? "Saving…" : "Save generation settings"}</button></div>
    </form> : <LoadingState label="Loading generation settings…" error={error} retry={() => { setError(""); setRevision(v => v + 1); }} />}
    {config && error && <p className="error" role="alert">{error}</p>}
    <div role="status">{saved && <p className="notice">Generation settings saved.</p>}</div>
  </section>;
}
