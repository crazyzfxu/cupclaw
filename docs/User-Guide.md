# CupClaw User Guide

_For OpenClaw administrators. This guide helps you understand what CupClaw is and how to use it._

---

## What Is CupClaw

CupClaw is the web interface for OpenClaw. It lets administrators share their AI assistant with team members.

Team members **do not need to access the OpenClaw UI** or share admin credentials. They simply open CupClaw in a browser to use the AI assistant.

## Architecture

```
Team Member (Browser) — No need to enter OpenClaw UI
        ↓
CupClaw (Web Interface) — Manages user accounts, member just uses CupClaw
        ↓
OpenClaw (AI Core) — Handles requests, manages permissions, maintains user memory
```

CupClaw handles: user account management, web UI, message forwarding
OpenClaw handles: request processing, permission checking, user memory

## What to Do After Deployment

### 1. Create Team Member Accounts

The admin creates accounts for each team member in the CupClaw dashboard.

### 2. Assign User Roles

| Role | Who | Permissions |
|------|-----|-------------|
| Regular User | General members | Chat, query, file upload; submit write requests for approval |
| Department Head | Team leads | Regular user permissions + approve others' requests |
| Ops | Tech lead | Department head permissions + code modification execution |

### 3. Decide Whether to Enable User Memory

OpenClaw can maintain an independent memory space for each user. **Enabling is recommended**:

| Benefit | Description |
|---------|-------------|
| Personalized replies | OpenClaw remembers your query preferences and common terminology |
| Continuous context | Pick up where you left off — no need to re-explain background |
| Better experience | The more you use it, the smarter it gets |

| Things to Know | Description |
|----------------|-------------|
| Data storage | User history is stored locally on the machine running OpenClaw, not uploaded externally |
| Privacy notice | Team members should be informed that OpenClaw remembers their usage habits |
| How to disable | Admin can turn off user memory in OpenClaw settings if not needed |

**Admin should make this decision based on team needs.**

## Daily Workflow

1. **Member logs into CupClaw** — Uses their own account (no need to access OpenClaw)
2. **Member makes a request** — Chat, query, or file upload
3. **CupClaw forwards the request** — Includes user identity tag
4. **OpenClaw processes it** — Returns result based on user identity and permissions
5. **Result is pushed in real-time** — CupClaw displays to the member

## Permission Management

The following operations require approval:
- Adding, deleting, or modifying data records
- Sending external emails
- Changing scheduled tasks

Regular members submit a request; the department head approves or rejects it online.

## FAQ

**Q: How do I add a new team member?**
A: Create a new account in the CupClaw dashboard and set their role.

**Q: How do I revoke a user's access?**
A: Delete their account in the CupClaw dashboard, or adjust ACL configuration in OpenClaw.

**Q: How does OpenClaw identify different users?**
A: CupClaw sends messages with a user identity tag. OpenClaw reads the tag, identifies the user, and loads their corresponding memory.

**Q: Where are uploaded files stored?**
A: Files are stored in CupClaw's local directory on the server. OpenClaw reads and analyzes them, then returns the result — files are never uploaded to external services.

**Q: Do team members need to know how to use OpenClaw?**
A: No. Members only need to use CupClaw's web interface. All OpenClaw operations are transparent to members.
