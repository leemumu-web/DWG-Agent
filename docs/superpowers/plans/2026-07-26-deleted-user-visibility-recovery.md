# Deleted User Visibility and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and execute this plan inline; this repository explicitly forbids subagent delegation.

**Goal:** Make soft-deleted usernames visible and safely recoverable from user management.

**Architecture:** The existing paginated user endpoint will include every lifecycle state. A dedicated administrator-only restore command will lock and restore a deleted row to `disabled`, clear `deleted_at`, and audit the action. The frontend will expose only the safe restore action for deleted rows.

**Tech Stack:** FastAPI, SQLAlchemy, MySQL, React, TypeScript, Ant Design, pytest.

---

## Implementation tasks

### Task 1: Backend lifecycle contract

**Files:**
- Modify: `backend/tests/security/test_security_boundaries.py`
- Modify: `backend/app/modules/identity/users.py`
- Modify: `backend/app/modules/identity/routes/users.py`

- [x] Add failing API tests proving deleted users remain in the administrator list and restore to `disabled`.
- [x] Run the focused tests and confirm they fail because the list filters deleted users and the restore endpoint is absent.
- [x] Add a row-locked restore service operation and the administrator-only audited route.
- [x] Run the focused identity and security tests to green.

### Task 2: Operator interface

**Files:**
- Modify: `backend/tests/contracts/test_frontend_contract.py`
- Modify: `frontend/src/features/identity/users.api.ts`
- Modify: `frontend/src/features/identity/UsersPage.tsx`

- [x] Add a failing contract test for the restore endpoint and visible “恢复账号” operation.
- [x] Run the focused contract test and confirm the expected failure.
- [x] Add the typed API call, deleted-user count, recovery mutation, and protected row actions.
- [x] Run the contract tests and production frontend build.

### Task 3: Release verification

**Files:**
- Modify only if verification exposes a defect.

- [x] Run identity, security, frontend-contract, lint, and build gates.
- [x] Commit the implementation without touching user-owned untracked data.
- [ ] Build and deploy the encrypted r9 application images while preserving MySQL, MinIO, and volumes.
- [ ] Verify container health, database/object counts, and the recovery flow in a real browser.
- [ ] Push the verified commit to `main`.
