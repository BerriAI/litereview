import { ArrowLeft } from "lucide-react";
import { Brand } from "./ui";

export function Documentation() {
  return (
    <div className="docs">
      <header className="docs-header">
        <a className="brand" href="/" aria-label="LiteReview home">
          <Brand />
        </a>
        <a className="button" href="/">
          <ArrowLeft size={14} /> Open workspace
        </a>
      </header>
      <main className="docs-content">
        <h1>Documentation</h1>
        <p className="docs-intro">
          LiteReview runs your PR review agents from a GitHub comment. Each
          agent has its own model, instructions, and tools. Agents run in
          parallel in separate Modal sandboxes and report back on the PR.
        </p>

        <section>
          <h2>Start a review</h2>
          <ol className="docs-steps">
            <li>
              <h3>Connect a GitHub repository</h3>
              <p>
                Open <a href="/connections">Connections → GitHub</a>. LiteReview
                checks your existing App and shows the account that owns it.
                Continue installation on GitHub, select your repositories, then
                return here and choose Connect beside a repository. Installation
                details are filled in automatically.
              </p>
              <p>
                A private App can only be installed on its owning account. If
                your App belongs to a personal account and you need organization
                repositories, choose Connect an organization instead. An
                organization owner may need to finish this step. Your existing
                model connections, agents, and access settings stay saved.
              </p>
            </li>
            <li>
              <h3>Create a PR agent</h3>
              <p>
                Reuse your saved model connection, or add a gateway or provider
                under Connections → Models. In <a href="/agents">PR agents</a>,
                name the reviewer, choose a model, and describe what it should
                check. New agents default to Codex. Enable tools as needed.
                Attach skills or MCP tools if useful. Test agent runs a small
                private check of that harness, model, and selected capabilities
                in Modal. It uses model and Modal credits without touching a
                GitHub repo.
              </p>
            </li>
            <li>
              <h3>Set access</h3>
              <p>
                In <a href="/access">Access</a>, add GitHub usernames under
                People and manage connected repositories under Repositories.
                Give each one All agents and groups (including future additions)
                or select specific ones. New repositories start with All access;
                you can narrow it here.
              </p>
              <p>
                Both the person and the repository must have access to each name
                in a command. The person must also have GitHub access to the
                repository. Access to a group includes its members when called
                through that group.
              </p>
              <p>
                With Google SSO enabled, each caller also signs in with their
                workspace Google account and links GitHub on{" "}
                <a href="/account">Your LiteReview account</a>. Renew Google
                sign-in when authorization expires. A People entry alone does
                not bypass SSO.
              </p>
            </li>
            <li>
              <h3>Comment on an open PR</h3>
              <p>
                Use agent names, group names, or both in a new comment, with
                nothing else in it:
              </p>
              <pre>
                <code>@litereview frontend security</code>
              </pre>
              <p>
                If frontend is an agent and security is a group of five, this
                starts six independent reviewers. Use just @litereview frontend
                to run that agent alone. Each checks the PR commit at the time
                you called it.
              </p>
              <p>
                Agent and group names must be unique, ignoring capitalization.
                Repeating a name in one comment runs it once. An agent selected
                directly and through a group runs separately for each selection.
              </p>
              <pre>
                <code>@litereview frontend t=5 security t=10</code>
              </pre>
              <p>
                Add t=MINUTES after a name to request a minimum review duration
                for that agent or every instance in that group. The clock starts
                after setup. Agents continue investigating and cannot finish
                successfully before their time elapses. Failures and exhausted
                limits are reported as incomplete. Omit t= to keep the usual
                behavior. Use whole minutes, up to 120 by default. Tools,
                skills, and model selections stay the same.
              </p>
            </li>
            <li>
              <h3>Read the results</h3>
              <p>
                LiteReview updates a PR comment with each agent’s findings or
                “No findings.” Open <a href="/runs">Runs</a> for details,
                activity, and screenshots. An incomplete check means the agent
                could not finish its configured review. Results are summary
                comments; agents do not push fixes.
              </p>
            </li>
          </ol>
        </section>

        <section>
          <h2>Resume GitHub setup</h2>
          <p>
            To start over, choose Reset in Connections → GitHub. Reviews pause
            while your agents, models, skills, MCP connections, repository
            settings and secrets, access rules, and review history stay saved.
            The App remains on GitHub. Undo reset restores the previous
            connection, even after a refresh. Saving a new App replaces that
            backup. Finish active PR reviews before resetting.
          </p>
          <p>
            App saved means credentials are stored; it does not mean GitHub has
            granted repository access. Connections → GitHub checks the App when
            you open the page and when you return from GitHub. Use Check again
            after an owner approves installation. A failed check keeps your
            settings and offers a retry.
          </p>
          <p>
            If access was removed, restore it on GitHub. If you replace the App,
            use Reconnect beside your repositories to update their installation
            while keeping review environments and access restrictions. App
            settings and recovery lets you update credentials. Optional app
            startup commands belong in the connected repository’s editor.
          </p>
        </section>
        <section>
          <h2>Optional harnesses and groups</h2>
          <p>
            Agents can use Built-in, Pi, OpenCode V1, OpenCode V2, Codex, Claude
            Code, or Deep Agents. Codex needs a Responses-compatible endpoint;
            Claude Code needs a Messages-compatible endpoint. A gateway can
            translate these protocols. Test agent checks your selected harness
            and capabilities. Skills and MCP selections stay with the agent.
          </p>
          <p>
            In <a href="/groups">Agent groups</a>, bundle existing agents and
            choose how many instances to run. Groups are optional; you can call
            one agent directly for your first review.
          </p>
        </section>

        <section>
          <h2>Choose what an agent can do</h2>
          <dl className="docs-capabilities">
            <div>
              <dt>Code only</dt>
              <dd>Read the PR diff and repository files.</dd>
            </div>
            <div>
              <dt>Terminal</dt>
              <dd>Run commands, install dependencies, and execute tests.</dd>
            </div>
            <div>
              <dt>Browser</dt>
              <dd>
                Interact with the running app using Playwright and inspect
                screenshots.
              </dd>
            </div>
            <div>
              <dt>Computer</dt>
              <dd>See the desktop and use its mouse and keyboard.</dd>
            </div>
          </dl>
          <p>
            For agents that run your app, edit its repository in Connections.
            Set the setup command, start command, and a localhost readiness URL.
            Add any test credentials the app needs. Each sandbox starts fresh.
          </p>
        </section>

        <section>
          <h2>Reuse skills and tools</h2>
          <p>
            <a href="/skills">Skills</a> hold reusable instructions and optional
            supporting files. Create one or upload a ZIP with SKILL.md, then
            attach it to agents.
          </p>
          <p>
            Add shared MCP servers under Connections → MCP servers using a
            Streamable HTTP URL. Test a connection to discover its tools, then
            select which tools each agent can use. Connecting a server does not
            enable it for every agent.
          </p>
        </section>
      </main>
    </div>
  );
}
