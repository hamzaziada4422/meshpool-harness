# Skills

A skill is a folder with a `SKILL.md` file: front matter with a name and a description, then the instructions. meshpool keeps your skills in `skills/` in its data folder. Add one from the Skills page, or drop a folder there. The agent sees each skill's name and description and loads the full instructions when a task fits. You can also ask for one by writing `$skill-name` in a message.

```markdown
---
name: fix-issue
description: Fix a GitHub issue by number.
when_to_use: the user gives an issue number and wants it fixed
argument-hint: [number] [priority]
disallowed-tools: [bash]
---
Read issue $1. Treat it as priority $2.
```

## Front matter

- `name` and `description`: what the agent sees in its list of skills.
- `when_to_use`: added to the description, to help the agent pick the skill at the right time.
- `argument-hint`: what the skill takes. In the instructions, `$ARGUMENTS` is everything given and `$1` to `$9` are its words. With `$fix-issue 123 high` in a message, `$1` is 123. A skill that names none of these gets the arguments at the end.
- `disable-model-invocation: true`: the agent never picks this skill by itself. It runs only when you write `$skill-name`. Use it for skills with side effects, such as a deploy.
- `user-invocable: false`: background knowledge for the agent. `$skill-name` in a message does not load it.
- `disallowed-tools`: tools the agent must not use while it follows this skill, for the rest of that turn. `bash` covers every command tool, and `edit` covers every file change.
- `paths`: file patterns, such as `["*.sql", "migrations/**"]`. The first time the agent works on a matching file, it is told the skill may fit.

`allowed-tools`, which in some other tools lets a skill run tools without asking, is not used: a skill file never grants itself permissions. Approvals and saved rules work the same with or without a skill.
