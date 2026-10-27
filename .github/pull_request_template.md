<!-- PR title = Conventional Commits subject: type(scope): summary (≤ 72 chars). -->

## Changes

- <one bullet per change>

Tasks: T### · Scenarios: AS-x.y

## Checklist

- [ ] Tests named after the scenarios (`test_as_<major>_<minor>_<what>`) were written first and pass
- [ ] `make check` passes (ruff, mypy, pytest -n 2, contract drift)
- [ ] Nothing under `specs/` or `contracts/` changed (contract changes go through a spec amendment)
- [ ] Only my machine's packages and routers are touched; cross-machine needs went through a brain handoff
- [ ] No secrets, no personal data; fixtures use synthetic numbers (`+57300000xxxx`)
- [ ] No AI attribution anywhere: no `Co-Authored-By`, no "Generated with" lines, in commits or in this description
- [ ] Brain updated: journal entry, lane STATUS, handoff if another machine depends on this
