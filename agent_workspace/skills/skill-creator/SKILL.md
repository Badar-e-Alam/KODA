---
name: skill-creator
description: Create new KODA skills, modify existing skills, and validate skill structure. Use when the user wants to add a new skill, update a skill, or inspect the skills directory.
---

# Skill Creator

You are helping the user create or modify a KODA skill. Skills are stored under
`agent_workspace/skills/` and follow the Agent Skills standard.

## Skill structure

Each skill is either:
1. A directory containing a `SKILL.md` file (preferred for skills with assets)
2. A standalone `.md` file in the skills root (for simple skills)

### Directory layout (preferred)

```
agent_workspace/skills/
  my-skill/
    SKILL.md          # Required: frontmatter + instructions
    assets/           # Optional: templates, examples, configs
```

### SKILL.md format

```markdown
---
name: my-skill
description: One-line description of what the skill does and when to use it
---

# Skill Title

Instructions for the agent when this skill is loaded...
```

## Rules

### Naming
- Lowercase letters, digits, and hyphens only: `[a-z0-9-]+`
- No leading/trailing hyphens, no consecutive hyphens
- Max 64 characters
- Directory name must match the `name:` field

### Description
- Required, max 1024 characters
- Should clearly state **what** the skill does and **when** to invoke it
- Written for the agent (the model reads this to decide whether to load the skill)

### Content
- The body after the frontmatter contains the full instructions
- Use markdown headings, code blocks, and lists
- Reference files with paths relative to the skill directory
- Include concrete examples and anti-patterns where helpful

## Workflow

When asked to create a skill:

1. Ask the user what the skill should do (if not already clear)
2. Choose a name: lowercase, hyphenated, descriptive
3. Create the directory: `agent_workspace/skills/{name}/`
4. Write `SKILL.md` with frontmatter + instructions
5. Verify the skill loads by reading the file back and checking:
   - Frontmatter parses (starts and ends with `---`)
   - `name:` matches directory name
   - `description:` is present and under 1024 chars
   - Body has useful instructions

When asked to modify a skill:

1. Read the existing `SKILL.md`
2. Make the requested changes
3. Verify the frontmatter is still valid

When asked to list skills:

1. Run `ls agent_workspace/skills/`
2. For each entry, read its SKILL.md frontmatter and report name + description
