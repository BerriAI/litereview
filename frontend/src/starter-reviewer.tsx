import { useEffect, useState } from "react";
import { api, send } from "./api";
import { Field, Modal } from "./ui";

export function StarterReviewer({
  connections,
  onSaved,
  close,
}: {
  connections: any[];
  onSaved: () => Promise<void>;
  close: () => void;
}) {
  const [connectionId, setConnectionId] = useState(connections[0]?.id || "");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [name, setName] = useState("reviewer");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [modelError, setModelError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<any>(null);
  const connection = connections.find((c) => c.id === connectionId);
  const harness = connection?.type === "anthropic" ? "claude-code" : "codex";
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setModels([]);
    setModel("");
    setModelError("");
    api(`/connections/${connectionId}/test`, send("POST"))
      .then((r) => {
        if (alive) setModels(r.models || []);
      })
      .catch(() => {
        if (alive)
          setModelError(
            "Couldn’t load models. Enter a model ID from your gateway.",
          );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [connectionId]);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const agent =
        saved ||
        (await api(
          "/resources/agents",
          send("POST", {
            name,
            harness,
            model,
            connection_id: connectionId,
            capabilities: [],
            skill_ids: [],
            mcps: [],
            instructions:
              "Review this PR for actionable defects. Explain a concrete failure scenario and cite the affected code. Report no findings when appropriate.",
          }),
        ));
      setSaved(agent);
      await api("/agents/check", send("POST", agent));
      await onSaved();
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      page
      title="Create your first reviewer"
      description="Reuse your model connection. You can customize the agent later."
      close={() => !busy && close()}
    >
      <form onSubmit={save} className="starter-form">
        <div className="dialog-body">
          <Field label="Reviewer name" hint="Use this name in your PR comment.">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              pattern="[a-zA-Z][a-zA-Z0-9_-]*"
              maxLength={40}
              required
              disabled={!!saved || busy}
            />
          </Field>
          {connections.length > 1 ? (
            <Field label="Model connection">
              <select
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                disabled={!!saved || busy}
              >
                {connections.map((c) => (
                  <option value={c.id} key={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <div className="starter-connection">
              <strong>Model connection</strong>
              <p>{connection?.name} · already connected</p>
            </div>
          )}
          <Field
            label="Model"
            hint={
              loading
                ? "Loading available models…"
                : modelError || "Choose the model this reviewer should use."
            }
          >
            {models.length ? (
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required
                disabled={!!saved || busy}
              >
                <option value="">Choose an available model</option>
                {models.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            ) : (
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Model ID"
                required
                disabled={loading || !!saved || busy}
              />
            )}
          </Field>
          <p className="starter-note">
            Harness: {harness === "codex" ? "Codex" : "Claude Code"}. Review
            instructions, skills, and MCP tools remain editable in Agents.
          </p>
          {saved && (
            <p className="notice">
              Your reviewer is saved. Retry the check without creating another
              agent.
            </p>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer>
          <p>
            Checks the harness and model on a sample project. Uses Modal and
            model credits.
          </p>
          <button className="primary" disabled={busy || loading}>
            {busy
              ? "Starting check…"
              : saved
                ? "Retry check"
                : "Save and check reviewer"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
