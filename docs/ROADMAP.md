# Agency Roadmap

## Priority 1: First Run Wizard
**Impact:** High | **Effort:** Low | **Dependencies:** None

The single biggest adoption barrier identified in the expert panel review. A new user installs Agency, starts the server, and lands on an empty admin page with no guidance. A first-run wizard walks them through: naming their instance, pointing at an agent directory, initializing the folder structure, and seeing a populated UI for the first time.

**Why first:** Every other feature on this roadmap benefits from more users. More users come from easier onboarding. This is the highest-leverage change possible.

---

## Priority 2: Agent-Level MCP Management
**Impact:** High | **Effort:** Medium | **Dependencies:** None

Agents use `.mcp.json` files to configure which MCP servers they connect to. Currently there's no way to view or edit these through Agency. Adding MCP config viewing/editing to the agent profile page makes Agency the single pane of glass for agent configuration — not just identity and memory, but tooling.

**Why second:** Natural extension of the agent profile page we just built. High daily utility for anyone managing agents. Self-contained, no external dependencies.

---

## Priority 3: Multi-LLM Support
**Impact:** Critical for adoption | **Effort:** Medium-High | **Dependencies:** None

Agency currently assumes Claude agents (reads `CLAUDE.md` for identity). To support colleagues using GPT, Gemini, Llama, or mixed fleets, Agency needs to be LLM-agnostic. This means: supporting multiple agent definition file patterns (`CLAUDE.md`, `AGENTS.md`, `agent.yaml`, etc.), abstracting identity parsing, and ensuring the UI doesn't assume any specific LLM.

**Why third:** Critical for broad adoption but requires careful design work. The agent identity model touches many parts of the codebase. Best done after the profile page and MCP management are stable.

---

## Priority 4: Skills CRUD + Vercel Skills Integration
**Impact:** Medium-High | **Effort:** Medium | **Dependencies:** [Vercel Skills](https://github.com/vercel-labs/skills) framework

A skills management interface within Agency: browse installed skills per agent, create/edit/delete skills, and integrate with the [Vercel Skills](https://github.com/vercel-labs/skills) open-source framework for discovering, installing, and managing community skills. This turns Agency into a full agent development environment, not just a monitoring dashboard.

**Why fourth:** Powerful feature but depends on the Vercel Skills ecosystem maturing. The CRUD portion (managing local skill files) can ship independently; the browse/install integration with the Vercel Skills registry is additive.

---

## Priority 5: Home Assistant Integration
**Impact:** Medium (niche) | **Effort:** Medium | **Dependencies:** Home Assistant instance

Connect Agency to Home Assistant for: triggering agent dispatches based on HA events (time of day, presence, device state), surfacing agent decisions as HA notifications, and letting agents read/write HA entity states through the Agency UI. This bridges the gap between digital agents and physical environment automation.

**Why fifth:** High value for users who run both systems, but that's a narrower audience. The integration is self-contained and doesn't block other features.

---

## Priority 6: Native macOS Application
**Impact:** High (polish) | **Effort:** Very High | **Dependencies:** All other features stable

Package Agency as a native macOS app (Tauri or Electron) with: menu bar icon showing agent status, native notifications for clues/curiosities, local file picker for agent directories (no manual path entry), and auto-start on login. This is the "it just works" experience for non-technical colleagues.

**Why last:** Highest effort by far. The web UI needs to be feature-complete and stable before wrapping it in a native shell. A Tauri app is essentially a webview around the existing FastAPI server — all the features above need to work well first.

---

## Priority Matrix

| Feature | Impact | Effort | Dependency | Priority |
|---------|--------|--------|------------|----------|
| First Run Wizard | High | Low | None | 1 |
| MCP Management | High | Medium | None | 2 |
| Multi-LLM Support | Critical | Medium-High | None | 3 |
| Skills CRUD + Vercel Skills | Medium-High | Medium | Vercel Skills | 4 |
| Home Assistant | Medium | Medium | HA instance | 5 |
| Native macOS App | High | Very High | Stable UI | 6 |
