<img src="app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Security policy

## Supported versions

Meshpool is a pre-1.0 beta. Only the latest 0.0.x release gets security fixes. Fixes ship in a new release, not as
patches to older ones. Update with Settings → About → **Check for updates**, or download the latest release.

| Version | Supported |
|---|---|
| Latest 0.0.x release | Yes |
| Older 0.0.x releases | No |

## Reporting a problem

Report privately. Do not open a public issue, pull request or discussion for a security problem.

- **Where:** GitHub private vulnerability reporting for this repository:
  <https://github.com/hamzaziada4422/meshpool-harness/security/advisories/new>. Only the maintainer sees it.
- **What to include:** the Meshpool version (Settings → About, or `meshpool --version`), your operating system, the
  steps to reproduce, and what an attacker gains. A proof of concept helps. Do not include real API keys or tokens.
- **Response times:** we confirm receipt within 3 working days and give a first assessment within 10 working days.
  We aim to ship a fix for a confirmed high-severity problem within 30 days, and tell you if it will take longer.
- **Disclosure:** we publish an advisory when the fix ships and credit you unless you ask us not to. Please give us
  the time above before you disclose.

Meshpool is maintained by a small team. These times are targets, not a contract.

## Threat model

Meshpool is an agent. It runs on your computer with your user's rights. Know what that means before you use it.

**It runs shell commands that a model chooses.** The model decides what to run. A wrong answer, or a prompt
injection hidden in a web page, file, issue or tool result the model reads, can lead to a harmful command.

- In **Ask** mode Meshpool asks before file edits and commands. In **Full access** it does not ask.
- On macOS (Seatbelt) and Linux (bubblewrap), commands run in a sandbox when the tool is present, except in Full
  access. The sandbox limits *writes* to the project folder, temp folders and tool caches. It does **not** limit
  *reads*: a command can read any file your user can read, including your SSH keys and Meshpool's own token files.
  The network stays open unless you turn it off (Settings, or `meshpool --no-network`).
- On Windows there is no sandbox.

**Connectors hold sign-in tokens on your disk.** Connector OAuth access and refresh tokens, and client secrets, are
stored in `mcp-auth.json` in the Meshpool data folder (`MESHPOOL_HOME`). The file is owner-only, but it is not
encrypted, and any process running as your user can read it, including a command the agent runs. Your model API key
is stored in `meshpool.config` in the same folder, also in plain text.

**Skills and MCP servers are third-party code.** An imported skill or MCP server runs with your user's rights. MCP
servers start as programs on your computer. Skills are instructions the model follows, and may include scripts.
Importing from Claude Code, Codex, Cursor and other agents copies their MCP server commands. Read what you import,
and only enable servers you trust.

**What leaves your computer.** Model requests carry your messages and the file content the agent reads. They go to
the endpoint you configure, which is the Meshpool endpoint (`llm.meshpool.ai`) by default. Connector sign-ins and
connector calls go from your computer to the service, with no Meshpool server in between. Telemetry is off unless you
turn it on (`MH_OTEL=1` and an endpoint you choose).

## Running it safely

- Run Meshpool in a container, a virtual machine, or a disposable checkout when you point it at code you did not
  write, or when you use Full access.
- Keep **Ask** mode for work on your own machine, and read each command before you approve it.
- Turn the network off for tasks that do not need it.
- Give connectors the smallest scopes the service allows, and remove the ones you no longer use.
- Never put API keys in code, issues or pull requests. Use `.env` locally and repository secrets in CI.
