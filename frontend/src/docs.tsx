import { ArrowLeft } from "lucide-react";
import { useEffect } from "react";
import { Brand } from "./ui";

export function Documentation() {
  useEffect(() => {
    document.title = "Documentation · LiteReview";
    if (location.hash === "#runtime")
      document.getElementById("runtime")?.scrollIntoView();
  }, []);
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
              <h3>Sign in and connect your identity</h3>
              <p>
                Open <a href="/start">Start a review</a>, sign in with your
                workspace Google account, and choose Connect GitHub. Link the
                account you use to comment on pull requests. Your workspace
                owner handles repository setup and approves who can request
                reviews.
              </p>
            </li>
            <li>
              <h3>Copy your review comment</h3>
              <p>
                Start a review shows repositories and checked reviewers
                available to your account. Choose a repository and reviewer,
                then use Copy comment. If setup or approval is missing, the page
                explains what your workspace owner needs to finish.
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
              <p>
                Workspace owners set Minimum review time (minutes) in the
                reviewer’s PR agents editor. Use 0 for no minimum, or whole
                minutes up to the workspace limit (120 by default). Every
                instance uses its reviewer’s saved time, including inside
                groups. Comments contain only names; time options in comments
                are rejected. The clock starts after setup. Agents continue
                investigating and cannot finish successfully before that time
                elapses. Failures and exhausted limits are reported as
                incomplete. Longer reviews can use more credits. Private harness
                checks do not use this minimum.
              </p>
              <p>
                Runs have no LiteReview time or model-call limit by default. In
                the agent editor, expand Optional limits to set Maximum run time
                (minutes), Maximum model calls, or both. Clear a field to remove
                its limit. Maximum run time includes setup; minimum review time
                does not create a maximum. These settings apply to future runs,
                including group instances. Modal’s 24-hour sandbox lifetime and
                provider limits still apply.
              </p>
            </li>
            <li>
              <h3>Read the results</h3>
              <p>
                LiteReview reacts with 👀 when it accepts your request. When all
                agents finish, it posts one comment with findings or “No issues
                found.” Expand Review notes for the summary. Workspace owners
                can open <a href="/runs">Runs</a> for details, activity, and
                screenshots. An incomplete check means the agent could not
                finish its configured review. Results are summary comments;
                agents do not push fixes.
              </p>
            </li>
          </ol>
        </section>

        <section>
          <h2>Set up a workspace</h2>
          <p>Workspace owners complete these steps once.</p>
          <ol className="docs-steps">
            <li>
              <h3>Connect repositories</h3>
              <p>
                Open <a href="/repositories">Repositories</a> and connect the
                GitHub App. Select repositories on GitHub; they appear in
                LiteReview automatically. Use Add repositories to change that
                selection later. Public and private repositories are supported.
              </p>
              <p>
                A private App must belong to the account where it is installed.
                To connect an organization, create an App owned by that
                organization. An organization owner or organization-wide App
                Manager can create it; installation may need an owner’s
                approval. Copy setup request includes the role-assignment steps
                you can send to an owner.
              </p>
              <p>
                To manage installation yourself, you need Admin access on each
                repository and the organization must allow repository admins to
                install GitHub Apps. An owner controls this in GitHub’s Settings
                → Member privileges → GitHub Apps. Once the App is installed,
                use Add repositories to change its selection.
              </p>
            </li>
            <li>
              <h3>Choose a reviewer</h3>
              <p>
                On Repositories, choose Configure reviewer. Reuse a saved model
                connection, choose a model, and save and check the reviewer.
                Codex is the default harness; a direct Anthropic connection uses
                Claude Code. The private check uses model and Modal credits on a
                sample project without touching your repositories. You can leave
                the page and return while it runs.
              </p>
              <p>
                If no model connection is saved, add your gateway or provider in
                <a href="/models"> Models</a>. Use
                <a href="/agents"> PR agents</a> to customize instructions,
                capabilities, skills, and MCP tools or inspect a failed check.
              </p>
            </li>
            <li>
              <h3>Invite people to sign in</h3>
              <p>
                Share the workspace URL. With Google SSO enabled, people appear
                in <a href="/people">People</a> after sign-in and link GitHub to
                start reviews. They can use all reviewers by default. Edit a
                person’s access to restrict it; an empty selection blocks
                reviews. Existing restrictions stay in place.
              </p>
              <p>
                New repositories allow all reviewers; narrow access under
                Repositories → repository options → Review permissions. Callers
                still need current Google authorization and GitHub access to the
                repository, including public repositories. Without SSO, add
                approved GitHub usernames in People manually.
              </p>
            </li>
            <li>
              <h3>Run your first review</h3>
              <p>
                Choose Start a review on Repositories, link your own GitHub
                identity, and copy the comment to an open PR. A successful
                private check verifies the reviewer; the findings posted on your
                PR are the first completed review.
              </p>
            </li>
          </ol>
        </section>
        <section>
          <h2>Resume or repair setup</h2>
          <p>
            Linking your GitHub account lets LiteReview recognize your PR
            comments. Connecting repositories lets the workspace access code.
            Repositories and Account show these separately. If your account is
            linked but repository access is missing, choose Connect GitHub
            repositories from Account or open Repositories to finish setup.
          </p>
          <p>
            Repositories checks GitHub when you open the page or return from
            GitHub. If approval is pending, use Check installation after an
            owner approves it. A failed check keeps your settings and offers a
            retry. An expired LiteReview session asks you to sign in again and
            returns you to the page you were using.
          </p>
          <p>
            To start GitHub setup over, open Settings → GitHub App → Reset
            setup. Reviews pause while agents, models, skills, MCP connections,
            repository environments and secrets, permissions, and history stay
            saved. The App remains on GitHub. Undo reset restores the previous
            connection, even after a refresh. Saving a new App replaces that
            backup. Finish active reviews before resetting.
          </p>
          <p>
            Restore removed GitHub access on GitHub. Repository syncing updates
            installations while keeping saved environments and restrictions.
            Repositories you explicitly remove in LiteReview stay excluded from
            syncing; use Previously removed repositories → Restore to add one
            back. Advanced credentials live in Settings → GitHub App.
          </p>
        </section>
        <section id="runtime">
          <h2>Configure the review runtime</h2>
          <p>
            If Repositories reports missing runtime setup, open the service’s
            environment settings on Render. Set MODAL_TOKEN_ID and
            MODAL_TOKEN_SECRET from a Modal token with permission to run
            sandboxes. Set DISPATCHER_ENABLED=true and redeploy the service.
            Keep these credentials private.
          </p>
          <p>
            LiteReview uses Render’s HTTPS address automatically. For a custom
            domain, set PUBLIC_URL to the full HTTPS origin that Modal can
            reach, with no path, and register that origin’s Google and GitHub
            callback URLs. Return to Repositories and run the private reviewer
            check to verify execution. A missing or invalid credential can still
            fail that check even when all environment variables are present.
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
              <dt>Code & terminal · Included</dt>
              <dd>
                Read the diff and files, install dependencies, run tests, and
                create reproducers in a disposable checkout.
              </dd>
            </div>
            <div>
              <dt>Subagents</dt>
              <dd>
                Instructions and skills can request delegation in harnesses that
                support it. The main reviewer combines their evidence into one
                result.
              </dd>
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
            For agents that run your app, open Repositories → repository options
            → Review environment. Set the setup command, start command, and a
            localhost readiness URL. Add any test credentials the app needs.
            Each sandbox starts fresh.
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
            Open <a href="/mcp">MCP servers</a> in the left sidebar and choose
            Add MCP server. Enter its name and Streamable HTTP URL, keep OAuth,
            and choose Save &amp; connect. Approve access with the provider.
            LiteReview tests the connection and displays the tools it discovers.
            Select tools in an agent to make them available to that agent.
          </p>
          <p>
            For token-based or public servers, choose Bearer token or No
            authentication and Save &amp; test. Test connection repeats
            discovery. If OAuth requires a registered app, Advanced OAuth
            settings shows the redirect URI and accepts its client ID and
            optional secret. Disconnect removes local authorization; revoke the
            grant in the provider’s account settings if needed.
          </p>
          <p>
            MCP servers, Skills, and Models stay accessible while editing an
            agent. Your agent draft is preserved when you navigate away or
            return from OAuth in the same tab. Choose Resume editing to continue
            with the new resources.
          </p>
        </section>
      </main>
    </div>
  );
}
