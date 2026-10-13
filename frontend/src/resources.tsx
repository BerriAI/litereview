import { Plug, Plus, RotateCcw, Trash2 } from "lucide-react";
import { Badge, Field } from "./ui";
import "./resources.css";

type Item = { id: string; name: string; [key: string]: any };

export function MCPAuthFields({
  data,
  update,
}: {
  data: any;
  update: (key: string, value: any) => void;
}) {
  return (
    <>
      <Field label="Authentication">
        <select
          value={data.auth_type || "bearer"}
          onChange={(e) => update("auth_type", e.target.value)}
        >
          <option value="oauth">OAuth — connect your account</option>
          <option value="bearer">Bearer token</option>
          <option value="none">No authentication</option>
        </select>
      </Field>
      {data.auth_type === "oauth" && (
        <>
          <p className="meta">
            You’ll approve access with the provider. LiteReview then tests the
            connection and discovers its tools. Selected agents share this
            connection.
          </p>
          <details className="oauth-advanced">
            <summary>Advanced OAuth settings</summary>
            <p>
              Usually automatic. If your provider requires a registered app,
              enter its client ID and register this redirect URI.
            </p>
            <code className="redirect-uri">
              {data.oauth?.redirect_uri ||
                location.origin + "/api/mcp/oauth/callback"}
            </code>
            <Field
              label="OAuth client ID"
              hint="Optional for servers that support automatic app registration."
            >
              <input
                value={data.oauth_client_id || ""}
                onChange={(e) => update("oauth_client_id", e.target.value)}
              />
            </Field>
            <Field
              label="OAuth client secret"
              hint={
                data.oauth?.has_client_secret
                  ? "Saved. Leave blank to keep it."
                  : "Only if required by your registered app."
              }
            >
              <input
                type="password"
                autoComplete="new-password"
                value={data.oauth_client_secret || ""}
                onChange={(e) => update("oauth_client_secret", e.target.value)}
              />
            </Field>
            <Field
              label="OAuth scopes"
              hint="Leave blank to use the scopes advertised by the server."
            >
              <input
                value={data.oauth_scope || ""}
                onChange={(e) => update("oauth_scope", e.target.value)}
              />
            </Field>
          </details>
        </>
      )}
    </>
  );
}

