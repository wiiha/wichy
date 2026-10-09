# Browser tests (Playwright)

Real-browser coverage for the web UIs, replacing the old Python tests that
asserted the *text* of `templates/` and `static/` files. These run the actual
page in Chromium against the real Flask app.

## Running

```bash
make test-e2e
```

This is opt-in: the default `make test` deselects the `e2e` marker (see
`addopts` in `pyproject.toml`), so the fast suite stays fast. Run everything with:

```bash
make test && make test-e2e
```

Requires the Playwright chromium build. It is cached in this container at
`/home/wichy/.cache/ms-playwright`; on a fresh machine run
`python -m playwright install chromium`.

## How the harness works

`conftest.py` boots the app once per session with
`wichy.server.create_app(no_chat=True, mode="repl")`, served by
`werkzeug.serving.make_server` on an ephemeral port. The app's blueprints are
module-level and can register only once per process, which is why the server
fixture is session-scoped.

Isolation uses the working directory: the note, graph, and log directories are
resolved relative to CWD, so the fixture `chdir`s into a temp directory and each
test clears `.wichy/notes` and `.wichy/graphs` first. The original CWD is
restored on teardown. No test touches the live agent on port 7891.

## Coverage

- `test_landing.py` -- tool cards link to every GUI; the status pill renders
  Running / REPL active / Stopped from the real `/health` contract.
- `test_notes.py` -- EditorJS initializes, a note opens with a live block, the
  vendored editor globals exist, pinning sets the scratchpad, the toolbar
  enables on open, the review/history surfaces are served, and a hostile title
  renders as literal text (XSS guard).
