import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import { api, send } from "./api";

export function MCPConnect() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const load = () => api("/mcp/connections").then(setData);
  useEffect(() => { let active = true; api("/mcp/connections").then(r => { if (active) setData(r); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, []);
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); setNotice("Copied."); } catch { setError("Could not copy. Select and copy the text below."); } };
  return <section className="integration-settings" aria-label="Connect an AI assistant">
    <h2>Connect an AI assistant</h2>
    <p>Manage agents, review rules, models, repositories and runs from Claude or another MCP client. Sign in with your Google Workspace account; your existing permissions apply.</p>
    {data?.enabled ? <>
      <p>Add this URL as a remote MCP connector, sign in with Google, then choose Connect.</p>
      <div className="mcp-copy"><code>{data.url}</code><button type="button" aria-label="Copy MCP URL" onClick={() => copy(data.url)}><Copy size={14} />Copy URL</button></div>
      <details><summary>Claude Code</summary><p>Run this command, then use <code>/mcp</code> in Claude Code to sign in.</p>
        <div className="mcp-copy"><code>{`claude mcp add --transport http litereview ${data.url}`}</code><button type="button" onClick={() => copy(`claude mcp add --transport http litereview ${data.url}`)}>Copy command</button></div>
      </details>
      <p className="meta">Try: “Show my LiteReview agents and their review rules.” The MCP includes a usage guide, live option schemas and a configuration prompt for your assistant.</p>
      <p className="meta">Connected clients can manage shared settings and start paid checks and reviews. Spend and admin roles remain admin-only.</p>
      {!!data.connections.length && <h3>Your connected clients</h3>}
      {data.connections.map((connection: any) => <div className="mcp-client" key={connection.id}>
        <span>{connection.name}<small>Connected {new Date(connection.created_at * 1000).toLocaleDateString()}</small></span>
        <button disabled={!!busy} onClick={async () => {
          setBusy(connection.id); setError(""); setNotice("");
          try { await api(`/mcp/connections/${connection.id}`, send("DELETE")); await load(); setNotice("Client disconnected. Reconnect it to grant access again."); }
          catch (e) { setError((e as Error).message); } finally { setBusy(""); }
        }}>{busy === connection.id ? "Disconnecting…" : "Disconnect"}</button>
      </div>)}
    </> : data ? <p className="meta">{data.reason}</p> : !error && <p>Loading connection details…</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