export function MCPServers({
  rows,
  busy,
  edit,
  connect,
  test,
  disconnect,
  remove,
}: {
  rows: Item[];
  busy: boolean;
  edit: (row?: Item) => void;
  connect: (row: Item) => void;
  test: (row: Item) => void;
  disconnect: (row: Item) => void;
  remove: (row: Item) => void;
}) {
  if (!rows.length)
    return (
      <div className="empty">
        <div className="empty-icon">
          <Plug size={24} />
        </div>
        <h3>Connect your first MCP server</h3>
        <p>
          Add a server URL, connect your account, and choose its tools on an
          agent.
        </p>
        <button className="primary" onClick={() => edit()}>
          <Plus size={16} />
          Add MCP server
        </button>
      </div>
    );
  return (
    <div className="mcp-servers">
      {rows.map((row) => {
        const oauth = row.auth_type === "oauth";
        const connected = oauth && row.oauth?.connected;
        const needsAuth = oauth && !connected;
        const tested = row.last_test?.ok && !needsAuth;
        return (
          <article className="mcp-server" key={row.id}>
            <div className="resource-card-head">
              <div>
                <button className="row-name" onClick={() => edit(row)}>
                  {row.name}
                </button>
                <small className="resource-endpoint">{row.base_url}</small>
              </div>
              <Badge
                tone={
                  tested
                    ? "green"
                    : needsAuth || row.last_test?.ok === false
                      ? "amber"
                      : ""
                }
              >
                {needsAuth
                  ? "Connect account"
                  : tested
                    ? `${row.last_test.tool_count} tools available`
                    : row.last_test?.ok === false
                      ? "Test failed"
                      : connected
                        ? "Authorized · not tested"
                        : "Not tested"}
              </Badge>
            </div>
            {row.last_test?.ok === false && (
              <p className="connection-error">{row.last_test.message}</p>
            )}
            {tested && (
              <details className="tool-catalog">
                <summary>Available tools ({row.tools?.length || 0})</summary>
                <ul>
                  {row.tools?.map((tool: any) => (
                    <li key={tool.name}>
                      <strong>{tool.name}</strong>
                      <span>{tool.description}</span>
                    </li>
                  ))}
                </ul>
                {!row.tools?.length && (
                  <p>
                    The server connected successfully but returned no tools.
                  </p>
                )}
              </details>
            )}
            <div className="resource-card-footer">
              <span>
                {oauth
                  ? "OAuth"
                  : row.auth_type === "none"
                    ? "No authentication"
                    : "Bearer token"}
                {tested && " · Select tools in an agent to use them"}
              </span>
              <div className="resource-actions">
                {oauth && (
                  <button
                    disabled={busy}
                    className={needsAuth ? "primary" : ""}
                    onClick={() => connect(row)}
                  >
                    {connected ? "Reconnect" : "Connect account"}
                  </button>
                )}
                {!needsAuth && (
                  <button disabled={busy} onClick={() => test(row)}>
                    Test connection
                  </button>
                )}
                <button disabled={busy} onClick={() => edit(row)}>
                  Settings
                </button>
                {connected && (
                  <button disabled={busy} onClick={() => disconnect(row)}>
                    Disconnect
                  </button>
                )}
                <button
                  className="icon"
                  disabled={busy}
                  aria-label={`Delete ${row.name}`}
                  onClick={() => remove(row)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function People({
  rows,
  targets,
  edit,
  reset,
  isAdmin,
  currentSub,
  busy,
  setRole,
}: {
  rows: Item[];
  targets: Item[];
  edit: (row: Item) => void;
  reset: (row: Item) => void;
  isAdmin: boolean;
  currentSub: string;
  busy: boolean;
  setRole: (row: Item, role: "admin" | "member") => void;
}) {
  if (!rows.length)
    return (
      <div className="empty">
        <h3>Your team appears here after Google sign-in</h3>
        <p>
          Share this workspace’s URL. People link GitHub to start reviews on
          connected repositories.
        </p>
      </div>
    );
  const statuses: Record<string, string> = {
    ready: "Connected",
    github_required: "GitHub not linked",
    sign_in_required: "Google sign-in required",
    not_signed_in: "Awaiting Google sign-in",
  };
  return (
    <>
      {isAdmin && (
        <p className="meta">
          Admins can view all workspace spend and manage admin roles. Choose
          Make admin beside a teammate to grant access immediately. GitHub
          linking is not required.
        </p>
      )}
      <div className="people-list">
        {rows.map((row) => (
          <article className="person-row" key={row.id}>
            <div className="person-identity">
              <strong>{row.email || row.name}</strong>
              {row.identity_sub && (
                <small>
                  {row.is_admin ? "Admin" : "Member"}
                  {row.identity_sub === currentSub ? " · You" : ""}
                  {row.admin_source === "configuration"
                    ? " · Managed by deployment"
                    : ""}
                </small>
              )}
              <small>
                {row.github_login
                  ? `@${row.github_login}`
                  : row.status === "not_signed_in"
                    ? `@${row.name} · saved restriction`
                    : "Google account"}
              </small>
            </div>
            <div>
              <Badge tone={row.status === "ready" ? "green" : "amber"}>
                {statuses[row.status]}
              </Badge>
            </div>
            <div className="person-access">
              <strong>
                {row.scope === "all"
                  ? "All agents and groups"
                  : row.target_ids.length
                    ? row.target_ids
                        .map(
                          (id: string) =>
                            targets.find((t) => t.id === id)?.name,
                        )
                        .filter(Boolean)
                        .join(", ")
                    : "Reviews blocked"}
              </strong>
              <small>
                {row.inherited ? "Workspace default" : "Custom access"}
              </small>
            </div>
            <div className="resource-actions">
              {isAdmin &&
                row.identity_sub &&
                (row.is_admin || row.status !== "sign_in_required") &&
                row.admin_source !== "configuration" &&
                (row.is_admin && row.identity_sub === currentSub ? (
                  <small>Another admin can remove your role.</small>
                ) : (
                  <button
                    disabled={busy}
                    aria-label={`${row.is_admin ? "Remove admin role from" : "Make admin:"} ${row.email}`}
                    onClick={() =>
                      setRole(row, row.is_admin ? "member" : "admin")
                    }
                  >
                    {row.is_admin ? "Remove admin" : "Make admin"}
                  </button>
                ))}
              {(row.github_login || row.status === "not_signed_in") && (
                <button disabled={busy} onClick={() => edit(row)}>
                  Edit access
                </button>
              )}
              {!row.inherited && (
                <button
                  disabled={busy}
                  aria-label={`Use workspace default for ${row.name}`}
                  title="Reset to workspace default"
                  onClick={() => reset(row)}
                >
                  <RotateCcw size={15} />
                  Use workspace default
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
