# Contributing

This is a portfolio lab project, not a production service.
Contributions are welcome, but the bar is: prove it runs.

## Rules

- Run the whole thing before you open a PR: `bash scripts/run_local_demo.sh`.
  It must end with verify PASS and a clean destroy.
- Unit tests must pass: `cd python && python -m unittest discover -s tests -v`.
- Terraform must be formatted and valid: `cd terraform && terraform fmt -check && terraform validate`.
- Do not commit Terraform state, `.terraform/`, `__pycache__/`, venvs, or credentials.
- Do not claim emulator results as production results. Lab
  limits stay labelled as lab limits.
- One change per PR. If you add a service, add its ADR in
  `docs/adr/`, its failure mode in `docs/SYSTEM_DESIGN.md`,
  and its test.
