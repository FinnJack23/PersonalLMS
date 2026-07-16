# Shared Workspace Setup

## Canonical WSL location

```text
/home/ajsch/projects/personal-lms
```

## Create the folder

```bash
mkdir -p /home/ajsch/projects/personal-lms
cd /home/ajsch/projects/personal-lms
```

Place the contents of this package inside that folder.

## Initialize Git

```bash
cd /home/ajsch/projects/personal-lms
git init
git add .
git commit -m "docs: initialize Personal LMS architecture and handoff"
```

If a remote repository is created later, add it only after deciding whether the project should be private or public. The initial repository should be private because it will eventually reference private learning records and local paths.

## Open the same folder in VS Code/Claude Code

From WSL:

```bash
cd /home/ajsch/projects/personal-lms
code .
```

Claude Code should run with this directory as its current working directory.

## Add the folder to Codex

In Codex, choose **Open Folder** and select the WSL path:

```text
\\wsl.localhost\Ubuntu\home\ajsch\projects\personal-lms
```

The distribution name may differ from `Ubuntu`; use the installed WSL distribution shown by:

```powershell
wsl -l -v
```

Codex should read `AGENTS.md` at the repository root.

## Concurrent work

Opening the same folder in Codex provides visibility, but do not allow Claude and Codex to modify the same worktree concurrently.

For active Codex implementation:

```bash
cd /home/ajsch/projects/personal-lms
git worktree add ../personal-lms-codex -b codex/<task-name>
```

For Qwen work:

```bash
cd /home/ajsch/projects/personal-lms
git worktree add ../personal-lms-qwen -b qwen/backlog
```

Open each worktree in a separate window.

## Recommended window roles

| Window | Path | Role |
|---|---|---|
| Claude | `~/projects/personal-lms` | primary implementation |
| Codex review | `~/projects/personal-lms` | inspection only while Claude writes |
| Codex active | `~/projects/personal-lms-codex` | isolated implementation/review fixes |
| Qwen | `~/projects/personal-lms-qwen` | low-risk local backlog |

## First Claude command

After the folder is open, use the launch prompt in:

```text
docs/handoffs/2026-07-16_CLAUDE_NIGHT_RUN.md
```

## Safety check before every session

```bash
git branch --show-current
git status --short --branch
git worktree list
```

Verify the agent is in the intended branch and worktree before approving writes.
