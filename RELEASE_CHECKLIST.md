# Release Checklist

## Validation

- [x] Python sources compile.
- [x] Automated tests pass.
- [x] Templates compile and reference valid routes.
- [x] SQLite integrity and foreign-key checks pass.
- [x] Sample-data reset is repeatable.
- [x] Runtime databases, credentials, uploads, logs, and generated QR images are ignored.
- [x] Repository audit passes with zero findings.
- [x] The application uses a single runtime with no environment-switching controls.

## Before Publishing

- [ ] Confirm source-code redistribution rights.
- [ ] Review third-party library licenses and notices.
- [ ] Inspect the staged file list.
- [ ] Run `python tools/release_audit.py`.
- [ ] Configure repository visibility and branch protection.

## Before Hosting

- [ ] Configure unique host-managed secrets and administrator credentials.
- [ ] Configure the final HTTPS URL before generating QR images.
- [ ] Enable secure cookies.
- [ ] Configure monitoring, resource limits, patching, and backups.
- [ ] Replace process-local rate limiting and SQLite when running multiple instances.
