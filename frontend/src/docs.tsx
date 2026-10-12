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
          LiteReview runs your PR review agents from a GitHub comment or a saved automatic review rule. Each
          agent has its own model, instructions, and tools. Agents run in
          parallel in separate Modal sandboxes and report back on the PR.
        </p>

        <section>
          <h2>Before you connect a team</h2>
          <p>
            LiteReview is an MIT-licensed beta for one trusted workspace per
            deployment. Every admitted member can manage shared connections,
            repositories, permissions, and review history. Reviews require a
            Modal account, a model provider or gateway, and a publicly reachable
            HTTPS service. Modal and model usage incur charges; there is no local
            sandbox backend.
          </p>
          <p>
            Set optional run time and model-call limits in each reviewer’s
            editor, and configure provider budgets and a deployment concurrency
            cap for your team. Repository code runs with network access in the
            sandbox; give it only disposable test credentials. See the{" "}
            <a href="https://github.com/BerriAI/litereview/blob/main/docs/DEPLOYMENT.md">
              deployment guide
            </a>{" "}
            to host your own workspace and the{" "}
            <a href="https://github.com/BerriAI/litereview/blob/main/SECURITY.md">
              security policy
            </a>{" "}
            for access and data boundaries.
          </p>
        </section>

        <section>
          <h2>Start a review</h2>
          <ol className="docs-steps">
            <li>
              <h3>Sign in and connect your identity</h3>
              <p>
                Open <a href="/start">Start a review</a>, sign in with your
                workspace Google account, and choose Connect GitHub. Link the
                account you use to comment on pull requests. Everyone admitted
                through Google SSO can manage the shared workspace.
              </p>
            </li>
            <li>
              <h3>Copy your review comment</h3>
              <p>
                Start a review shows repositories and checked reviewers
                available to your account. Choose a repository and reviewer,
                then use Copy comment. If repository or reviewer setup is
                missing, the page links to the setup you can finish.
              </p>
            </li>
            <li>
              <h3>Comment on an open PR</h3>
              <p>
                Post this comment to use the workspace’s default reviewer:
              </p>
              <pre>
                <code>@litereview</code>
              </pre>
              <p>
                The first reviewer becomes the default automatically. Change
                it with Set default on an agent’s row under PR agents. If an existing workspace
                has several reviewers, choose a default once. Caller and
                repository permissions still apply.
              </p>
              <p>
                To choose specific agents or groups, put their names in the
                comment, with nothing else in it:
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
                Reviewers stay in command order as their activity updates; group
                members and instances follow their saved order.
              </p>
              <p>
                Set Minimum review time (minutes) in the reviewer’s PR agents
                editor. Use 0 for no minimum, or enter whole minutes. Every instance uses its
                reviewer’s saved time, including inside groups. Comments contain
                only reviewer names or the bare mention; time options in comments are rejected. The clock
                starts after setup. Agents continue investigating and cannot
                finish successfully before that time elapses. Failures and
                exhausted limits are reported as incomplete. Longer reviews can
                use more credits. Private harness checks do not use this
                minimum.
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
                LiteReview reacts with 👀 and creates a progress comment for
                each agent. Each comment shows ✅ and that agent’s response as
                it finishes. Findings link to inline threads where you can
                reply; findings without a usable diff line stay in the agent
                comment. The agent’s 👀 reaction disappears when it finishes,
                and the request’s disappears when all agents finish. ✅ means
                finished, not issue-free. Expand Checks and execution details
                for verification results. Open <a href="/runs">Runs</a> and
                expand an agent to read its report. Findings, checks, activity,
                and screenshots start collapsed. An incomplete check means the
                agent could not finish work required by its instructions.
                Optional checks that were not run appear under Checks without
                making the review incomplete. Agents do not push fixes.
              </p>
            </li>
          </ol>
        </section>

        <section>
          <h2>Set up a workspace</h2>
          <p>
            Any signed-in workspace member can complete setup and manage agents,
            skills, connections, repositories, and review settings. Connections
            and model credits are shared. GitHub still controls who can install
            the App and which repositories it can access.
          </p>
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
                Every reviewer uses Codex with OpenAI or a Responses-compatible
                gateway. Anthropic models require a gateway. The private check uses model and Modal credits on a
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
                Admins can choose Make admin beside a signed-in teammate in People.
                Admins can view all workspace spend and manage admin roles. Changes
                take effect immediately, without linking GitHub or signing in again.
                Use Remove admin to revoke a role granted in People.
              </p>
              <p>
                New repositories allow all reviewers; narrow access under
                Repositories → repository options → Review permissions. Callers
                need a GitHub account linked after Google sign-in and GitHub
                access to the repository, including public repositories. The
                account link keeps working after signing out or browser session
                expiry. Without SSO, add approved GitHub usernames in People
                manually.
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
            retry. Each browser stays signed in independently, including when you
            sign in or out elsewhere. An expired LiteReview session asks you to
            sign in again and returns you to the page you opened, including a
            specific run. It does not interrupt review requests from your linked
            GitHub account.
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
      <h2>Create a reviewer from a prompt</h2>
      <p>Open <a href="/agents">PR agents</a>, choose <strong>Create agent</strong>, then <strong>Create from prompt</strong>.
        Describe the reviewer’s focus and choose <strong>Generate draft</strong>. Review the suggested command name,
        instructions, and capabilities, choose its review model, then <strong>Save agent</strong> and <strong>Test agent</strong>.
        Generating a draft does not save an agent or start a PR review.</p>
      <p>Draft generation defaults to Haiku 5.5 through a saved gateway or Anthropic connection.
        Change the connection or model in <a href="/settings">Settings → Agent creation</a>.
        This model writes the draft; the agent’s own model runs reviews. Generation uses one model request and incurs provider charges,
        which are separate from PR review spend. If creation fails, your prompt stays in the editor for retry.</p>

      <h2>Codex, Fusion, and agent groups</h2>
          <p>
            Every agent uses Codex. Choose an OpenAI connection or a gateway
            that supports the Responses API. A gateway can route compatible
            requests to other models, including Anthropic. Test agent checks
            Codex, the model, and enabled capabilities. Skills and MCP selections
            stay with the agent.
          </p>
          <p>
            Existing reviewers move to Codex automatically. If a reviewer uses a
            direct Anthropic connection, add a Responses-compatible gateway in
            <a href="/models"> Models</a>, select it in the reviewer’s Model
            section, and run Test agent. Models, instructions, skills, tools, and
            limits are preserved. Running reviews and history keep their original
            settings; queued reviews using a retired runtime need a new request.
          </p>
          <p>
            Codex retains its Apache-2.0 license. See the
            <a href="/THIRD_PARTY_NOTICES.md"> third-party notices</a> when
            redistributing the runtime.
          </p>
          <p>
            In <a href="/groups">Agent groups</a>, bundle existing agents and
            choose how many instances to run. Groups are optional; you can call
            one agent directly for your first review.
          </p>
          <p>
            To combine independent reviews into one result, open a reviewer in
            <a href="/agents"> PR agents</a> and choose Add model under Model.
            Adding a second model enables fusion automatically. Every model uses
            the same instructions, skills, and tools. Choose the Final review model,
            save, and run Test agent. You can add up to 16 models; remove extra
            models to return to a single reviewer.
          </p>
          <p>
            Each branch gets its own checkout. The aggregator verifies their reports
            against a fresh checkout and publishes one review. Expand Fusion in Runs
            to see branch results and activity. Failed branches remain visible as
            incomplete coverage; if every branch fails, the aggregator does not run.
          </p>
          <p>
            Fusion costs include every branch plus the aggregator. Time and call
            limits apply separately to each stage; minimum review time applies to
            branches. Workspace concurrency can queue branches. Choose a fusion
            reviewer with Set default in PR agents to run it with <code>@litereview</code>.
          </p>
        </section>

        <section>
          <h2>Choose what an agent can do</h2>
          <p>
            Review instructions define the agent’s task, scope, workflow,
            language, and voice. Attached skills support those instructions;
            the agent instructions take precedence when they conflict. The
            response is shown directly, including for tasks that do not call
            for bug findings.
          </p>
          <p>
            For relevant checks, agents inspect the project’s setup instructions
            and attempt missing dependency installation before reporting a
            blocker. They can install project packages and official toolchains
            in the disposable sandbox. State any required tests or setup
            restrictions in Review instructions. Optional verification gaps
            remain visible under Checks.
          </p>
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
                Instructions and skills can request Codex subagents. The main reviewer combines their evidence into one
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
        <section>
          <h2>Automatic reviews</h2>
          <p>Open Repositories → repository options → Automatic reviews. Add a rule, choose an agent or group, and save. Leave PR authors blank for everyone, or enter one or more GitHub usernames separated by commas. Bot accounts such as <code>dependabot[bot]</code> work too.</p>
          <p>Choose PR opened, Ready for review, New commits pushed, PR reopened, or Label added. A label trigger also needs a Trigger label. Enabled rules start paid reviews and post results on GitHub, including for external authors who have not signed in to LiteReview. Repository Review permissions still apply.</p>
          <p>For an existing GitHub App, enable Pull request under its Permissions &amp; events and keep Issue comment enabled. Return to Repositories to refresh the connection check. Apps created by this version include both subscriptions.</p>
          <p>After a matching event, check Runs for the automatic trigger and reviewed commit. If nothing starts, check the event subscription, enabled rule, author and label filters, and repository permissions. Drafts and closed PRs are skipped. Saving a rule does not run reviews retroactively.</p>
          <p>Each agent instance runs automatically once per PR commit, including when rules overlap. Failed reviews are not retried automatically; use an explicit <code>@litereview agent-name</code> comment to run again. Disabling a rule stops future triggers and lets accepted runs finish. Automatic review spend is attributed to the PR author.</p>
          <p>PR agents sorts by Times used: the number of PR runs that used each agent, including group use and incomplete runs. Model checks and fusion branches are excluded. Ties sort by name.</p>
        </section>
        <section>
          <h2>License and contributions</h2>
          <p>
            <a href="/LICENSE.txt">MIT license</a> ·{" "}
            <a href="/third-party-licenses.txt">Browser dependency licenses</a> ·{" "}
            <a href="https://github.com/BerriAI/litereview/blob/main/CONTRIBUTING.md">
              Contributing
            </a>
          </p>
        </section>
        <section>
          <h2>Track review spend</h2>
          <p>
            Admins can open <a href="/spend">Spend</a> for total model cost, PR run count,
            and average cost per run. Choose By agent, By user, or By repository to see
            the same breakdown. Runs also shows each review’s model cost,
            including every fusion model and the final review pass. Other members
            see no costs. Each agent counts a PR run once; its costs include every
            instance and fusion stage, including use through groups. Deleted agents
            retain their recorded spend. Admins manage additional admin roles in People;
            the deployment’s ADMIN_EMAILS setting supplies the initial admins.
          </p>
          <p>
            Costs use reported gateway amounts or estimates from token usage,
            cache usage, and available model rates. Estimates are labeled est.;
            a + marks a partial total. Older runs and requests without cost data
            show Unavailable. Averages include only finished runs with complete
            cost data. Agent checks, sandbox charges, and hosting are excluded.
            Spend tracks usage; it does not enforce a budget or replace your invoice.
          </p>
        </section>

      </main>
    </div>
  );
}
