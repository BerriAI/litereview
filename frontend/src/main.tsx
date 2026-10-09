import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Layers,
  Plug,
  BookOpen,
  History,
  Plus,
  X,
  Trash2,
  ChevronRight,
  ArrowUpRight,
  LogOut,
  Check,
  Terminal,
  Globe,
  Monitor,
  Search,
  Loader2,
  Copy,
  Upload,
  CircleHelp,
  Menu,
  FileCode2,
  ArrowLeft,
  LockKeyhole,
  Users,
} from "lucide-react";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./style.css";
import { Documentation } from "./docs";
import {
  Brand,
  ProviderMark,
  Field,
  Badge,
  Toggle,
  Modal,
  FormSection,
  Capabilities,
  SearchPicker,
} from "./ui";

type Row = { id: string; name: string; [key: string]: any };
type Kind = "agents" | "groups" | "connections" | "skills" | "repos" | "access";
type Store = Record<Kind, Row[]>;
const empty: Store = {
  agents: [],
  groups: [],
  connections: [],
  skills: [],
  repos: [],
  access: [],
};
const icons = {
  access: Users,
  agents: Bot,
  groups: Layers,
  connections: Plug,
  skills: BookOpen,
  runs: History,
};
const titles = {
  access: "Access",
  agents: "PR agents",
  groups: "Agent groups",
  connections: "Connections",
  skills: "Skills",
  runs: "Runs",
};
const descriptions = {
  access:
    "Choose which agents and groups people and repositories can use. Both must have access to start a review.",
  agents: "Create reusable reviewers with their own model, tools, and skills.",
  groups: "Optional shortcuts for running several agents together.",
  connections:
    "Connect repositories, models, and the tools your reviewers need.",
  skills: "Reusable instructions and files for your agents.",
  runs: "Review progress, findings, and visual evidence from each agent.",
};
async function api(path: string, options: RequestInit = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Request failed (${response.status}).`,
    );
  return data;
}
const send = (method: string, body?: unknown) => ({
  method,
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});
function App() {
  const [session, setSession] = useState<any>(undefined);
  const [store, setStore] = useState<Store>(empty);
  const [path, setPath] = useState(location.pathname);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [accessSubject, setAccessSubject] = useState<"user" | "repo">("user");
  const [accessQuery, setAccessQuery] = useState("");
  const [tab, setTab] = useState("github");
  const [agentFilter, setAgentFilter] = useState("all");
  const [mobileNav, setMobileNav] = useState(false);
  const [setup, setSetup] = useState<any>({});
  const [runs, setRuns] = useState<Row[]>([]);
  const [run, setRun] = useState<any>(null);
  const [editor, setEditor] = useState<{
    kind: Kind;
    data: any;
    id?: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [harnesses, setHarnesses] = useState<
    { id: string; name: string; version: string }[]
  >([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState("");
  const [testingModel, setTestingModel] = useState(false);
  const [checkRunId, setCheckRunId] = useState<string | null>(null);
  const [modelCheck, setModelCheck] = useState<{
    error: boolean;
    text: string;
  } | null>(null);
  const [availableRepos, setAvailableRepos] = useState<any[]>([]);
  const [githubForm, setGithubForm] = useState(false);
  const [githubCreate, setGithubCreate] = useState(false);
  const [appOwnerType, setAppOwnerType] = useState("organization");
  const [appOwner, setAppOwner] = useState("");
  const [githubData, setGithubData] = useState<Record<string, string>>({});
  const section = (path.split("/")[1] || "agents") as keyof typeof titles;
  const active = section in titles ? section : "agents";
  const modelConnection =
    editor?.kind === "agents" ? editor.data.connection_id : "";
  useEffect(() => {
    if (session?.user?.admin)
      api("/harnesses")
        .then(setHarnesses)
        .catch(() => {});
  }, [session?.user]);
  useEffect(() => {
    setModelError("");
    setLoadingModels(false);
    if (!modelConnection || models[modelConnection]) return;
    let active = true;
    setLoadingModels(true);
    api("/connections/" + modelConnection + "/test", send("POST"))
      .then((result) => {
        if (active)
          setModels((current) => ({
            ...current,
            [modelConnection]: result.models || [],
          }));
      })
      .catch(() => {
        if (active)
          setModelError(
            "Could not load models. Enter a model ID or try loading again.",
          );
      })
      .finally(() => {
        if (active) setLoadingModels(false);
      });
    return () => {
      active = false;
    };
  }, [modelConnection]);
  useEffect(() => {
    setModelCheck(null);
  }, [
    editor?.data.model,
    editor?.data.harness,
    modelConnection,
    JSON.stringify(editor?.data.capabilities),
  ]);
  const navigate = (next: string) => {
    history.pushState({}, "", next);
    setPath(next);
    setMobileNav(false);
    setEditor(null);
    setRun(null);
    setQuery("");
    setError("");
    setNotice("");
  };
  useEffect(() => {
    const fn = () => setPath(location.pathname);
    addEventListener("popstate", fn);
    return () => removeEventListener("popstate", fn);
  }, []);
  useEffect(() => {
    if (!mobileNav) return;
    const trigger = document.activeElement as HTMLElement | null;
    document.querySelector<HTMLButtonElement>(".mobile-close")?.focus();
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNav(false);
    };
    const resize = () => {
      if (innerWidth >= 768) setMobileNav(false);
    };
    addEventListener("keydown", dismiss);
    addEventListener("resize", resize);
    return () => {
      removeEventListener("keydown", dismiss);
      removeEventListener("resize", resize);
      trigger?.focus();
    };
  }, [mobileNav]);
  useEffect(() => {
    api("/session")
      .then(setSession)
      .catch((e) => setError(e.message));
  }, []);
  const load = async () => {
    const kinds = Object.keys(empty) as Kind[];
    const values = await Promise.all(kinds.map((k) => api("/resources/" + k)));
    setStore(Object.fromEntries(kinds.map((k, i) => [k, values[i]])) as Store);
    setSetup(await api("/setup"));
  };
  useEffect(() => {
    if (session?.user?.admin) load().catch((e) => setError(e.message));
  }, [session]);
  useEffect(() => {
    if (!session?.user?.admin || active !== "runs") return;
    let alive = true;
    const refresh = async () => {
      try {
        const data = path.split("/")[2]
          ? await api("/runs/" + path.split("/")[2])
          : await api("/runs");
        if (alive) {
          if (Array.isArray(data)) setRuns(data);
          else setRun(data);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [session, active, path]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(t);
  }, [notice]);
  const mutate = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      if (editor) setFormError((e as Error).message);
      else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const edit = (kind: Kind, item?: Row) => {
    setNotice("");
    setAccessQuery("");
    const defaults: Record<Kind, any> = {
      access: {
        name: "",
        subject: accessSubject,
        scope: "all",
        target_ids: [],
      },
      agents: {
        name: "",
        harness: "codex",
        instructions:
          "Review this PR for actionable defects. Explain a concrete failure scenario and cite the affected code. Report no findings when appropriate.",
        connection_id:
          store.connections.find((c) => c.type !== "mcp")?.id || "",
        model: "",
        capabilities: [],
        skill_ids: [],
        mcps: [],
      },
      groups: {
        name: "",
        description: "",
        members: [],
      },
      connections: {
        name: "",
        type: tab === "mcp" ? "mcp" : "gateway",
        base_url: "",
        api_key: "",
      },
      skills: {
        name: "",
        description: "",
        instructions: "# Review instructions\n\n",
        files: {},
      },
      repos: {
        name: "",
        installation_id: 0,
        setup_command: "",
        start_command: "",
        readiness_url: "",
        test_secrets: {},
      },
    };
    setEditor({
      kind,
      id: item?.id,
      data: structuredClone(
        item
          ? {
              ...defaults[kind],
              ...item,
              ...(kind === "connections" ? { api_key: "" } : {}),
              ...(kind === "repos" ? { test_secrets: {} } : {}),
            }
          : defaults[kind],
      ),
    });
    setFormError("");
  };
  const update = (key: string, value: any) =>
    setEditor((e) => e && { ...e, data: { ...e.data, [key]: value } });
  const toggleArray = (key: string, value: string, on: boolean) =>
    update(
      key,
      on
        ? [...editor!.data[key], value]
        : editor!.data[key].filter((v: string) => v !== value),
    );
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editor) return;
    setBusy(true);
    setFormError("");
    try {
      await api(
        "/resources/" + editor.kind + (editor.id ? "/" + editor.id : ""),
        send(editor.id ? "PUT" : "POST", editor.data),
      );
      await load();
      setEditor(null);
      setNotice("Saved successfully.");
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = (kind: Kind, item: Row) => {
    if (
      confirm(
        kind === "access"
          ? `Remove access for “${item.name}”?`
          : `Delete “${item.name}”?`,
      )
    )
      mutate(async () => {
        await api(`/resources/${kind}/${item.id}`, send("DELETE"));
        await load();
        setNotice("Deleted.");
      });
  };
  const testConnection = (item: Row) =>
    mutate(async () => {
      const r = await api(`/connections/${item.id}/test`, send("POST"));
      if (r.models) setModels((m) => ({ ...m, [item.id]: r.models }));
      await load();
      setNotice(
        r.models
          ? `Connected · ${r.models.length} models available.`
          : `Connected · ${r.tools.length} tools discovered.`,
      );
    });
  const modelConnections = store.connections.filter((c) => c.type !== "mcp");
  const mcpConnections = store.connections.filter((c) => c.type === "mcp");
  if (session === undefined)
    return (
      <div className="center">
        <Loader2 className="spin" />
        <p>{error || "Loading LiteReview…"}</p>
      </div>
    );
  if (!session.user)
    return (
      <div className="login">
        <div className="login-brand">
          <Brand />
        </div>
        <div className="login-card">
          <h1>Sign in to LiteReview</h1>
          <p>Configure your reviewers. Run them from GitHub.</p>
          {!session.sso_required && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                mutate(async () => {
                  await api("/login", send("POST", { token: f.get("token") }));
                  setSession(await api("/session"));
                });
              }}
            >
              <Field
                label="Workspace access key"
                hint="Use the ADMIN_TOKEN from your deployment settings."
              >
                <input
                  name="token"
                  type="password"
                  required
                  autoComplete="current-password"
                />
              </Field>
              {error && (
                <div role="alert" className="error">
                  {error}
                </div>
              )}
              <button className="primary full" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
          )}
          {session.sso_required &&
            (session.google_login ? (
              <a className="button primary full" href="/api/auth/google">
                Sign in with Google
              </a>
            ) : (
              <p role="alert">
                Google sign-in is being configured. Contact your workspace
                administrator.
              </p>
            ))}
          {!session.sso_required && session.github_login && (
            <a className="button full" href="/api/auth/github">
              <ProviderMark type="github" />
              Sign in with GitHub
            </a>
          )}
          <small className="login-note">
            <LockKeyhole size={13} />
            {session.sso_required
              ? "Access is restricted to your Google Workspace."
              : "Access is restricted to workspace administrators."}
          </small>
        </div>
      </div>
    );
  if (session.sso_required && (path === "/account" || !session.user.admin))
    return (
      <div className="login">
        <div className="login-brand">
          <Brand />
        </div>
        <div className="login-card">
          <h1>Your LiteReview account</h1>
          <p>Signed in as {session.user.email}.</p>
          <p>
            {session.user.github_login
              ? `GitHub account: @${session.user.github_login}`
              : "Link your GitHub account to request reviews from PR comments."}
          </p>
          {session.github_login ? (
            <a className="button full" href="/api/auth/github">
              {session.user.github_login
                ? "Change linked GitHub account"
                : "Link GitHub account"}
            </a>
          ) : (
            <p>An administrator needs to finish connecting the GitHub App.</p>
          )}
          <p>
            Your Google authorization expires{" "}
            {new Date(session.user.expires_at * 1000).toLocaleString()}. Sign in
            again to renew it. Existing People and Repository access grants
            still apply.
          </p>
          <p>
            Example: <code>@litereview security t=10 frontend t=5</code>. Each
            duration is a minimum number of minutes for the preceding agent or
            group.
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {session.user.admin && (
            <button
              className="primary full"
              onClick={() => navigate("/agents")}
            >
              Open workspace
            </button>
          )}
          <button
            className="button full"
            onClick={() =>
              mutate(async () => {
                await api("/logout", send("POST"));
                setSession(await api("/session"));
              })
            }
          >
            Sign out
          </button>
        </div>
      </div>
    );
  const rows =
    active === "runs"
      ? runs
      : active === "access"
        ? store.access.filter((grant) => grant.subject === accessSubject)
        : active === "connections"
          ? tab === "github"
            ? store.repos
            : store.connections.filter((c) =>
                tab === "mcp" ? c.type === "mcp" : c.type !== "mcp",
              )
          : store[active as Kind];
  const filtered = rows.filter((row) => {
    const searchable = [row.name, row.model, row.description, row.repo, row.pr]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (
      searchable.includes(query.toLowerCase()) &&
      (active !== "agents" ||
        agentFilter === "all" ||
        (agentFilter === "code"
          ? !row.capabilities.length
          : row.capabilities.includes(agentFilter)))
    );
  });
  const createLabel =
    active === "access"
      ? accessSubject === "user"
        ? "Add person"
        : "Add repository access"
      : active === "agents"
        ? "Create agent"
        : active === "groups"
          ? "Create group"
          : active === "skills"
            ? "Create skill"
            : tab === "github"
              ? "Add repository"
              : "Add connection";
  const PageIcon = icons[active];
  const isRunDetail = active === "runs" && Boolean(path.split("/")[2]);
  const newKind: Kind =
    active === "connections"
      ? tab === "github"
        ? "repos"
        : "connections"
      : (active as Kind);
  return (
    <div className="app">
      <aside className={mobileNav ? "sidebar open" : "sidebar"}>
        <div className="sidebar-brand">
          <button
            className="brand"
            aria-label="LiteReview home"
            onClick={() => navigate("/")}
          >
            <Brand />
          </button>
          <button
            className="icon mobile-close"
            aria-label="Close navigation"
            onClick={() => setMobileNav(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav aria-label="Main navigation">
          {[
            ["Review", ["agents", "groups", "runs"]],
            ["Workspace", ["connections", "skills", "access"]],
          ].map(([label, keys]) => (
            <div className="nav-group" key={label as string}>
              <div className="workspace-label">{label as string}</div>
              {(keys as string[]).map((key) => {
                const Icon = icons[key as keyof typeof icons];
                return (
                  <button
                    key={key}
                    aria-label={titles[key as keyof typeof titles]}
                    aria-current={active === key ? "page" : undefined}
                    className={active === key ? "selected" : ""}
                    onClick={() => navigate("/" + key)}
                  >
                    <Icon size={17} />
                    <span className="nav-label">
                      {titles[key as keyof typeof titles]}
                    </span>
                    {key === "agents" && store.agents.length > 0 && (
                      <span className="nav-count">{store.agents.length}</span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-help">
          <a href="/docs">
            <BookOpen size={15} /> Documentation
          </a>
        </div>
        <div className="sidebar-bottom">
          <span className="avatar">
            {session.user.login.slice(0, 1).toUpperCase()}
          </span>
          <div>
            <strong>
              {session.user.login === "owner"
                ? "Workspace owner"
                : session.user.login}
            </strong>
            <small>Administrator</small>
          </div>
          {session.sso_required && (
            <button
              className="icon-button"
              title="Google and GitHub account"
              onClick={() => navigate("/account")}
            >
              <Users size={16} />
            </button>
          )}
          <button
            className="icon"
            title="Sign out"
            aria-label="Sign out"
            onClick={() =>
              mutate(async () => {
                await api("/logout", send("POST"));
                setSession(await api("/session"));
              })
            }
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-hidden="true"
          tabIndex={-1}
          onClick={() => setMobileNav(false)}
        />
      )}
      <main id="main-content" inert={mobileNav}>
        <div className="topbar">
          <button
            className="icon mobile-menu"
            aria-label="Open navigation"
            aria-expanded={mobileNav}
            onClick={() => setMobileNav(true)}
          >
            <Menu size={18} />
          </button>
          <span className="topbar-product">LiteReview</span>
          <ChevronRight size={13} />
          <span>{titles[active]}</span>
        </div>
        <div className="page">
          <header className="page-heading">
            <div>
              <div className="title-line">
                <PageIcon size={22} />
                <h1>{isRunDetail ? "Review results" : titles[active]}</h1>
              </div>
              <p>{descriptions[active]}</p>
            </div>
            {active !== "runs" && (
              <button className="primary" onClick={() => edit(newKind)}>
                <Plus size={16} />
                {createLabel}
              </button>
            )}
          </header>
          {error && (
            <div className="error" role="alert">
              {error}
              <button
                className="icon"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="notice" role="status">
              <Check size={16} />
              {notice}
            </div>
          )}
          {active === "connections" && (
            <>
              <div className="tabs">
                {[
                  ["github", "GitHub"],
                  ["models", "Models"],
                  ["mcp", "MCP servers"],
                ].map(([k, t]) => (
                  <button
                    key={k}
                    aria-pressed={tab === k}
                    className={tab === k ? "active" : ""}
                    onClick={() => {
                      setTab(k);
                      setQuery("");
                    }}
                  >
                    {t}
                  </button>
                ))}
              </div>
              {tab === "github" && (
                <div className="setup-panel">
                  <div className="setup-title">
                    <ProviderMark type="github" />
                    <div>
                      <h3>
                        {setup.github
                          ? "GitHub App connected"
                          : "Connect your GitHub App"}
                      </h3>
                      <p>
                        Install on selected repositories, then add them below.
                        Your organization may require owner approval.
                      </p>
                    </div>
                    <Badge tone={setup.github ? "green" : ""}>
                      {setup.github ? "Connected" : "Setup needed"}
                    </Badge>
                  </div>
                  <div className="actions">
                    <button
                      disabled={busy}
                      onClick={() => setGithubCreate(true)}
                    >
                      Create GitHub App <ArrowUpRight size={14} />
                    </button>
                    <button onClick={() => setGithubForm(true)}>
                      Use existing App
                    </button>
                    {setup.github_app_url && (
                      <a
                        className="button"
                        href={setup.github_app_url + "/installations/new"}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Manage installation <ArrowUpRight size={14} />
                      </a>
                    )}
                    <button
                      disabled={busy}
                      onClick={() =>
                        mutate(async () => {
                          setAvailableRepos(await api("/github/repositories"));
                          setNotice(
                            "Repository list refreshed. Choose Add repository to connect one.",
                          );
                        })
                      }
                    >
                      Refresh repositories
                    </button>
                  </div>
                  <div className="setup-note">
                    <span className={"dot " + (setup.modal ? "ok" : "")} />
                    {setup.modal
                      ? "Modal credentials configured"
                      : "Set Modal credentials in your deployment environment"}
                    <code>{setup.webhook_url}</code>
                  </div>
                </div>
              )}
              {tab === "mcp" && (
                <div className="info">
                  <CircleHelp size={16} />
                  Add a remote Streamable HTTP MCP server, test it to discover
                  tools, then select its tools on each agent.
                </div>
              )}
            </>
          )}
          {active === "runs" && path.split("/")[2] ? (
            <>
              <button className="back" onClick={() => navigate("/runs")}>
                <ArrowLeft size={14} /> All runs
              </button>
              {run ? (
                <div className="run-detail">
                  <div className="run-head">
                    <a
                      href={`https://github.com/${run.repo}/pull/${run.pr}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {run.repo} #{run.pr} <ArrowUpRight size={15} />
                    </a>
                    <Badge
                      tone={
                        run.status === "completed"
                          ? "green"
                          : run.status === "incomplete"
                            ? "amber"
                            : ""
                      }
                    >
                      {run.status}
                    </Badge>
                    <code>{run.head.slice(0, 12)}</code>
                  </div>
                  <dl className="run-summary">
                    <div>
                      <dt>Reviewers</dt>
                      <dd>
                        {run.jobs.length}
                        <span>
                          {" "}
                          independent{" "}
                          {run.jobs.length === 1 ? "agent" : "agents"}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>Findings</dt>
                      <dd>
                        {run.jobs.reduce(
                          (n: number, job: any) =>
                            n + (job.result.findings?.length || 0),
                          0,
                        )}
                        <span> reported</span>
                      </dd>
                    </div>
                    <div>
                      <dt>Triggered by</dt>
                      <dd className="summary-caller">{run.actor}</dd>
                    </div>
                  </dl>
                  {run.error && <div className="error">{run.error}</div>}
                  {run.jobs.map((job: any, index: number) => (
                    <article className="job" key={job.id}>
                      <div className="job-title">
                        <span className="job-number">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <h3>{job.config.name}</h3>
                        <span>
                          {job.config.target_kind === "agent"
                            ? "Direct call"
                            : job.config.group}{" "}
                          · Instance {job.config.instance}
                        </span>
                        <Badge
                          tone={
                            job.result.limitations?.length
                              ? "amber"
                              : job.status === "completed"
                                ? "green"
                                : job.status === "failed" ||
                                    job.status === "timed_out"
                                  ? "amber"
                                  : ""
                          }
                        >
                          {job.result.limitations?.length
                            ? "incomplete"
                            : job.status}
                        </Badge>
                      </div>
                      <p className="meta">
                        <code>{job.config.model}</code>
                      </p>
                      {job.result.summary && <p>{job.result.summary}</p>}
                      {job.status === "completed" &&
                        !job.result.findings?.length &&
                        !job.result.limitations?.length && (
                          <div className="clean">
                            <Check size={17} />
                            No findings
                          </div>
                        )}
                      {job.result.limitations?.map((l: string, i: number) => (
                        <div className="warning" key={i}>
                          Incomplete check: {l}
                        </div>
                      ))}
                      {job.result.findings?.map((f: any, i: number) => (
                        <div className="finding" key={i}>
                          <h4>
                            <Badge
                              tone={
                                f.severity === "high" ||
                                f.severity === "critical"
                                  ? "red"
                                  : "amber"
                              }
                            >
                              {f.severity}
                            </Badge>
                            {f.title}
                          </h4>
                          <p>{f.explanation}</p>
                          {f.evidence?.length > 0 && (
                            <ul className="finding-evidence">
                              {f.evidence.map(
                                (evidence: string, idx: number) => (
                                  <li key={idx}>{evidence}</li>
                                ),
                              )}
                            </ul>
                          )}
                          {f.file && (
                            <a
                              className="source-link"
                              href={`https://github.com/${run.repo}/blob/${run.head}/${f.file.split("/").map(encodeURIComponent).join("/")}${f.line ? "#L" + f.line : ""}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <FileCode2 size={13} />
                              <code>
                                {f.file}
                                {f.line ? ":" + f.line : ""}
                              </code>
                              <ArrowUpRight size={12} />
                            </a>
                          )}
                        </div>
                      ))}
                      {job.artifacts.length > 0 && (
                        <details>
                          <summary>{job.artifacts.length} screenshots</summary>
                          <div className="screenshots">
                            {job.artifacts.map((a: any) => (
                              <a
                                key={a.id}
                                href={"/api/artifacts/" + a.id}
                                target="_blank"
                                rel="noreferrer"
                              >
                                <img
                                  src={"/api/artifacts/" + a.id}
                                  loading="lazy"
                                  alt={a.name}
                                />
                                <span>{a.name}</span>
                              </a>
                            ))}
                          </div>
                        </details>
                      )}
                      <details>
                        <summary>
                          Activity & execution · {job.logs.length} events
                        </summary>
                        <div className="execution-meta">
                          <span>{job.model_calls} model calls</span>
                          {job.sandbox_id && (
                            <span>
                              Sandbox <code>{job.sandbox_id}</code>
                            </span>
                          )}
                        </div>
                        <div className="logs">
                          {job.logs.map((l: any, i: number) => (
                            <div key={i}>
                              <time>
                                {new Date(l.time * 1000).toLocaleTimeString()}
                              </time>
                              {l.message}
                            </div>
                          ))}
                          {!job.logs.length && (
                            <p>Waiting for the sandbox to start.</p>
                          )}
                        </div>
                      </details>
                    </article>
                  ))}
                </div>
              ) : (
                <p>Loading run…</p>
              )}
            </>
          ) : (
            <>
              {active === "access" && (
                <div className="tabs" aria-label="Access settings">
                  {(
                    [
                      ["user", "People"],
                      ["repo", "Repositories"],
                    ] as const
                  ).map(([subject, label]) => (
                    <button
                      key={subject}
                      className={accessSubject === subject ? "active" : ""}
                      aria-pressed={accessSubject === subject}
                      onClick={() => {
                        setAccessSubject(subject);
                        setQuery("");
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {active === "agents" && (
                <div className="tabs agent-tabs" aria-label="Filter agents">
                  {[
                    ["all", "All agents"],
                    ["code", "Code only"],
                    ["browser", "Browser"],
                    ["computer", "Computer use"],
                  ].map(([value, label]) => (
                    <button
                      key={value}
                      aria-pressed={agentFilter === value}
                      className={agentFilter === value ? "active" : ""}
                      onClick={() => setAgentFilter(value)}
                    >
                      {label}
                      {value === "all" && (
                        <span className="tab-count">{store.agents.length}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
              <div className="table-toolbar">
                <div className="search">
                  <Search size={16} />
                  <input
                    aria-label="Search"
                    placeholder={
                      "Search " +
                      (active === "connections"
                        ? tab === "github"
                          ? "repositories"
                          : "connections"
                        : titles[active].toLowerCase()) +
                      "…"
                    }
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>
                <span className="result-count">
                  {filtered.length}
                  {filtered.length !== rows.length
                    ? ` of ${rows.length}`
                    : ""}{" "}
                  {active === "connections"
                    ? tab === "github"
                      ? "repositories"
                      : "connections"
                    : active === "runs"
                      ? "runs"
                      : active === "access"
                        ? accessSubject === "user"
                          ? filtered.length === 1
                            ? "person"
                            : "people"
                          : filtered.length === 1
                            ? "repository"
                            : "repositories"
                        : active}
                </span>
              </div>
              {filtered.length === 0 ? (
                <div className="empty">
                  <div className="empty-icon">
                    {React.createElement(icons[active], { size: 24 })}
                  </div>
                  <h3>
                    {query
                      ? "No matches"
                      : active === "agents" && agentFilter !== "all"
                        ? "No agents with this capability"
                        : active === "agents"
                          ? "Create your first PR agent"
                          : active === "access"
                            ? accessSubject === "user"
                              ? "Add people who can start reviews"
                              : "Choose repository access"
                            : active === "groups"
                              ? "Build a review group"
                              : active === "skills"
                                ? "Add your review playbook"
                                : active === "runs"
                                  ? "Your reviews will appear here"
                                  : tab === "github"
                                    ? "Connect a repository"
                                    : "Add your first connection"}
                  </h3>
                  <p>
                    {query
                      ? "Try a different search."
                      : active === "runs"
                        ? "Post @litereview followed by agent or group names on a connected PR. Your review will appear here."
                        : active === "agents" && !modelConnections.length
                          ? "Connect a model provider or gateway, then create your first reviewer."
                          : active === "groups"
                            ? "Choose existing PR agents and set how many instances to run."
                            : descriptions[active]}
                  </p>
                  {!query && active !== "runs" && (
                    <button
                      onClick={() =>
                        active === "agents" && !modelConnections.length
                          ? (navigate("/connections"), setTab("models"))
                          : edit(newKind)
                      }
                    >
                      {active === "agents" && !modelConnections.length
                        ? "Connect a model"
                        : createLabel}
                      <ChevronRight size={15} />
                    </button>
                  )}
                </div>
              ) : (
                <div
                  className="table-wrap"
                  tabIndex={0}
                  role="region"
                  aria-label={titles[active] + " table"}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>
                          {active === "runs"
                            ? "Pull request"
                            : active === "access"
                              ? accessSubject === "user"
                                ? "GitHub account"
                                : "Repository"
                              : "Name"}
                        </th>
                        <th>
                          {active === "access"
                            ? "Access"
                            : active === "agents"
                              ? "Model"
                              : active === "groups"
                                ? "Agents"
                                : active === "skills"
                                  ? "Used by"
                                  : active === "runs"
                                    ? "Status"
                                    : tab === "github"
                                      ? "Environment"
                                      : "Type"}
                        </th>
                        <th>
                          {active === "access"
                            ? "Agents and groups"
                            : active === "agents"
                              ? "Capabilities"
                              : active === "groups"
                                ? "Includes"
                                : active === "skills"
                                  ? "Supporting files"
                                  : active === "runs"
                                    ? "Started"
                                    : "Connection"}
                        </th>
                        <th>
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <button
                              className="row-name"
                              onClick={() =>
                                active === "runs"
                                  ? navigate("/runs/" + row.id)
                                  : edit(newKind, row)
                              }
                            >
                              {active === "runs"
                                ? `${row.repo} #${row.pr}`
                                : row.name}
                            </button>
                            <small>
                              {active === "groups" || active === "skills"
                                ? row.description
                                : active === "agents"
                                  ? `${row.skill_ids.length} ${row.skill_ids.length === 1 ? "skill" : "skills"} · ${row.mcps.length} MCP ${row.mcps.length === 1 ? "connection" : "connections"}`
                                  : active === "runs"
                                    ? row.head.slice(0, 12)
                                    : ""}
                            </small>
                          </td>
                          <td>
                            {active === "access" ? (
                              row.scope === "all" ? (
                                "All"
                              ) : (
                                "Specific"
                              )
                            ) : active === "agents" ? (
                              <div className="model-cell">
                                <code>{row.model}</code>
                                <small>
                                  {harnesses.find(
                                    (h) => h.id === (row.harness || "builtin"),
                                  )?.name || "Built-in"}{" "}
                                  ·{" "}
                                  {store.connections.find(
                                    (c) => c.id === row.connection_id,
                                  )?.name || "Connection unavailable"}
                                </small>
                              </div>
                            ) : active === "groups" ? (
                              <span>
                                {row.members.reduce(
                                  (a: number, m: any) => a + m.count,
                                  0,
                                )}{" "}
                                instances
                                <small>
                                  {row.members.length} reusable{" "}
                                  {row.members.length === 1
                                    ? "agent"
                                    : "agents"}
                                </small>
                              </span>
                            ) : active === "skills" ? (
                              `${store.agents.filter((a) => a.skill_ids.includes(row.id)).length} agents`
                            ) : active === "runs" ? (
                              <Badge
                                tone={
                                  row.status === "completed"
                                    ? "green"
                                    : row.status === "incomplete"
                                      ? "amber"
                                      : ""
                                }
                              >
                                {row.status}
                              </Badge>
                            ) : tab === "github" ? (
                              row.start_command ? (
                                "App configured"
                              ) : (
                                "Repository only"
                              )
                            ) : (
                              <span className="connection-type">
                                <ProviderMark type={row.type} />
                                {{
                                  gateway: "Gateway",
                                  openai: "OpenAI",
                                  anthropic: "Anthropic",
                                  mcp: "MCP",
                                }[row.type as string] || row.type}
                              </span>
                            )}
                          </td>
                          <td>
                            {active === "access" ? (
                              row.scope === "all" ? (
                                <span>
                                  All agents and groups
                                  <small>Includes future additions</small>
                                </span>
                              ) : (
                                <span>
                                  {row.target_ids
                                    .map(
                                      (id: string) =>
                                        [...store.agents, ...store.groups].find(
                                          (target) => target.id === id,
                                        )?.name,
                                    )
                                    .filter(Boolean)
                                    .join(", ") || "None selected"}
                                </span>
                              )
                            ) : active === "agents" ? (
                              <Capabilities values={row.capabilities} />
                            ) : active === "groups" ? (
                              <div className="repo-list">
                                {row.members.map((member: any) => (
                                  <span key={member.agent_id}>
                                    {
                                      store.agents.find(
                                        (agent) => agent.id === member.agent_id,
                                      )?.name
                                    }{" "}
                                    × {member.count}
                                  </span>
                                ))}
                              </div>
                            ) : active === "skills" ? (
                              `${Object.keys(row.files || {}).length} files`
                            ) : active === "runs" ? (
                              new Date(row.created_at * 1000).toLocaleString()
                            ) : tab === "github" ? (
                              `Installation ${row.installation_id}`
                            ) : (
                              <span className="url">
                                {row.base_url || "Direct provider"}
                              </span>
                            )}
                          </td>
                          <td className="row-actions">
                            {active === "connections" && tab !== "github" && (
                              <button
                                disabled={busy}
                                onClick={() => testConnection(row)}
                              >
                                Test
                              </button>
                            )}
                            {active !== "runs" && (
                              <button
                                className="icon"
                                aria-label={
                                  (active === "access"
                                    ? "Remove access for "
                                    : "Delete ") + row.name
                                }
                                onClick={() => remove(newKind, row)}
                              >
                                <Trash2 size={15} />
                              </button>
                            )}
                            {active === "runs" && (
                              <button
                                className="icon"
                                aria-label="Open run"
                                onClick={() => navigate("/runs/" + row.id)}
                              >
                                <ChevronRight size={18} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {active === "groups" && store.groups.length > 0 && (
                <div className="info">
                  <ProviderMark type="github" />
                  <span>
                    Call agents and groups together:{" "}
                    <code>
                      @litereview{" "}
                      {store.groups
                        .slice(0, 3)
                        .map((g) => g.name)
                        .join(" ")}
                    </code>
                    . Each membership runs independently.
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      </main>
      {editor && (
        <Modal
          page={["agents", "groups", "skills", "repos"].includes(editor.kind)}
          description={
            {
              agents:
                "Set the model, review instructions, and tools for this reusable agent.",
              groups: "Choose agents and how many instances to run together.",
              access:
                "Choose which agents and groups can be called from PR comments.",
              skills: "Write instructions once and attach them to any agent.",
              repos: "Configure the environment used to run this repository.",
              connections:
                "Make a provider or tool server available to your agents.",
            }[editor.kind]
          }
          title={
            editor.kind === "access"
              ? (editor.id ? "Edit " : "Add ") +
                (editor.data.subject === "user"
                  ? "person"
                  : "repository access")
              : (editor.id ? "Edit " : "Create ") +
                {
                  access: "access",
                  agents: "PR agent",
                  groups: "agent group",
                  connections: "connection",
                  skills: "skill",
                  repos: "repository",
                }[editor.kind]
          }
          close={() => !busy && setEditor(null)}
        >
          <form onSubmit={save} className="resource-editor-form">
            <div className={"dialog-body resource-form " + editor.kind}>
              <FormSection
                title={
                  editor.kind === "groups"
                    ? "Group details"
                    : editor.kind === "repos"
                      ? "Repository"
                      : "Details"
                }
                description={
                  editor.kind === "groups"
                    ? "The command name is how you call this group in a PR."
                    : editor.kind === "agents"
                      ? "The command name is how you call this agent in a PR."
                      : "Identify this resource in your workspace."
                }
              >
                {editor.kind === "access" && editor.data.subject === "repo" ? (
                  <SearchPicker
                    label="Repository"
                    value={editor.data.name}
                    options={store.repos.map((repo) => ({
                      value: repo.name.toLowerCase(),
                      label: repo.name,
                    }))}
                    onChange={(value) => update("name", value)}
                  />
                ) : editor.kind === "repos" ? (
                  <Field
                    label="Repository"
                    hint="Select a repository from the GitHub App installation, or enter owner/repo."
                  >
                    <input
                      list="repo-list"
                      value={editor.data.name}
                      onChange={(e) => {
                        update("name", e.target.value);
                        const match = availableRepos.find(
                          (r) => r.name === e.target.value,
                        );
                        if (match)
                          update("installation_id", match.installation_id);
                      }}
                      placeholder="BerriAI/my-repo"
                      required
                    />
                    <datalist id="repo-list">
                      {availableRepos.map((r) => (
                        <option key={r.name} value={r.name} />
                      ))}
                    </datalist>
                  </Field>
                ) : (
                  <Field
                    label={
                      ["agents", "groups"].includes(editor.kind)
                        ? "Command name"
                        : editor.kind === "access"
                          ? "GitHub username"
                          : "Name"
                    }
                    hint={
                      ["agents", "groups"].includes(editor.kind)
                        ? "Unique across agents and groups. Letters, numbers, hyphens, and underscores; start with a letter. @litereview " +
                          (editor.data.name || "name")
                        : editor.kind === "access"
                          ? "They must also have access to the repository on GitHub."
                          : undefined
                    }
                  >
                    <input
                      value={editor.data.name}
                      onChange={(e) => update("name", e.target.value)}
                      required
                      maxLength={
                        ["agents", "groups"].includes(editor.kind) ? 40 : 100
                      }
                      pattern={
                        ["agents", "groups"].includes(editor.kind)
                          ? "[a-zA-Z][a-zA-Z0-9_\\-]*"
                          : undefined
                      }
                      placeholder={
                        editor.kind === "agents"
                          ? "frontend"
                          : editor.kind === "groups"
                            ? "security"
                            : ""
                      }
                    />
                  </Field>
                )}
                {editor.id &&
                  ["agents", "groups"].includes(editor.kind) &&
                  store[editor.kind].some(
                    (row) =>
                      row.id === editor.id && row.name === editor.data.name,
                  ) && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        navigator.clipboard
                          .writeText(
                            "@litereview " + editor.data.name.toLowerCase(),
                          )
                          .then(() => setNotice("PR comment copied."))
                          .catch(() =>
                            setFormError(
                              "Could not access the clipboard. Copy the example above.",
                            ),
                          );
                      }}
                    >
                      <Copy size={14} /> Copy PR comment
                    </button>
                  )}
                {editor.kind === "groups" && (
                  <Field label="Description">
                    <input
                      value={editor.data.description}
                      onChange={(e) => update("description", e.target.value)}
                      placeholder="What does this group review?"
                    />
                  </Field>
                )}
              </FormSection>
              {editor.kind === "agents" && (
                <>
                  {editor.id &&
                    store.groups.some((g) =>
                      g.members.some((m: any) => m.agent_id === editor.id),
                    ) && (
                      <div className="info">
                        Changes apply to future runs in:{" "}
                        {store.groups
                          .filter((g) =>
                            g.members.some(
                              (m: any) => m.agent_id === editor.id,
                            ),
                          )
                          .map((g) => g.name)
                          .join(", ")}
                        .
                      </div>
                    )}
                  <FormSection
                    title="Harness & model"
                    description="Each review starts only this harness in its own Modal sandbox."
                  >
                    <SearchPicker
                      label="Harness"
                      disabled={testingModel}
                      value={editor.data.harness || "builtin"}
                      options={harnesses.map((h) => ({
                        value: h.id,
                        label: h.name,
                      }))}
                      onChange={(value) => update("harness", value)}
                    />
                    {(editor.data.harness === "codex" ||
                      editor.data.harness === "claude-code") && (
                      <small>
                        {editor.data.harness === "codex"
                          ? "Requires a Responses-compatible connection. A gateway can route it to Claude or other models."
                          : "Requires a Messages-compatible connection, through Anthropic or your gateway."}
                      </small>
                    )}
                    <div className="form-grid">
                      <SearchPicker
                        label="Model connection"
                        disabled={testingModel}
                        value={editor.data.connection_id}
                        options={modelConnections.map((connection) => ({
                          value: connection.id,
                          label: connection.name,
                        }))}
                        onChange={(value) =>
                          setEditor(
                            (current) =>
                              current && {
                                ...current,
                                data: {
                                  ...current.data,
                                  connection_id: value,
                                  model: "",
                                },
                              },
                          )
                        }
                      />
                      <SearchPicker
                        key={editor.data.connection_id}
                        label="Model"
                        disabled={testingModel}
                        value={editor.data.model}
                        options={(models[editor.data.connection_id] || []).map(
                          (model) => ({ value: model, label: model }),
                        )}
                        onChange={(value) => update("model", value)}
                        allowCustom
                        loading={loadingModels}
                      />
                    </div>
                    {modelError && <small role="status">{modelError}</small>}
                    <div className="model-actions">
                      <button
                        type="button"
                        className="text-button"
                        disabled={
                          busy || loadingModels || !editor.data.connection_id
                        }
                        onClick={() => {
                          const c = store.connections.find(
                            (c) => c.id === editor.data.connection_id,
                          );
                          if (c) {
                            setModelError("");
                            testConnection(c);
                          }
                        }}
                      >
                        {loadingModels ? "Loading models…" : "Refresh models"}
                      </button>
                      <button
                        type="button"
                        className="text-button"
                        disabled={
                          busy ||
                          !editor.data.connection_id ||
                          !editor.data.model
                        }
                        onClick={async () => {
                          setTestingModel(true);
                          setBusy(true);
                          setModelCheck(null);
                          try {
                            const check = await api(
                              "/agents/check",
                              send("POST", {
                                harness: editor.data.harness || "builtin",
                                connection_id: editor.data.connection_id,
                                model: editor.data.model,
                                capabilities: editor.data.capabilities,
                              }),
                            );
                            setCheckRunId(check.id);
                            setModelCheck({
                              error: false,
                              text: "Starting a private test sandbox. The first run may take a few minutes.",
                            });
                            const deadline = Date.now() + 45 * 60 * 1000;
                            let complete = false;
                            while (Date.now() < deadline) {
                              await new Promise((resolve) =>
                                setTimeout(resolve, 3000),
                              );
                              const result = await api(
                                "/agents/check/" + check.id,
                              );
                              if (
                                ["completed", "failed", "timed_out"].includes(
                                  result.status,
                                )
                              ) {
                                complete = true;
                                if (
                                  result.status !== "completed" ||
                                  result.result.limitations?.length
                                )
                                  throw new Error(
                                    result.result.limitations?.join(" ") ||
                                      result.result.summary ||
                                      "Agent check did not complete.",
                                  );
                                setModelCheck({
                                  error: false,
                                  text: "Harness, model, and selected capabilities passed the test.",
                                });
                                break;
                              }
                              setModelCheck({
                                error: false,
                                text:
                                  result.logs?.at(-1)?.message ||
                                  "Starting test sandbox…",
                              });
                            }
                            if (!complete)
                              throw new Error(
                                "The test is still running. Check Modal for its status.",
                              );
                          } catch (error) {
                            setModelCheck({
                              error: true,
                              text: (error as Error).message,
                            });
                          } finally {
                            setCheckRunId(null);
                            setTestingModel(false);
                            setBusy(false);
                          }
                        }}
                      >
                        {testingModel ? "Testing…" : "Test agent"}
                      </button>
                      {testingModel && checkRunId && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={async () => {
                            try {
                              await api(
                                "/agents/check/" + checkRunId + "/cancel",
                                send("POST"),
                              );
                            } catch (error) {
                              setModelCheck({
                                error: true,
                                text: (error as Error).message,
                              });
                            }
                          }}
                        >
                          Stop test
                        </button>
                      )}
                    </div>
                    <small>
                      Checks this harness, model, and enabled tools on a sample
                      project. Uses Modal and model credits. Your review
                      instructions, attached skills, and MCP tools are not run
                      in this check.
                    </small>
                    {modelCheck && (
                      <p
                        className={modelCheck.error ? "error" : "notice"}
                        role={modelCheck.error ? "alert" : "status"}
                      >
                        {modelCheck.text}
                      </p>
                    )}
                  </FormSection>
                  <FormSection
                    title="Review instructions"
                    description="Define what to look for and the evidence you expect in a finding."
                  >
                    <Field label="Review instructions">
                      <textarea
                        rows={5}
                        value={editor.data.instructions}
                        onChange={(e) => update("instructions", e.target.value)}
                        required
                      />
                    </Field>
                  </FormSection>
                  <FormSection
                    title="Tools & environment"
                    description="Each instance runs in its own Modal sandbox. Enable the tools this reviewer needs."
                  >
                    <div className="included-capability">
                      <FileCode2 size={18} />
                      <span>
                        <strong>Repository access</strong>
                        <small>
                          Read files, search code, and inspect the PR diff.
                        </small>
                      </span>
                      <span className="included-label">
                        <Check size={13} /> Always included
                      </span>
                    </div>
                    <div className="capabilities">
                      {[
                        [
                          "terminal",
                          "Terminal",
                          "Run tests and commands",
                          Terminal,
                        ],
                        [
                          "browser",
                          "Browser",
                          "Inspect pages and screenshots",
                          Globe,
                        ],
                        [
                          "computer",
                          "Computer use",
                          "See and operate the desktop",
                          Monitor,
                        ],
                      ].map(([key, label, desc, Icon]) => {
                        const I = Icon as typeof Terminal;
                        return (
                          <label
                            className={
                              "capability " +
                              (editor.data.capabilities.includes(key)
                                ? "on"
                                : "")
                            }
                            key={key as string}
                          >
                            <input
                              type="checkbox"
                              checked={editor.data.capabilities.includes(key)}
                              disabled={testingModel}
                              onChange={(e) =>
                                toggleArray(
                                  "capabilities",
                                  key as string,
                                  e.target.checked,
                                )
                              }
                            />
                            <I size={18} />
                            <span className="capability-description">
                              <strong>{label as string}</strong>
                              <small>{desc as string}</small>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                    <small>
                      Browser and computer use require a model that supports
                      images and tools. Screenshots are sent to the model.
                    </small>
                  </FormSection>
                  <FormSection
                    title="Skills"
                    description="Attach reusable instructions and supporting files. The same skill can guide multiple agents."
                  >
                    {!store.skills.length ? (
                      <p>Create reusable instructions on the Skills page.</p>
                    ) : (
                      store.skills.map((s) => (
                        <Toggle
                          key={s.id}
                          checked={editor.data.skill_ids.includes(s.id)}
                          onChange={(on) => toggleArray("skill_ids", s.id, on)}
                        >
                          <span>
                            {s.name}
                            <small>{s.description}</small>
                          </span>
                        </Toggle>
                      ))
                    )}
                  </FormSection>
                  <FormSection
                    title="MCP tools"
                    description="Select tools from your shared connections. Only selected tools are available to this agent."
                  >
                    {!mcpConnections.length ? (
                      <p>
                        Add and test an MCP server under Connections to select
                        its tools here.
                      </p>
                    ) : (
                      mcpConnections.map((c) => (
                        <div className="mcp-selector" key={c.id}>
                          <strong>{c.name}</strong>
                          {!c.tools?.length && (
                            <small>
                              Test this connection to discover available tools.
                            </small>
                          )}
                          {c.tools?.map((t: any) => {
                            const selected = editor.data.mcps.find(
                              (m: any) => m.connection_id === c.id,
                            );
                            return (
                              <Toggle
                                key={t.name}
                                checked={
                                  selected?.tools.includes(t.name) || false
                                }
                                onChange={(on) => {
                                  const updated = editor.data.mcps.filter(
                                    (m: any) => m.connection_id !== c.id,
                                  );
                                  const tools = on
                                    ? [...(selected?.tools || []), t.name]
                                    : selected.tools.filter(
                                        (n: string) => n !== t.name,
                                      );
                                  update(
                                    "mcps",
                                    tools.length
                                      ? [
                                          ...updated,
                                          { connection_id: c.id, tools },
                                        ]
                                      : updated,
                                  );
                                }}
                              >
                                <span>
                                  {t.name}
                                  <small>{t.description?.slice(0, 130)}</small>
                                </span>
                              </Toggle>
                            );
                          })}
                        </div>
                      ))
                    )}
                  </FormSection>
                </>
              )}
              {editor.kind === "groups" && (
                <>
                  <FormSection
                    title="Agents in this group"
                    description="Add existing PR agents and choose how many instances of each to run in parallel."
                  >
                    {editor.data.members.map((m: any, i: number) => (
                      <div className="member" key={m.agent_id}>
                        <Bot size={18} />
                        <div className="member-identity">
                          <strong>
                            {store.agents.find((a) => a.id === m.agent_id)
                              ?.name || "Missing agent"}
                          </strong>
                          <small>
                            {
                              store.agents.find((a) => a.id === m.agent_id)
                                ?.model
                            }
                          </small>
                        </div>
                        <label>
                          Instances
                          <input
                            type="number"
                            min="1"
                            max="10"
                            value={m.count}
                            onChange={(e) =>
                              update(
                                "members",
                                editor.data.members.map((v: any, j: number) =>
                                  j === i
                                    ? { ...v, count: Number(e.target.value) }
                                    : v,
                                ),
                              )
                            }
                          />
                        </label>
                        <button
                          type="button"
                          className="icon"
                          aria-label="Remove agent"
                          onClick={() =>
                            update(
                              "members",
                              editor.data.members.filter(
                                (_: any, j: number) => j !== i,
                              ),
                            )
                          }
                        >
                          <X size={16} />
                        </button>
                      </div>
                    ))}
                    <select
                      aria-label="Add agent to group"
                      value=""
                      onChange={(e) =>
                        e.target.value &&
                        update("members", [
                          ...editor.data.members,
                          { agent_id: e.target.value, count: 1 },
                        ])
                      }
                    >
                      <option value="">Add an existing agent…</option>
                      {store.agents
                        .filter(
                          (a) =>
                            !editor.data.members.some(
                              (m: any) => m.agent_id === a.id,
                            ),
                        )
                        .map((a) => (
                          <option value={a.id} key={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </select>
                    {!store.agents.length && (
                      <small>
                        Create your first PR agent before creating a group.
                      </small>
                    )}
                  </FormSection>
                </>
              )}
              {editor.kind === "access" && (
                <FormSection
                  title="Can trigger"
                  description="All includes agents and groups added later. Specific grants access only to your selections."
                >
                  <div className="tabs" aria-label="Access scope">
                    <button
                      type="button"
                      aria-pressed={editor.data.scope === "all"}
                      className={editor.data.scope === "all" ? "active" : ""}
                      onClick={() => update("scope", "all")}
                    >
                      All agents and groups
                    </button>
                    <button
                      type="button"
                      aria-pressed={editor.data.scope === "selected"}
                      className={
                        editor.data.scope === "selected" ? "active" : ""
                      }
                      onClick={() => update("scope", "selected")}
                    >
                      Specific
                    </button>
                  </div>
                  {editor.data.scope === "all" ? (
                    <p className="meta">
                      All {store.agents.length} agents and {store.groups.length}{" "}
                      groups, plus future additions.
                    </p>
                  ) : (
                    <>
                      <Field label="Find agents and groups">
                        <input
                          value={accessQuery}
                          onChange={(event) =>
                            setAccessQuery(event.target.value)
                          }
                          placeholder="Search by name…"
                        />
                      </Field>
                      <div className="access-targets">
                        {(["agents", "groups"] as const).map((kind) => (
                          <div key={kind}>
                            <h4>{kind === "agents" ? "Agents" : "Groups"}</h4>
                            {store[kind]
                              .filter((target) =>
                                target.name
                                  .toLowerCase()
                                  .includes(accessQuery.toLowerCase()),
                              )
                              .map((target) => (
                                <Toggle
                                  key={target.id}
                                  checked={editor.data.target_ids.includes(
                                    target.id,
                                  )}
                                  onChange={(checked) =>
                                    toggleArray(
                                      "target_ids",
                                      target.id,
                                      checked,
                                    )
                                  }
                                >
                                  {target.name}
                                </Toggle>
                              ))}
                          </div>
                        ))}
                      </div>
                      <small>
                        Access to a group includes running all of its members
                        through that group.
                      </small>
                    </>
                  )}
                </FormSection>
              )}
              {editor.kind === "connections" && (
                <FormSection
                  title="Connection"
                  description="Credentials are stored securely and shared only through the review service."
                >
                  <Field label="Connection type">
                    <select
                      value={editor.data.type}
                      onChange={(e) => update("type", e.target.value)}
                    >
                      <option value="gateway">OpenAI-compatible gateway</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                      <option value="mcp">MCP server (Streamable HTTP)</option>
                    </select>
                  </Field>
                  <Field
                    label={
                      editor.data.type === "mcp"
                        ? "MCP endpoint URL"
                        : "API base URL"
                    }
                    hint={
                      editor.data.type === "mcp"
                        ? "For example: https://your-server.example.com/mcp"
                        : "For gateways, include /v1 if required. Leave blank for direct OpenAI or Anthropic."
                    }
                  >
                    <input
                      type="url"
                      value={editor.data.base_url}
                      onChange={(e) => update("base_url", e.target.value)}
                      required={["gateway", "mcp"].includes(editor.data.type)}
                    />
                  </Field>
                  <Field
                    label={
                      editor.data.type === "mcp" ? "Bearer token" : "API key"
                    }
                    hint={
                      editor.data.has_secret
                        ? "A credential is saved. Leave blank to keep it."
                        : "Encrypted at rest. Shared only through the job-scoped relay."
                    }
                  >
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={editor.data.api_key}
                      onChange={(e) => update("api_key", e.target.value)}
                      placeholder={
                        editor.data.has_secret ? "•••••••• (saved)" : ""
                      }
                    />
                  </Field>
                  <div className="info">
                    Save the connection, then use Test to discover{" "}
                    {editor.data.type === "mcp" ? "tools" : "models"}.
                  </div>
                </FormSection>
              )}
              {editor.kind === "skills" && (
                <FormSection
                  title="Skill contents"
                  description="Write in Markdown or upload a skill folder containing SKILL.md and supporting files."
                >
                  <Field label="Description">
                    <input
                      value={editor.data.description}
                      onChange={(e) => update("description", e.target.value)}
                    />
                  </Field>
                  <Field label="SKILL.md">
                    <textarea
                      className="mono"
                      rows={13}
                      value={editor.data.instructions}
                      onChange={(e) => update("instructions", e.target.value)}
                      required
                    />
                  </Field>
                  <label className="upload">
                    <Upload size={16} />
                    Import skill ZIP
                    <input
                      type="file"
                      accept=".zip"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        setBusy(true);
                        setFormError("");
                        try {
                          const data = new FormData();
                          data.append("file", file);
                          const r = await api("/skills/upload", {
                            method: "POST",
                            body: data,
                          });
                          setEditor(
                            (v) => v && { ...v, data: { ...v.data, ...r } },
                          );
                        } catch (err) {
                          setFormError((err as Error).message);
                        } finally {
                          setBusy(false);
                        }
                      }}
                    />
                  </label>
                  <small>
                    Include SKILL.md and up to 50 supporting files. Maximum 2
                    MB.
                  </small>
                  {Object.keys(editor.data.files).length > 0 && (
                    <div className="file-list">
                      {Object.keys(editor.data.files).map((f) => (
                        <code key={f}>{f}</code>
                      ))}
                    </div>
                  )}
                </FormSection>
              )}
              {editor.kind === "repos" && (
                <FormSection
                  title="Review environment"
                  description="Prepare and launch the app inside each sandbox for terminal, browser, or computer reviews."
                >
                  <Field
                    label="Installation ID"
                    hint="Filled automatically when you select a discovered repository."
                  >
                    <input
                      type="number"
                      min="0"
                      value={editor.data.installation_id}
                      onChange={(e) =>
                        update("installation_id", Number(e.target.value))
                      }
                    />
                  </Field>
                  <div className="info">
                    Optional app setup runs inside each agent’s sandbox when it
                    has Terminal, Browser, or Computer use enabled.
                  </div>
                  <Field
                    label="Setup command"
                    hint="Install dependencies or prepare test data. Maximum 3 minutes."
                  >
                    <textarea
                      className="mono"
                      rows={3}
                      value={editor.data.setup_command}
                      onChange={(e) => update("setup_command", e.target.value)}
                      placeholder="npm ci"
                    />
                  </Field>
                  <Field label="App start command">
                    <input
                      className="mono"
                      value={editor.data.start_command}
                      onChange={(e) => update("start_command", e.target.value)}
                      placeholder="npm run dev -- --host 0.0.0.0"
                    />
                  </Field>
                  <Field
                    label="Readiness URL"
                    hint="The address inside the sandbox. Use localhost or 127.0.0.1."
                  >
                    <input
                      type="url"
                      value={editor.data.readiness_url}
                      onChange={(e) => update("readiness_url", e.target.value)}
                      placeholder="http://localhost:3000"
                    />
                  </Field>
                  <Field
                    label="Test secrets (JSON)"
                    hint={
                      editor.data.secret_names?.length
                        ? "Saved keys: " +
                          editor.data.secret_names.join(", ") +
                          ". Leave empty to keep them."
                        : "Optional test-only environment variables. Never use production credentials."
                    }
                  >
                    <textarea
                      className="mono"
                      rows={3}
                      defaultValue=""
                      placeholder={'{"TEST_API_KEY": "..."}'}
                      onChange={(e) => {
                        try {
                          update(
                            "test_secrets",
                            e.target.value ? JSON.parse(e.target.value) : {},
                          );
                          setFormError("");
                        } catch {
                          setFormError("Test secrets must be valid JSON.");
                        }
                      }}
                    />
                  </Field>
                </FormSection>
              )}
              {notice && (
                <div className="notice" role="status">
                  <Check size={15} />
                  {notice}
                </div>
              )}
              {formError && (
                <div role="alert" className="error">
                  {formError}
                </div>
              )}
            </div>
            <footer>
              <div className="editor-footer-note">
                {editor.kind === "groups" ? (
                  <>
                    <code>@litereview {editor.data.name || "group-name"}</code>
                    <small>
                      {editor.data.members.reduce(
                        (count: number, member: any) => count + member.count,
                        0,
                      )}{" "}
                      instances · independent sandboxes
                    </small>
                  </>
                ) : (
                  <span>Changes apply to future reviews.</span>
                )}
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditor(null)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {busy && !testingModel ? (
                  <>
                    <Loader2 size={15} className="spin" />
                    Saving…
                  </>
                ) : (
                  "Save " +
                  {
                    access: "access",
                    agents: "agent",
                    groups: "group",
                    connections: "connection",
                    skills: "skill",
                    repos: "repository",
                  }[editor.kind]
                )}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {githubCreate && (
        <Modal
          title="Create a private GitHub App"
          close={() => setGithubCreate(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              mutate(async () => {
                const r = await api(
                  "/github/manifest",
                  send("POST", { owner_type: appOwnerType, owner: appOwner }),
                );
                const form = document.createElement("form");
                form.method = "POST";
                form.action = r.url;
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = "manifest";
                input.value = r.manifest;
                form.append(input);
                document.body.append(form);
                form.submit();
              });
            }}
          >
            <div className="dialog-body">
              <p>
                A private GitHub App can only be installed on the account that
                owns it. Choose the account or organization that owns your
                repositories.
              </p>
              <Field label="App owner">
                <select
                  value={appOwnerType}
                  onChange={(e) => setAppOwnerType(e.target.value)}
                >
                  <option value="organization">GitHub organization</option>
                  <option value="personal">My personal GitHub account</option>
                </select>
              </Field>
              {appOwnerType === "organization" && (
                <Field
                  label="Organization name"
                  hint="An organization owner may need to create or install the App. You can also ask them to create it and use “Use existing App”."
                >
                  <input
                    value={appOwner}
                    onChange={(e) => setAppOwner(e.target.value)}
                    placeholder="BerriAI"
                    required
                  />
                </Field>
              )}
              {error && <div className="error">{error}</div>}
            </div>
            <footer>
              <button type="button" onClick={() => setGithubCreate(false)}>
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                Continue to GitHub <ArrowUpRight size={14} />
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {githubForm && (
        <Modal
          title="Connect an existing GitHub App"
          close={() => setGithubForm(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              mutate(async () => {
                await api("/github/config", send("POST", githubData));
                await load();
                setGithubForm(false);
                setNotice("GitHub App configuration saved.");
              });
            }}
          >
            <div className="dialog-body">
              <p>
                Configure the App webhook for <code>{setup.webhook_url}</code>.
                Subscribe to issue comments. Grant Contents read, Pull requests
                write, and Issues write access.
              </p>
              {[
                ["app_id", "App ID"],
                ["private_key", "Private key (PEM)"],
                ["webhook_secret", "Webhook secret"],
                ["client_id", "OAuth client ID"],
                ["client_secret", "OAuth client secret"],
                ["html_url", "GitHub App URL"],
              ].map(([key, label]) => (
                <Field key={key} label={label}>
                  {key === "private_key" ? (
                    <textarea
                      rows={5}
                      value={githubData[key] || ""}
                      onChange={(e) =>
                        setGithubData((d) => ({ ...d, [key]: e.target.value }))
                      }
                    />
                  ) : (
                    <input
                      type={key.includes("secret") ? "password" : "text"}
                      value={githubData[key] || ""}
                      onChange={(e) =>
                        setGithubData((d) => ({ ...d, [key]: e.target.value }))
                      }
                    />
                  )}
                </Field>
              ))}
              <small>
                Leave existing secret fields blank to keep their saved values.
                Add your GitHub username to ADMIN_USERS to enable GitHub
                sign-in.
              </small>
              {error && <div className="error">{error}</div>}
            </div>
            <footer>
              <button type="button" onClick={() => setGithubForm(false)}>
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                Save App
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  /^\/docs\/?$/.test(location.pathname) ? <Documentation /> : <App />,
);
