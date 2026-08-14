# Template — create-pr

PR body follows `.github/pull_request_template.md`.

```markdown
Short description sentence derived from the diff and, when linked, the issue.

Closes #ISSUE_NUMBER <!-- Omit when no issue is linked. -->

#### Background

Context from the issue body, or derived from commits and diff when no issue is linked.

#### Goals

Goals from the issue body, or derived from the change when no issue is linked.

- Goal 1
- Goal 2

#### Tasks

Complete

- [x] Completed task (mapped to specific commits/changes)
- [x] Another completed task
- [ ] Incomplete task (goal not addressed by diff)

to cover all goals.

#### Tests

What tests were run. Ask user — leave empty if none.

#### Other

- Supporting change not directly in issue scope
- Necessary refactor to enable the main work
- Any bundled unrelated change (note explicitly)
```

## Field mapping

| Section | Source |
|---------|--------|
| Short description | Validated issue title, if any, plus diff summary — one sentence |
| Closes | Validated issue in the resolved repository; omit when unlinked |
| Background | Issue body when linked; otherwise commits and diff |
| Goals | Issue goals when linked; otherwise commits and diff |
| Tasks | Cross-reference goals ↔ diff + commits; `[x]` if done |
| Tests | Ask user; empty if no tests run |
| Other | Commits/diff that don't map to any goal |
