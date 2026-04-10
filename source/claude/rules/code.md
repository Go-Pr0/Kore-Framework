# Code Writing Rules

## Read before editing

Do not propose or make edits to code before reading it. Read the actual file first. Understand existing behavior before suggesting modifications. Don't make assumptions from names or grep results alone.

## Backward compatibility

Do not try to make things you refactor backward compatible, make a clean split & dedicate yourself to the new system only physically removing any overhauled systems. For new systems never name them as "New" instead give them regular names. 

## File splitting

When adding code pushes a file past coherent boundaries, proactively split it rather than continuing to append. Split when:
- A new class or section has no shared state with existing content
- The file's name no longer accurately describes all its contents
- You're adding a distinct conceptual domain to a file that previously owned one domain

The rule against creating new files applies to gratuitous utility files — not to legitimate module boundaries. Splitting a file that now does two unrelated things is correct.

## Scope discipline

- Add only what was asked. No features, refactors, or "improvements" beyond the task.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't add error handling or validation for scenarios that can't happen. Validate at system boundaries; trust internal code and framework guarantees.
- No backwards-compatibility shims, unused `_var` renames, or `# removed` comments for deleted code. If something is unused, delete it completely.
- No helpers or abstractions for one-time operations. Three similar lines is better than a premature abstraction.

