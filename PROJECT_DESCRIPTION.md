# Project Description

IT Asset Management centralizes asset inventory, employee assignments, infrastructure records, repairs, lifecycle events, approvals, QR-based retrieval, audit history, and operational reporting.

The application uses Flask and SQLAlchemy with role-based and location-based authorization. It includes repeatable sample data, secure file validation, CSRF protection, login throttling, security headers, automated integration tests, and release verification tooling.

## Key Engineering Outcomes

- Implemented end-to-end asset assignment, transfer, return, repair, and lifecycle workflows.
- Enforced role permissions and location-scoped access at both route and query levels.
- Added QR-assisted asset retrieval with a minimized read-only public profile.
- Built repeatable sample-data generation and a project-local runtime boundary.
- Added automated tests for authorization, workflows, uploads, reporting, and public routes.
