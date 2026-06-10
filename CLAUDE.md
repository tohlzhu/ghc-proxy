# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GHC Proxy is a proxy service that wraps GitHub Copilot's model API into a unified endpoint compatible with Claude Code, Codex, and OpenClaw. It manages multiple GHC account credentials, routes frontend users to backend GHC accounts (1:1 mapping to avoid multi-user detection), and provides centralized token accounting, prompt logging, and user analytics.

## Tech Stack

- **Language**: Python (preferred) or TypeScript
- **Database**: PostgreSQL (persistent state), Redis (caching, session management)
- **Message queue**: Kafka (prompt logs, audit trail)
- **Deployment**: Kubernetes (designed for horizontal scaling and high availability)

## Key Design Constraints

- Each frontend user maps to exactly one backend GHC account at a time to avoid GHC detecting account sharing
- GHC login credentials are stored in the database (not in per-container filesystem state), enabling management of many accounts without containerized isolation
- Credentials must be proactively refreshed before expiry via scheduled tasks to maintain persistent login sessions
- When a GHC account's login expires, the system automatically routes the affected user to an available idle account
- All scripts and configs must use placeholders — never real tenant IDs, secrets, tokens, or customer data
