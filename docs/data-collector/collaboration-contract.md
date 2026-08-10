# Cross-Model / Cross-Client Collaboration Contract

## 1. Repository as Shared Memory

The Git repository is the only authoritative shared memory across clients and models. It must persist:

- requirements and frozen contracts;
- SOURCE_SPEC files and decisions;
- evidence, open questions and known limitations;
- implementation and test state;
- handoff state needed by the next role.

Conversation may help people discuss work, but `Chat history is NOT part of the project contract.` It must never be the only location of a decision or result.

## 2. Zero-Context Continuation

A different model in a different client, with zero previous chat context, must be able to continue from repository artifacts. If it cannot identify the current scope, contract, evidence, changed files, blockers and next action from the repository, the handoff is incomplete.

## 3. Role, Client and Model

These are three independent layers:

| Layer | Meaning | Examples |
|---|---|---|
| Role | Current responsibility and authority boundary | Source Researcher, Developer, Tester |
| Client | Execution environment | Codex, Claude Code CLI |
| Model | Model running in that client | GPT/Codex model, DeepSeek |

No permanent mapping is allowed. In particular, `Developer = Codex`, `Researcher = DeepSeek` and `Tester = Claude Code` are not project rules. The same role may be executed by different clients/models, and a client/model may execute different roles in separate authorized tasks.

## 4. Model Independence

Role boundaries are defined by repository contracts, not model capability.

- A stronger model does not gain additional authority.
- A weaker model does not receive relaxed acceptance criteria.
- A Source Researcher must not modify production implementation, regardless of model strength.
- A Developer must not redefine source semantics when a SOURCE_SPEC appears wrong; report `SPEC_MISMATCH` and return to Source Researcher when blocking.
- A Tester must not silently repair implementation while testing; report `TEST_FAIL` with evidence and route it to Developer.

```text
Role authority > model capability.
```

All roles remain subject to the frozen contract and current scope.

## 5. Independent Testing

When practical, Tester SHOULD use a different model and/or independent client context from Developer. This is a recommendation, not a role assignment or a hard dependency.

The hard requirement is independent validation: Tester must inspect the repository, applicable SOURCE_SPEC, changed files and deterministic evidence directly. A Developer's summary is not evidence by itself.

## 6. Handoff Protocol

Each role finishes with repository artifacts sufficient for zero-context continuation. A handoff must identify current status, evidence, changed files, limitations, open questions and the next action.

### Source Researcher → Developer

Minimum handoff:

- frozen or explicitly blocked SOURCE_SPEC;
- research evidence and evidence limitations;
- open questions and known uncertainty;
- current status;
- next required Developer action.

Developer does not need the Researcher's chat history.

### Developer → Tester

Minimum handoff:

- implementation status;
- changed files;
- SOURCE_SPEC sections implemented;
- known limitations and unresolved assumptions;
- exact test command;
- exact execution command when applicable.

Tester must inspect code and SOURCE_SPEC independently.

### Tester → User / Next Round

Minimum handoff:

- `TEST_PASS` or `TEST_FAIL`;
- executed commands and exit results;
- failing cases and evidence;
- unresolved blockers;
- recommended next role/action.

The handoff records status; it does not grant permission for a later phase.

## 7. Handoff Artifact and Run Convention

Use `runs/_templates/handoff.md` as the minimum structure. From Phase 1 onward, an actual round may contain:

```text
runs/phase-XX-round-YY/
    scope.md
    handoff.md
    status.txt
    ...other evidence artifacts as needed...
```

`handoff.md` is the minimal cross-client continuation entry point. Existing artifacts may carry research, implementation or test details; do not manufacture empty documents merely to satisfy a template. `client`, `model` and `role` fields are traceability metadata only and never grant or reduce authority.

This is a repository artifact, not a handoff engine, workflow engine, router, automatic orchestrator, LLM coordinator or runtime state machine. The project still has exactly three logical roles: Source Researcher, Developer and Tester.

## 8. Source-of-Truth Priority

When clients/models disagree, use this order:

```text
Frozen canonical contracts
        >
Current phase / round scope
        >
Frozen SOURCE_SPEC
        >
Current handoff
        >
Implementation assumptions
        >
Chat history
        >
Agent inference
```

If repository artifacts conflict with each other, do not choose a preferred document silently. Report the conflict; if it affects the current task, mark the task `BLOCKED` until the contract is resolved.
