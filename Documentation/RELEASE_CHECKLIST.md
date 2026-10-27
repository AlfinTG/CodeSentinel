# Release and Demo Checklist

## Product truth
- [ ] README distinguishes planned, implemented, tested and verified features.
- [ ] Every shown feature works on deployed build; no fake live provider output.
- [ ] No unsupported accuracy, time-saved or bug-prevention claims.
- [ ] Demo is repeatable and records actual model/provider use.

## Security and reliability
- [ ] HMAC raw-body verification and duplicate delivery tests pass.
- [ ] Object-level authorization tests pass.
- [ ] No secrets in Git history, frontend, logs, evidence or model inputs.
- [ ] Runner limits/isolation tests pass; otherwise external repository execution is disabled.
- [ ] Provider errors do not change test outcome.
- [ ] Request/response size, rate, time and output limits are configured.
- [ ] TLS, session/CSRF protections, safe logging and kill switch checked.
- [ ] Retention/deletion, backup/restore and rollback documented as appropriate.

## Reproducibility
- [ ] Clean clone setup works with lockfiles and exact commands.
- [ ] Migrations succeed from empty database.
- [ ] CI lint/type/test/build/security scans pass.
- [ ] Controlled baseline and regression are reproducible.
- [ ] Public app/demo URL works for evaluators.

## Submission
- [ ] Confirm current official rules, deadline/timezone, eligible model and selected track.
- [ ] Public source repository has owner-approved open-source LICENSE.
- [ ] Submission links, demo recording and model/provider disclosure are accurate.
