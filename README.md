# CupClaw — Share OpenClaw with Your Team

> **Works with [OpenClaw](https://openclaw.ai)** — A lightweight web interface for sharing OpenClaw with your team.

## What Is CupClaw

CupClaw is a lightweight web interface that helps OpenClaw administrators share their AI assistant with their team.

Team members access CupClaw through a browser — **without needing to use the OpenClaw UI** or share admin credentials. Everyone uses their own account to interact with OpenClaw through CupClaw.

## What Problem Does It Solve

- **Shared AI for teams**: One OpenClaw instance, multiple users simultaneously, each with their own account
- **Local deployment, data security**: All data stays within your LAN, never uploaded to external services
- **Multi-user account system**: Each team member logs in independently; OpenClaw recognizes different users
- **Zero learning curve**: Download, deploy, and go — team members don't need to know how OpenClaw works

## Key Features

- **Multi-user access**: One OpenClaw, many team members, each with their own account
- **Independent of OpenClaw UI**: Team members don't need to use the OpenClaw interface at all
- **Local deployment**: All data flows locally, no external network dependency
- **Role-based permissions**: Regular user / Department head / Ops — three permission levels, configurable by admin
- **File upload**: Supports PDF, Excel, images, documents and more for analysis
- **Zero-barrier onboarding**: Deploy and open in browser — done

## User Memory (Recommended)

OpenClaw supports individual memory spaces for each user, recording usage habits and preferences. The more a user interacts, the better CupClaw understands their needs.

### Benefits of User Memory

- **Personalized service**: OpenClaw remembers your query preferences, common terminology, and file habits
- **Continuous context**: Pick up exactly where you left off — no need to re-explain background
- **Better experience over time**: The more you use it, the smarter it gets

### Things to Know About User Memory

- **Data storage**: User history is stored locally on the machine where OpenClaw is deployed
- **Privacy notice**: Admin should inform team members that OpenClaw remembers their usage habits
- **How to disable**: Admin can turn off user memory in OpenClaw settings if not needed

## Deployment

1. Download and extract CupClaw to your OpenClaw machine
2. OpenClaw will automatically detect and load CupClaw
3. Access CupClaw through your browser

For detailed steps, see `docs/User-Guide.md`.

## Quick Start

```bash
# Download
tar -xzf cupclaw-v1.0.0.tar.gz
cd cupclaw
# That's it — OpenClaw handles the rest
```

## Tech Stack

- **Frontend**: Web-based interface
- **Backend**: Node.js service
- **Integration**: OpenClaw API

## Documentation

- `README.md` — This file (Chinese)
- `docs/使用指南.md` — Detailed user guide (Chinese)
- `docs/User-Guide.md` — Detailed user guide (English)

## License

See `LICENSE` file.
