# GitHub App Setup & Production Registration Guide (Phase 3)

This document provides step-by-step instructions for creating and configuring the official **CodeSentinel GitHub App** on GitHub.

---

## 1. Automated Setup via GitHub App Manifest

GitHub supports pre-configured application creation through a Manifest.

### Step 1: Navigate to GitHub App Creation
1. Sign in to GitHub and navigate to:
   - **Personal Account:** [github.com/settings/apps/new](https://github.com/settings/apps/new)
   - **Organization Account:** `https://github.com/organizations/<YOUR_ORG>/settings/apps/new`

### Step 2: Use the Following Configuration Manifest

```json
{
  "name": "CodeSentinel",
  "url": "https://codesentinel.yourdomain.com",
  "hook_attributes": {
    "url": "https://codesentinel.yourdomain.com/api/v1/webhooks/github",
    "active": true
  },
  "redirect_url": "https://codesentinel.yourdomain.com/api/v1/auth/github/callback",
  "public": false,
  "default_permissions": {
    "checks": "read",
    "contents": "read",
    "metadata": "read",
    "pull_requests": "read",
    "statuses": "read"
  },
  "default_events": [
    "check_run",
    "check_suite",
    "pull_request",
    "push"
  ]
}
```

---

## 2. Manual Configuration (Alternative)

If creating manually through the GitHub UI:

### Basic Information:
- **GitHub App name:** `CodeSentinel` (or `CodeSentinel-<your-org>`)
- **Homepage URL:** `https://codesentinel.yourdomain.com`
- **Callback URL:** `https://codesentinel.yourdomain.com/api/v1/auth/github/callback`
- **Expire user authorization tokens:** Enabled

### Webhook Settings:
- **Active:** Checked
- **Webhook URL:** `https://codesentinel.yourdomain.com/api/v1/webhooks/github`
- **Webhook secret:** Generate a high-entropy 32-byte secret (`openssl rand -hex 32`)
- **SSL verification:** Enable SSL verification

### Repository Permissions (Least Privilege):
- **Checks:** Read-only (inspect check runs)
- **Contents:** Read-only (inspect files and diffs)
- **Metadata:** Read-only (mandatory repository metadata)
- **Pull requests:** Read-only (inspect PR context)
- **Commit statuses:** Read-only (inspect CI statuses)

> [!IMPORTANT]
> **Safety Invariant:** CodeSentinel strictly requires **read-only** permissions. Never grant Write or Admin permissions. CodeSentinel does not perform autonomous branch pushes or pull request merges.

### Subscribe to Events:
- `Check run`
- `Check suite`
- `Pull request`
- `Push`

---

## 3. Gathering Application Credentials

After creating the GitHub App, capture the following credentials:

1. **App ID:** Found under **General > About**.
2. **Client ID:** Found under **General > About**.
3. **Client Secret:** Click **Generate a new client secret** and copy the value immediately.
4. **Webhook Secret:** The secret you generated in Step 2.

Add these into your production environment or secret manager (`.env.production`):

```bash
GITHUB_APP_ID=123456
GITHUB_APP_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
GITHUB_APP_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_WEBHOOK_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

