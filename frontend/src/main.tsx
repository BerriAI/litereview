import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Layers,
  Plug,
  Settings,
  Network,
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
import "./onboarding.css";
import { Documentation } from "./docs";
import { MCPAuthFields, MCPServers, People } from "./resources";
import { MCPToolSelector } from "./mcp-tool-selector";
import { ReviewResult } from "./review-result";
import { FusionFields, FusionResults } from "./fusion";
import { api, send } from "./api";
import { GitHubSetup, GitHubSettings } from "./github-setup";
import { MemberStart } from "./member-start";
import { StarterReviewer } from "./starter-reviewer";
import { MinimumReviewTime, OptionalReviewLimits } from "./review-time";
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
type Editor = { kind: Kind; data: any; id?: string };
const empty: Store = {
  agents: [],
  groups: [],
  connections: [],
  skills: [],
  repos: [],
  access: [],
};
const icons = {
  repos: FileCode2,
  access: Users,
  agents: Bot,
  groups: Layers,
  connections: Settings,
  mcp: Plug,
  models: Network,
  skills: BookOpen,
  runs: History,
};
const titles = {
  repos: "Repositories",
  access: "People",
  agents: "PR agents",
  groups: "Agent groups",
  connections: "Settings",
  mcp: "MCP servers",
  models: "Models",
  skills: "Skills",
  runs: "Runs",
};
const descriptions = {
  repos: "Choose where LiteReview can run.",
  access:
    "Choose which agents and groups people and repositories can use. Both must have access to start a review.",
  agents: "Create reusable reviewers with their own model, tools, and skills.",
  groups: "Optional shortcuts for running several agents together.",
  connections: "GitHub App settings for this workspace.",
  mcp: "Connect your tools, then choose which ones each agent can use.",
  models: "Model providers and gateways available to your agents.",
  skills: "Reusable instructions and files for your agents.",
  runs: "Review progress, findings, and visual evidence from each agent.",
};
function App() {
  const [session, setSession] = useState<any>(undefined);
  const [store, setStore] = useState<Store>(empty);
  const [path, setPath] = useState(location.pathname);
  const [error, setError] = useState(() => {
    const reason = new URLSearchParams(location.search).get("github_error");
    return reason === "expired"
      ? "GitHub setup expired or sign-in is needed. Your settings are saved. Retry setup below; sign in if prompted."
      : reason === "conversion"
        ? "GitHub could not finish App creation. Your previous settings are saved. Retry setup, or connect the App from App settings if it already exists on GitHub."
        : "";
  });
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [accessSubject, setAccessSubject] = useState<"user" | "repo">("user");
  const [accessQuery, setAccessQuery] = useState("");
  const tab =
    path === "/mcp" ? "mcp" : path === "/models" ? "models" : "github";
  const [expired, setExpired] = useState(false);
  const [starter, setStarter] = useState(false);
  const [agentFilter, setAgentFilter] = useState("all");
  const [mobileNav, setMobileNav] = useState(false);
  const [setup, setSetup] = useState<any>({});
  const [workspaceLoaded, setWorkspaceLoaded] = useState(false);
  const [githubRevision, setGithubRevision] = useState(0);
  const [runs, setRuns] = useState<Row[]>([]);
  const [run, setRun] = useState<any>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [draft, setDraft] = useState<Editor | null>(null);
  const oauthHandled = useRef(false);
  const draftKey = session?.user
    ? "litereview:agent-draft:" + (session.user.sub || session.user.login)
    : "";
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState("");
  const [testingModel, setTestingModel] = useState(false);
  const [checkRunId, setCheckRunId] = useState<string | null>(null);
  const [modelCheck, setModelCheck] = useState<{
    error: boolean;
    text: string;
  } | null>(null);
  const [githubForm, setGithubForm] = useState(false);
  const [githubCreate, setGithubCreate] = useState(false);
  const [appOwnerType, setAppOwnerType] = useState("organization");
  const [appOwner, setAppOwner] = useState("");
  const [githubData, setGithubData] = useState<Record<string, string>>({});
  const route = path.split("/")[1] || "repositories";
  const section =
    (
      {
        repositories: "repos",
        settings: "connections",
        mcp: "connections",
        models: "connections",
        people: "access",
      } as Record<string, string>
    )[route] || (route === "connections" ? "repos" : route);
  const active = (section in titles ? section : "repos") as keyof typeof titles;
  const navActive = active === "connections" && tab !== "github" ? tab : active;
  const pageTitle = titles[navActive];
  const pageDescription =
    active === "access" && session?.sso_required
      ? "People appear after Google sign-in. Link GitHub to start reviews. Everyone can use all agents by default; you can restrict access here."
      : descriptions[navActive];
  useEffect(() => {
    if (!draftKey) {
      setDraft(null);
      return;
    }
    try {
      const saved = JSON.parse(sessionStorage.getItem(draftKey) || "null");
      if (saved?.kind === "agents") setDraft(saved);
    } catch {
      /* Storage is optional. */
    }
  }, [draftKey]);
  useEffect(() => {
    if (editor?.kind !== "agents" || !draftKey) return;
    setDraft(editor);
    try {
      sessionStorage.setItem(draftKey, JSON.stringify(editor));
    } catch {
      /* Keep the in-memory draft. */
    }
  }, [editor, draftKey]);
  const clearDraft = () => {
    setDraft(null);
    try {
      if (draftKey) sessionStorage.removeItem(draftKey);
    } catch {
      /* Storage is optional. */
    }
  };
  const closeEditor = () => {
    if (editor?.kind === "agents") clearDraft();
    setEditor(null);
  };
  const modelConnection =
    editor?.kind === "agents" ? editor.data.connection_id : "";
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
    editor?.data.reasoning_effort,
    JSON.stringify(editor?.data.fusion),
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
    if (next === "/people") setAccessSubject("user");
  };
  useEffect(() => {
    const fn = () => {
      setPath(location.pathname);
      setEditor(null);
    };
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
    let alive = true;
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      api("/session")
        .then((next) => {
          if (alive)
            setSession((current: any) =>
              JSON.stringify(current) === JSON.stringify(next) ? current : next,
            );
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    };
    refresh();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive = false;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [path]);
  useEffect(() => {
    const expiredSession = () => {
      setExpired(true);
      setEditor(null);
      setGithubCreate(false);
      setGithubForm(false);
      setStarter(false);
      setWorkspaceLoaded(false);
      setStore(empty);
      setSession((current: any) =>
        current ? { ...current, user: null } : current,
      );
    };
    window.addEventListener("litereview:unauthorized", expiredSession);
    return () =>
      window.removeEventListener("litereview:unauthorized", expiredSession);
  }, []);
  useEffect(() => {
    if (!session?.user || !workspaceLoaded || path !== "/") return;
    navigate(
      session.sso_required && !session.user.github_login
        ? "/start"
        : store.repos.length && store.agents.length
          ? "/runs"
          : "/repositories",
    );
  }, [session?.user, workspaceLoaded]);
  useEffect(() => {
    if (!session?.user || ["/account", "/start"].includes(path)) return;
    document.title = pageTitle + " · LiteReview";
    if (!editor)
      document
        .querySelector<HTMLElement>("main h1")
        ?.focus({ preventScroll: true });
  }, [path, session?.user, editor]);
  const load = async () => {
    const kinds = Object.keys(empty) as Kind[];
    const values = await Promise.all(kinds.map((k) => api("/resources/" + k)));
    setStore(Object.fromEntries(kinds.map((k, i) => [k, values[i]])) as Store);
    const nextSetup = await api("/setup");
    setSetup(nextSetup);
    setAppOwner((current) => current || nextSetup.github_organization || "");
    setWorkspaceLoaded(true);
  };
  const resetGithub = async (undo: boolean) => {
    const result = await api(
      "/github/reset" + (undo ? "/undo" : ""),
      send("POST"),
    );
    setSetup((current: any) => ({
      ...current,
      github: false,
      github_app_url: "",
      github_reset: result.github_reset,
    }));
    setAppOwner(setup.github_organization || "");
    setAppOwnerType("organization");
    setGithubData({});
    setGithubForm(false);
    setGithubCreate(false);
    setError("");
    setNotice(undo ? "GitHub setup restored. Checking repository access…" : "");
    history.replaceState({}, "", location.pathname);
    // Remount to abort discovery and discard stale repository/search state.
    setGithubRevision((v) => v + 1);
    try {
      await load();
      setSession(await api("/session"));
    } catch (err) {
      setError(
        "GitHub setup changed, but the workspace could not refresh. Reload the page. " +
          (err as Error).message,
      );
    }
  };
  useEffect(() => {
    if (session?.user) load().catch((e) => setError(e.message));
  }, [session]);
  useEffect(() => {
    if (!session?.user || !["connections", "repos"].includes(active)) return;
    let alive = true;
    const refreshSetup = () => {
      api("/setup")
        .then((value) => {
          if (alive) setSetup(value);
        })
        .catch(() => {});
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshSetup();
    };
    window.addEventListener("focus", refreshSetup);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.removeEventListener("focus", refreshSetup);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [session?.user, active, githubRevision]);
  useEffect(() => {
    if (!session?.user || active !== "runs") return;
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
        minimum_minutes: 0,
        maximum_minutes: null,
        max_model_calls: null,
        instructions:
          "Review this PR for actionable defects. Explain a concrete failure scenario and cite the affected code. Report no findings when appropriate.",
        connection_id:
          store.connections.find((c) => ["openai", "gateway"].includes(c.type))?.id || "",
        model: "",
        capabilities: ["terminal"],
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
        auth_type: tab === "mcp" ? "oauth" : "bearer",
        oauth_client_id: "",
        oauth_client_secret: "",
        oauth_scope: "",
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
      id: item?.inherited ? undefined : item?.id,
      data: structuredClone(
        item
          ? {
              ...defaults[kind],
              ...item,
              ...(kind === "connections"
                ? {
                    api_key: "",
                    oauth_client_secret: "",
                    auth_type: item.auth_type || "bearer",
                  }
                : {}),
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
  const discoverConnection = async (item: Row) => {
    try {
      const r = await api(`/connections/${item.id}/test`, send("POST"));
      if (r.models) setModels((m) => ({ ...m, [item.id]: r.models }));
      setNotice(
        r.models
          ? `Connected · ${r.models.length} models available.`
          : `Connected · ${r.tools.length} tools discovered. Choose tools in an agent to use them.`,
      );
    } finally {
      await load();
    }
  };
  const startOAuth = async (item: Row) => {
    const result = await api(
      `/connections/${item.id}/oauth/start`,
      send("POST"),
    );
    location.assign(result.authorization_url);
  };
  const connectOAuth = (item: Row) => mutate(() => startOAuth(item));
  const disconnectOAuth = (item: Row) =>
    mutate(async () => {
      await api(`/connections/${item.id}/oauth/disconnect`, send("POST"));
      await load();
      setNotice(
        "Disconnected from LiteReview. To revoke the provider’s grant, use that provider’s account settings.",
      );
    });
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const result = params.get("oauth"),
      cid = params.get("connection");
    if (
      !session?.user ||
      !workspaceLoaded ||
      !result ||
      !cid ||
      oauthHandled.current
    )
      return;
    oauthHandled.current = true;
    history.replaceState({}, "", location.pathname);
    if (result === "connected") {
      setNotice("Account authorized. Testing the connection…");
      void mutate(() => discoverConnection({ id: cid, name: "" }));
    } else
      setError(
        result === "denied"
          ? "OAuth was cancelled. Your server is saved; choose Connect account to try again."
          : "OAuth could not finish. Your server is saved; reconnect or check its OAuth settings.",
      );
  }, [session?.user, workspaceLoaded]);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editor) return;
    setBusy(true);
    setFormError("");
    try {
      const saved = await api(
        "/resources/" + editor.kind + (editor.id ? "/" + editor.id : ""),
        send(editor.id ? "PUT" : "POST", editor.kind === "agents" ? { ...editor.data, harness: "codex" } : editor.data),
      );
      // Retain the saved ID if discovery fails, so Retry never creates duplicates.
      setEditor({
        kind: editor.kind,
        id: saved.id,
        data: { ...saved, api_key: "", oauth_client_secret: "" },
      });
      await load();
      if (editor.kind === "connections") {
        if (
          saved.type === "mcp" &&
          saved.auth_type === "oauth" &&
          !saved.oauth?.connected
        ) {
          await startOAuth(saved);
          return;
        }
        await discoverConnection(saved);
      } else setNotice("Saved successfully.");
      if (editor.kind === "agents") clearDraft();
      setEditor(null);
      setGithubRevision((v) => v + 1);
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = (kind: Kind, item: Row) => {
    const reset = kind === "access" && item.sso;
    if (
      confirm(
        reset
          ? `Reset access for “${item.name}” to all agents and groups? Google sign-in and linked GitHub are still required.`
          : kind === "access"
            ? `Remove access for “${item.name}”?`
            : kind === "agents" && setup?.default_reviewer_id === item.id
              ? `Delete “${item.name}”? Choose another default reviewer afterward to keep @litereview working.`
            : `Delete “${item.name}”?`,
      )
    )
      mutate(async () => {
        await api(`/resources/${kind}/${item.id}`, send("DELETE"));
        await load();
        setNotice(reset ? "Access reset to workspace default." : "Deleted.");
      });
  };
  const testConnection = (item: Row) => mutate(() => discoverConnection(item));
  const modelConnections = store.connections.filter((c) => ["openai", "gateway"].includes(c.type));
  const mcpConnections = store.connections.filter((c) => c.type === "mcp");
  const needsRepositories =
    !setup.github || setup.github_reset || !store.repos.length;
  const needsGithubAccount =
    session?.sso_required && !session?.user?.github_login;
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
          <h1>
            {expired
              ? "Sign in to continue."
              : "Code reviews, right in your PR."}
          </h1>
          <p>
            {expired
              ? "Your Google session expired. Sign in again to return to your workspace. Your GitHub link and settings are saved."
              : "Sign in with your workspace Google account to start using LiteReview."}
          </p>
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
              <a
                className="button primary full"
                href={"/api/auth/google?next=" + encodeURIComponent(path)}
              >
                Sign in with Google
              </a>
            ) : (
              <p role="alert">
                Google sign-in is being configured. Contact the person who
                manages this deployment.
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
              : "Access is restricted to approved workspace accounts."}
          </small>
        </div>
      </div>
    );
  if (session.sso_required && (path === "/account" || path === "/start"))
    return (
      <MemberStart
        user={session.user}
        account={path === "/account"}
        githubLogin={session.github_login}
        workspaceLoaded={workspaceLoaded}
        repositoryCount={store.repos.length}
        githubConfigured={!!setup.github && !setup.github_reset}
        navigate={navigate}
        onLogout={() =>
          mutate(async () => {
            await api("/logout", send("POST"));
            setSession(await api("/session"));
          })
        }
      />
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
    const searchable = [
      row.name,
      row.model,
      row.description,
      row.email,
      row.repo,
      row.pr,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (
      searchable.includes(query.toLowerCase()) &&
      (active !== "agents" ||
        agentFilter === "all" ||
        (agentFilter === "code"
          ? !row.capabilities.some((cap: string) =>
              ["browser", "computer"].includes(cap),
            )
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
              : tab === "mcp"
                ? "Add MCP server"
                : "Add connection";
  const hideRepoTable =
    active === "connections" && tab === "github" && !store.repos.length;
  const PageIcon = icons[navActive];
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
            ["Resources", ["mcp", "skills", "models"]],
            ["Workspace", ["repos", "access", "connections"]],
          ].map(([label, keys]) => (
            <div className="nav-group" key={label as string}>
              <div className="workspace-label">{label as string}</div>
              {(keys as string[]).map((key) => {
                const Icon = icons[key as keyof typeof icons];
                return (
                  <button
                    key={key}
                    aria-label={titles[key as keyof typeof titles]}
                    aria-current={navActive === key ? "page" : undefined}
                    className={navActive === key ? "selected" : ""}
                    onClick={() =>
                      navigate(
                        (
                          {
                            repos: "/repositories",
                            connections: "/settings",
                            access: "/people",
                          } as Record<string, string>
                        )[key] || "/" + key,
                      )
                    }
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
                ? "Workspace account"
                : session.user.login}
            </strong>
            <small>
              {session.sso_required ? "Google Workspace" : "Workspace"}
            </small>
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
      <main
        id="main-content"
        inert={
          mobileNav ||
          !!(
            editor &&
            ["agents", "groups", "skills", "repos"].includes(editor.kind)
          )
        }
      >
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
          <span>{pageTitle}</span>
        </div>
        <div className="page">
          {draft && !editor && (
            <div className="draft-banner">
              <span>
                Agent draft saved · {draft.data.name || "Untitled agent"}
              </span>
              <div className="resource-actions">
                <button
                  onClick={() => {
                    navigate("/agents");
                    setEditor(draft);
                    setFormError("");
                  }}
                >
                  Resume editing
                </button>
                <button
                  className="icon"
                  aria-label="Discard agent draft"
                  onClick={clearDraft}
                >
                  <X size={15} />
                </button>
              </div>
            </div>
          )}
          {active !== "repos" && (
            <header className="page-heading">
              <div>
                <div className="title-line">
                  <PageIcon size={22} />
                  <h1 tabIndex={-1}>
                    {isRunDetail ? "Review results" : pageTitle}
                  </h1>
                </div>
                <p>{pageDescription}</p>
              </div>
              {active !== "runs" &&
                rows.length > 0 &&
                !(
                  active === "access" &&
                  accessSubject === "user" &&
                  session.sso_required
                ) &&
                !(active === "connections" && tab === "github") && (
                  <button className="primary" onClick={() => edit(newKind)}>
                    <Plus size={16} />
                    {createLabel}
                  </button>
                )}
            </header>
          )}
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
          {workspaceLoaded &&
            active !== "repos" &&
            (needsRepositories || needsGithubAccount) && (
              <section
                className="github-next-step"
                aria-label="GitHub connection needed"
              >
                <ProviderMark type="github" />
                <div>
                  <strong>
                    {needsRepositories
                      ? "GitHub repositories aren’t connected."
                      : "Your GitHub account isn’t linked."}
                  </strong>
                  <p>
                    {!needsRepositories
                      ? "Link the account you use to comment on PRs so LiteReview can recognize your review requests."
                      : session.user.github_login
                        ? `Your account is linked as @${session.user.github_login}. Connect repository access to start PR reviews.`
                        : "Connect GitHub and choose repositories before starting PR reviews."}
                  </p>
                </div>
                {needsRepositories ? (
                  <button
                    className="primary"
                    onClick={() => navigate("/repositories")}
                  >
                    Connect GitHub
                  </button>
                ) : (
                  <a
                    className="button primary"
                    href={"/api/auth/github?next=" + encodeURIComponent(path)}
                  >
                    Connect GitHub account
                  </a>
                )}
              </section>
            )}
          {active === "connections" && (
            <>
              {tab === "github" && (
                <GitHubSettings
                  setup={setup}
                  onCreate={() => setGithubCreate(true)}
                  onExisting={() => setGithubForm(true)}
                  onReset={resetGithub}
                  onRepositories={() => navigate("/repositories")}
                />
              )}
            </>
          )}
          {active === "repos" && !workspaceLoaded && !error && (
            <p role="status" className="onboarding-loading">
              <Loader2 size={16} className="spin" />
              Loading your workspace…
            </p>
          )}
          {active === "repos" && workspaceLoaded && (
            <GitHubSetup
              key={githubRevision}
              setup={setup}
              githubAccount={session.user.github_login || ""}
              onAccount={() => navigate("/account")}
              ssoRequired={session.sso_required}
              canLinkGithub={session.github_login}
              connected={store.repos}
              agents={store.agents}
              groups={store.groups}
              connections={store.connections}
              access={store.access}
              revision={githubRevision}
              onConnected={load}
              onCreate={() => setGithubCreate(true)}
              onExisting={() => setGithubForm(true)}
              onReset={resetGithub}
              onSettings={() => {
                navigate("/settings");
              }}
              onModel={() => {
                navigate("/models");
              }}
              onAgent={(agent) =>
                agent ? edit("agents", agent) : setStarter(true)
              }
              onEdit={(repo) => edit("repos", repo)}
              onAccess={(repo) => {
                setAccessSubject("repo");
                const grant = store.access.find(
                  (g) =>
                    g.subject === "repo" &&
                    g.name.toLowerCase() === repo.name.toLowerCase(),
                );
                setEditor({
                  kind: "access",
                  id: grant?.id,
                  data: grant || {
                    name: repo.name.toLowerCase(),
                    subject: "repo",
                    scope: "selected",
                    target_ids: [],
                  },
                });
              }}
              onRemove={(repo) => remove("repos", repo)}
              onPeople={() => navigate("/people")}
              onStart={() =>
                navigate(session.sso_required ? "/start" : "/agents")
              }
            />
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
                  {run.jobs.map((job: any) => (
                    <details className="job" key={job.id}>
                      <summary className="job-title">
                        <h3>{job.config.name}</h3>
                        {job.config.target_kind !== "agent" && (
                          <span>{job.config.group}</span>
                        )}
                        {run.jobs.filter(
                          (other: any) =>
                            other.config.name === job.config.name &&
                            other.config.group === job.config.group,
                        ).length > 1 && <span>Run {job.config.instance}</span>}
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
                            : job.status === "waiting" ? "reviewing branches" : job.status}
                        </Badge>
                        <span>
                          {job.result.findings?.length || 0}{" "}
                          {job.result.findings?.length === 1 ? "finding" : "findings"}
                        </span>
                      </summary>
                      <p className="meta">
                        <code>{job.config.model}</code>
                      </p>
                      <ReviewResult job={job} repo={run.repo} head={run.head} />
                      <FusionResults job={job} repo={run.repo} head={run.head} />
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
                          Activity · {job.logs.length}{" "}
                          {job.logs.length === 1 ? "event" : "events"}
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
                              <span>{l.message}</span>
                            </div>
                          ))}
                          {!job.logs.length && (
                            <p>Waiting for the sandbox to start.</p>
                          )}
                        </div>
                      </details>
                    </details>
                  ))}
                </div>
              ) : (
                <p>Loading run…</p>
              )}
            </>
          ) : active === "repos" ||
            (active === "connections" && tab === "github") ? null : active ===
              "access" &&
            accessSubject === "user" &&
            session.sso_required ? (
            <People
              rows={rows}
              targets={[...store.agents, ...store.groups]}
              edit={(row) => edit("access", row)}
              reset={(row) => remove("access", row)}
            />
          ) : active === "connections" && tab === "mcp" ? (
            <MCPServers
              rows={rows}
              busy={busy}
              edit={(row) => edit("connections", row)}
              connect={connectOAuth}
              test={testConnection}
              disconnect={disconnectOAuth}
              remove={(row) => remove("connections", row)}
            />
          ) : (
            <>
              {active === "agents" && store.agents.length > 0 && (
                <Field label="Default reviewer" hint="Comment @litereview to run this agent. Caller and repository permissions still apply.">
                  <select
                    value={setup?.default_reviewer_id || ""}
                    disabled={busy}
                    onChange={async (event) => {
                      const agentId = event.target.value;
                      setBusy(true);
                      setError("");
                      try {
                        const result = await api("/default-reviewer", send("PUT", { agent_id: agentId }));
                        setSetup((current: any) => ({ ...current, ...result }));
                      } catch (error) {
                        setError((error as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <option value="" disabled>Choose a reviewer</option>
                    {store.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                  </select>
                </Field>
              )}
              {active === "agents" && store.agents.length > 0 && (
                <div className="tabs agent-tabs" aria-label="Filter agents">
                  {[
                    ["all", "All agents"],
                    ["code", "Code & tests"],
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
              <div
                className="table-toolbar"
                hidden={hideRepoTable || rows.length === 0}
              >
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
                <div className="empty" hidden={hideRepoTable}>
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
                        ? "Post @litereview on a connected PR to run the default reviewer, or add agent or group names. Your review will appear here."
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
                          ? navigate("/models")
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
                                  Codex ·{" "}
                                  {store.connections.find(
                                    (c) => c.id === row.connection_id,
                                  )?.name || "Connection unavailable"}
                                  {row.minimum_minutes > 0 &&
                                    ` · ${row.minimum_minutes} min minimum`}
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
                                Test connection
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
      {starter && (
        <StarterReviewer
          connections={modelConnections}
          close={() => {
            setStarter(false);
            void load();
            setGithubRevision((v) => v + 1);
          }}
          onSaved={load}
        />
      )}
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
            editor.kind === "access" && editor.data.sso
              ? "Edit access"
              : editor.kind === "connections" && editor.data.type === "mcp"
                ? editor.id
                  ? "Edit MCP server"
                  : "Add MCP server"
                : editor.kind === "access"
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
          close={() => !busy && closeEditor()}
          openNavigation={() => setMobileNav(true)}
          navigationOpen={mobileNav}
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
                {editor.kind === "access" && editor.data.sso ? (
                  <Field label="GitHub account">
                    <input value={editor.data.name} readOnly />
                  </Field>
                ) : editor.kind === "access" &&
                  editor.data.subject === "repo" ? (
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
                    hint="Connected through the GitHub App installation."
                  >
                    <input
                      value={editor.data.name}
                      onChange={(e) => update("name", e.target.value)}
                      required
                    />
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
                  <Field label="Review mode" hint="Fusion combines independent branches into one verified review.">
                    <select value={editor.data.fusion ? "fusion" : "single"} disabled={testingModel}
                      onChange={(event) => update("fusion", event.target.value === "single" ? null : {
                        branches: [0, 1].map(() => ({ connection_id: editor.data.connection_id, model: editor.data.model, reasoning_effort: editor.data.reasoning_effort || null, focus: "" })),
                        instructions: "",
                      })}>
                      <option value="single">Single reviewer</option><option value="fusion">Fusion</option>
                    </select>
                  </Field>
                  {editor.data.fusion && <FusionFields value={editor.data.fusion}
                    onChange={(value) => update("fusion", value)} connections={modelConnections} disabled={testingModel} />}
                  <FormSection
                    title={editor.data.fusion ? "Aggregator model" : "Model"}
                    description="Reviews run with Codex in a dedicated Modal sandbox. Use OpenAI or a gateway that supports the Responses API."
                  >
                    {store.connections.find((c) => c.id === editor.data.connection_id)?.type === "anthropic" && (
                      <p className="error" role="alert">
                        This reviewer uses a direct Anthropic connection. Add a Responses-compatible gateway in Models, then select it here to keep using your Anthropic model with Codex.
                      </p>
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
                    <Field
                      label="Reasoning effort"
                      hint="Higher effort gives supported models more time to reason and can increase cost. Leave the default for models that do not support this setting."
                    >
                      <select
                        value={editor.data.reasoning_effort || ""}
                        disabled={testingModel}
                        onChange={(e) => update("reasoning_effort", e.target.value || null)}
                      >
                        <option value="">Model default</option>
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                        <option value="xhigh">Extra high</option>
                      </select>
                    </Field>
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
                                harness: "codex",
                                connection_id: editor.data.connection_id,
                                model: editor.data.model,
                                reasoning_effort: editor.data.reasoning_effort || null,
                                fusion: editor.data.fusion || null,
                                capabilities: editor.data.capabilities,
                              }),
                            );
                            setCheckRunId(check.id);
                            setModelCheck({
                              error: false,
                              text: editor.data.fusion ? "Starting private branch checks, followed by the aggregator check." : "Starting a private test sandbox. The first run may take a few minutes.",
                            });
                            while (true) {
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
                                  text: "All selected models and capabilities passed the harness check.",
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
                      Checks Codex, every selected model, and enabled tools on a sample
                      project. Uses Modal and model credits. Your review
                      instructions, attached skills, and MCP tools are not run
                      in this check. The minimum review time applies to PR
                      reviews, not this check.
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
                    description="These instructions define the agent’s task, scope, workflow, and voice. Attached skills support them."
                  >
                    <Field label="Review instructions">
                      <textarea
                        rows={5}
                        value={editor.data.instructions}
                        onChange={(e) => update("instructions", e.target.value)}
                        required
                      />
                    </Field>
                    <MinimumReviewTime
                      value={editor.data.minimum_minutes || 0}
                      onChange={(value) => update("minimum_minutes", value)}
                    />
                    <OptionalReviewLimits
                      maximumMinutes={editor.data.maximum_minutes}
                      maxModelCalls={editor.data.max_model_calls}
                      minimumMinutes={editor.data.minimum_minutes || 0}
                      onChange={update}
                    />
                  </FormSection>
                  <FormSection
                    title="Tools & environment"
                    description="Each reviewer can inspect code, run tests, and use Codex subagents when requested by your instructions or skills. Add visual tools if needed."
                  >
                    <div className="included-capability">
                      <FileCode2 size={18} />
                      <span>
                        <strong>Code & terminal</strong>
                        <small>
                          Read and search code, inspect the diff, and run tests
                          or commands.
                        </small>
                      </span>
                      <span className="included-label">
                        <Check size={13} /> Always included
                      </span>
                    </div>
                    <div className="capabilities">
                      {[
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
                      <p>Add reusable instructions and supporting files.</p>
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
                    <button
                      type="button"
                      className="resource-link"
                      onClick={() => navigate("/skills")}
                    >
                      Manage skills <ArrowUpRight size={14} />
                    </button>
                  </FormSection>
                  <FormSection
                    title="MCP tools"
                    description="Select a server to enable all its current tools. Expand it to choose individual tools."
                  >
                    {!mcpConnections.length ? (
                      <p>Connect an MCP server to select its tools here.</p>
                    ) : (
                      mcpConnections.map((c) => (
                        <MCPToolSelector
                          key={c.id}
                          name={c.name}
                          tools={c.tools}
                          selected={
                            editor.data.mcps.find(
                              (m: any) => m.connection_id === c.id,
                            )?.tools || []
                          }
                          onChange={(tools) => {
                            const updated = editor.data.mcps.filter(
                              (m: any) => m.connection_id !== c.id,
                            );
                            update(
                              "mcps",
                              tools.length
                                ? [...updated, { connection_id: c.id, tools }]
                                : updated,
                            );
                          }}
                        />
                      ))
                    )}
                    <button
                      type="button"
                      className="resource-link"
                      onClick={() => navigate("/mcp")}
                    >
                      Manage MCP servers <ArrowUpRight size={14} />
                    </button>
                  </FormSection>
                </>
              )}
              {editor.kind === "groups" && (
                <>
                  <FormSection
                    title="Agents in this group"
                    description="Add existing PR agents and choose how many instances of each to run in parallel. Each instance uses its reviewer's saved minimum time."
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
                  {editor.data.type !== "mcp" && (
                    <Field label="Connection type">
                      <select
                        value={editor.data.type}
                        onChange={(e) => update("type", e.target.value)}
                      >
                        <option value="gateway">
                          OpenAI-compatible gateway
                        </option>
                        <option value="openai">OpenAI</option>
                        {editor.data.type === "anthropic" && (
                          <option value="anthropic" disabled>Anthropic (requires a gateway)</option>
                        )}
                      </select>
                    </Field>
                  )}
                  <Field
                    label={
                      editor.data.type === "mcp"
                        ? "MCP endpoint URL"
                        : "API base URL"
                    }
                    hint={
                      editor.data.type === "mcp"
                        ? "For example: https://your-server.example.com/mcp"
                        : "Use a Responses-compatible gateway, including /v1 if required. Leave blank for direct OpenAI."
                    }
                  >
                    <input
                      type="url"
                      value={editor.data.base_url}
                      onChange={(e) => update("base_url", e.target.value)}
                      required={["gateway", "mcp"].includes(editor.data.type)}
                    />
                  </Field>
                  {editor.data.type === "mcp" && (
                    <MCPAuthFields data={editor.data} update={update} />
                  )}
                  {(editor.data.type !== "mcp" ||
                    (editor.data.auth_type || "bearer") === "bearer") && (
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
                  )}
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
                  <div className="info">
                    Optional app setup runs inside each agent’s sandbox when it
                    runs a review.
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
              <button type="button" disabled={busy} onClick={closeEditor}>
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {busy && !testingModel ? (
                  <>
                    <Loader2 size={15} className="spin" />
                    Saving…
                  </>
                ) : editor.kind === "connections" ? (
                  editor.data.type === "mcp" &&
                  editor.data.auth_type === "oauth" ? (
                    "Save & connect"
                  ) : (
                    "Save & test"
                  )
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
          title="Connect GitHub repositories"
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
                form.target = "_self";
                form.action = r.url;
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = "manifest";
                input.value = r.manifest;
                form.append(input);
                document.body.append(form);
                form.submit();
                form.remove();
                setGithubCreate(false);
                setNotice(
                  "Opening GitHub. After you approve the App and choose repositories, GitHub will return you here.",
                );
              });
            }}
          >
            <div className="dialog-body">
              <p>
                Choose where your repositories live. GitHub will ask you to
                create the LiteReview App there and choose which repositories it
                can access, then return you here.
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
                  hint="Use an organization where you are an owner or have the organization-wide App Manager role. If GitHub denies creation, ask an owner to grant that role or create the App. Repository installation may need separate approval."
                >
                  <input
                    value={appOwner}
                    onChange={(e) => setAppOwner(e.target.value)}
                    placeholder="your-organization"
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
                setGithubRevision((v) => v + 1);
                setNotice("App credentials saved. Checking repository access…");
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
                {setup.github_reset
                  ? "Enter the App credentials to replace the previous setup, or cancel and choose Undo reset to restore it."
                  : "Leave existing secret fields blank to keep their saved values."}{" "}
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
