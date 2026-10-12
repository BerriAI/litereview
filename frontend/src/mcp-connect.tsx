import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { api, send } from "./api";
import { LoadingState } from "./request-state";

export function MCPConnect() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState("");
  const [busy, setBusy] = useState("");
  const [revision, setRevision] = useState(0);
  const load = () => api("/mcp/connections").then(setData);
  useEffect(() => {
    let active = true;
    api("/mcp/connections").then(r => { if (active) { setData(r); setError(""); } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [revision]);
  const copy = async (text: string, kind: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(kind); setNotice(`${kind} copied.`); }
    catch { setError("Couldn’t copy. Select the text and copy it manually."); }
  };
  return <section className="integration-settings" aria-label="Connect an AI assistant">
    <h2>Connect an AI assistant</h2>
    <p>Manage LiteReview from Claude or another MCP client using your Google Workspace account.</p>
    {!data ? <LoadingState label="Loading connection details…" error={error} retry={() => { setError(""); setRevision(v => v + 1); }} />
      : data.enabled ? <>
        <div className="settings-step">
          <h3>1. Add a remote connector</h3>
          <p>In Claude, open Settings → Connectors → Add custom connector and paste this URL.</p>
          <div className="mcp-copy connection-url"><code>{data.url}</code>
            <button type="button" aria-label="Copy MCP URL" onClick={() => copy(data.url, "URL")}>
              {copied === "URL" ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}{copied === "URL" ? "Copied" : "Copy URL"}
            </button>
          </div>
        </div>
        <div className="settings-step">
          <h3>2. Sign in and connect</h3>
          <p>Sign in with Google, then approve the LiteReview connection.</p>
          <p className="meta">Try asking: “Show my agents and their review rules.” Your assistant can discover the available options and read the included guide.</p>
        </div>
        <details className="settings-step"><summary>Connect with Claude Code</summary>
          <p>Run this command, then open <code>/mcp</code> in Claude Code to sign in.</p>
          <div className="mcp-copy connection-url"><code>{`claude mcp add --transport http litereview ${data.url}`}</code>
            <button type="button" onClick={() => copy(`claude mcp add --transport http litereview ${data.url}`, "Command")}>{copied === "Command" ? "Copied" : "Copy command"}</button>
          </div>
        </details>
        <p className="settings-permissions">Connected clients use your workspace permissions and can start paid reviews. Only admins can see spend or manage admin roles.</p>
        <div className="settings-step"><h3>Connected clients</h3>
          {!data.connections.length && <p className="meta">No clients connected yet. They’ll appear here after you approve a connection.</p>}
          {data.connections.map((connection: any) => <div className="mcp-client" key={connection.id}>
            <span>{connection.name}<small>Connected {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(connection.created_at * 1000)}</small></span>
            <button disabled={!!busy} onClick={async () => {
              setBusy(connection.id); setError(""); setNotice("");
              try { await api(`/mcp/connections/${connection.id}`, send("DELETE")); await load(); setNotice("Client disconnected. Reconnect it to grant access again."); }
              catch (e) { setError((e as Error).message); } finally { setBusy(""); }
            }}>{busy === connection.id ? "Disconnecting…" : "Disconnect"}</button>
          </div>)}
        </div>
      </> : <p className="meta">{data.reason}</p>}
    <div role="status" aria-live="polite">{notice && <p className="notice">{notice}</p>}</div>
    {data && error && <p className="error" role="alert">{error}</p>}
  </section>;
}
