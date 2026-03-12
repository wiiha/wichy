# Svelte Frontend - Deferred

**Date:** 2026-03-12
**Status:** Deferred

## Context

As part of the wichy web UI redesign (see design doc from 2026-03-12), we planned to scaffold a Svelte frontend with:
- Single SPA under `src/wichy/frontend/`
- File-based routing (`src/routes/graph/+page.svelte`, etc.)
- Vite + Svelte + TypeScript build
- Shared layout with tool navigation

## Decision

**Stay with vanilla HTML for now.**

### Reasons to defer:

1. **Current HTML editor works well**
   - Bugs are fixed (save button re-enable, cancel modal)
   - Physics control added easily
   - ~700 lines is manageable in single file

2. **No immediate need for shared components**
   - Graph editor is the only web tool currently
   - Context editor (future) would be second tool
   - Can evaluate Svelte when adding more tools

3. **No build complexity yet**
   - HTML edits are instant (no build step)
   - Node.js not required for development
   - Simpler contributor experience

4. **Blueprint architecture already in place**
   - `src/wichy/server.py` - Central Flask app
   - `src/wichy/tools/graph/__init__.py` - Blueprint pattern
   - Adding new tools is straightforward

### When to revisit:

Consider Svelte migration when:
- Adding 2nd or 3rd web tool that would benefit from shared components
- Need shared navigation/layout across tools
- Want TypeScript type safety for frontend
- Build process (Makefile) is in place for other reasons

## Implementation Notes (for future)

If we proceed with Svelte:

1. **Directory structure:**
   ```
   src/wichy/frontend/
   ├── src/
   │   ├── routes/
   │   │   ├── graph/+page.svelte
   │   │   └── layout.svelte
   │   └── lib/
   │       └── GraphEditor.svelte
   ├── dist/          # built output (vendored into wheel)
   ├── package.json
   └── vite.config.ts
   ```

2. **Build process:**
   - Makefile target: `npm ci && npm run build`
   - Output goes to `dist/`
   - `MANIFEST.in` includes `frontend/dist/**`

3. **Flask integration:**
   - Blueprint serves `index.html` for SPA fallback
   - API routes remain under `/tools/graph/api/*`

4. **Current HTML location:**
   - `src/wichy/graph/templates/editor.html`
   - `src/wichy/graph/static/vis-network.min.js`
   - `src/wichy/graph/static/vis-network.min.css`

## Related Files

- Design doc: `/Users/wilhelm/Library/Mobile Documents/.../2026-03-12.md`
- Server: `src/wichy/server.py`
- Graph blueprint: `src/wichy/tools/graph/__init__.py`
- HTML template: `src/wichy/graph/templates/editor.html`