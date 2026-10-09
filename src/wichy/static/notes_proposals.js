/**
 * Proposal review for the notes page.
 *
 * The agent's edits arrive as proposals: they are not part of the document until
 * the user accepts one. This module fetches them for the open note, shows a count
 * on the toolbar control, and renders a sheet where each proposal can be accepted
 * or rejected.
 *
 * It owns no document state: the open slug arrives on `wichy:note-opened` and the
 * version comes from the proposals response itself, so the list and the version it
 * posts are always the same read.
 */
(function () {
    "use strict";

    const PREFIX = "/tools/notes";
    const POLL_MS = 3000;

    let slug = null;
    let version = null;
    let proposals = [];
    let enabled = true;
    let epoch = 0;
    let timer = null;
    let open = false;
    let lastFocus = null;

    function byId(id) {
        return document.getElementById(id);
    }

    function setSheetStatus(message) {
        const node = byId("proposal-status");
        if (node) node.textContent = message || "";
    }

    function updateToggle(busy) {
        const button = byId("btn-proposals-mode");
        if (!button) return;
        button.textContent = enabled ? "Proposals: On" : "Proposals: Off";
        button.setAttribute("aria-pressed", enabled ? "true" : "false");
        button.disabled = busy || !slug;
    }

    function updateCount() {
        const button = byId("btn-proposals");
        if (!button) return;
        const count = proposals.length;
        button.disabled = count === 0;
        button.textContent = count ? "Review Proposals (" + count + ")" : "Review Proposals";
    }

    async function fetchJson(url, options) {
        const response = await fetch(url, options || { credentials: "same-origin" });
        let body = {};
        try {
            body = await response.json();
        } catch (error) {
            body = {};
        }
        return { ok: response.ok, status: response.status, body: body };
    }

    function describe(proposal) {
        const payload = proposal.payload || {};
        if (proposal.kind === "insert") {
            return "Add a new " + (payload.type || "block") + ": \u201c" + (payload.text || "") + "\u201d";
        }
        if (proposal.kind === "delete") return "Delete a block";
        if (proposal.kind === "move") return "Move a block";
        if (proposal.kind === "change_type") {
            return "Change a block into a " + (payload.type || "different type");
        }
        if ("answer" in payload) return "Mark a question as answered";
        return "Replace a block's text with: \u201c" + (payload.text || "") + "\u201d";
    }

    function renderSheet() {
        const list = byId("proposal-list");
        const empty = byId("proposal-empty");
        if (!list) return;
        list.innerHTML = "";
        if (empty) {
            empty.classList.toggle("hidden", proposals.length > 0);
            empty.textContent = "No proposed changes.";
        }
        proposals.forEach(function (proposal) {
            const summary = describe(proposal);
            const item = document.createElement("li");
            item.className = "proposal-item";

            const text = document.createElement("p");
            text.className = "proposal-text";
            text.textContent = summary;
            item.appendChild(text);

            const actions = document.createElement("div");
            actions.className = "proposal-actions";

            const accept = document.createElement("button");
            accept.className = "btn btn-primary";
            accept.type = "button";
            accept.textContent = "Accept";
            accept.setAttribute("aria-label", "Accept: " + summary);
            accept.addEventListener("click", function () {
                resolve("accept", proposal.id, summary);
            });
            actions.appendChild(accept);

            const reject = document.createElement("button");
            reject.className = "btn btn-secondary";
            reject.type = "button";
            reject.textContent = "Reject";
            reject.setAttribute("aria-label", "Reject: " + summary);
            reject.addEventListener("click", function () {
                resolve("reject", proposal.id, summary);
            });
            actions.appendChild(reject);

            item.appendChild(actions);
            list.appendChild(item);
        });
    }

    async function resolve(action, proposalId, summary) {
        if (!slug) return;
        const mine = epoch;
        setSheetStatus("Working\u2026");
        const result = await fetchJson(
            PREFIX +
                "/api/notes/" +
                encodeURIComponent(slug) +
                "/proposals/" +
                encodeURIComponent(proposalId) +
                "/" +
                action,
            {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: version }),
            }
        );
        if (mine !== epoch) return;
        if (!result.ok) {
            setSheetStatus(
                result.status === 409
                    ? "The note changed since this was proposed. Reviewing again\u2026"
                    : result.body.error || "Could not " + action + " the change."
            );
            // A 409 means our version is stale: refresh so the next click can work.
            await refresh();
            return;
        }
        proposals = result.body.proposals || [];
        version = result.body.version;
        updateCount();
        renderSheet();
        setSheetStatus(
            action === "accept"
                ? "Accepted: " + summary
                : "Rejected: " + summary
        );
        if (proposals.length === 0 && action === "accept") {
            setSheetStatus("Accepted: " + summary);
        }
    }

    async function refresh() {
        if (!slug) return;
        const mine = epoch;
        const list = await fetchJson(
            PREFIX + "/api/notes/" + encodeURIComponent(slug) + "/proposals"
        );
        if (mine !== epoch) return;
        if (!list.ok) {
            if (open) setSheetStatus("Could not load proposed changes.");
            return;
        }
        proposals = list.body.proposals || [];
        version = list.body.version;
        updateCount();
        if (open) renderSheet();
    }

    async function refreshMode() {
        if (!slug) return;
        const mine = epoch;
        const info = await fetchJson(
            PREFIX + "/api/notes/" + encodeURIComponent(slug)
        );
        if (mine !== epoch || !info.ok) return;
        const meta = info.body.meta || {};
        enabled = meta.proposals_enabled !== false;
        updateToggle(false);
    }

    function openSheet() {
        const sheet = byId("proposal-sheet");
        if (!sheet || !slug) return;
        lastFocus = document.activeElement;
        open = true;
        renderSheet();
        setSheetStatus("");
        sheet.classList.remove("hidden");
        const close = byId("proposal-close");
        if (close) close.focus();
    }

    function closeSheet() {
        const sheet = byId("proposal-sheet");
        if (!sheet) return;
        open = false;
        sheet.classList.add("hidden");
        if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    async function setMode(next) {
        if (!slug) return;
        updateToggle(true);
        const result = await fetchJson(
            PREFIX + "/api/notes/" + encodeURIComponent(slug) + "/proposals-mode",
            {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: next, version: version }),
            }
        );
        if (result.ok) {
            enabled = Boolean(result.body.enabled);
            if (open) setSheetStatus("");
        } else if (result.status === 409) {
            setSheetStatus(
                "This note is in markdown format; proposal mode is unavailable."
            );
        } else {
            setSheetStatus(result.body.error || "Could not change proposal mode.");
        }
        updateToggle(false);
    }

    function onNoteOpened(event) {
        slug = event.detail && event.detail.slug ? event.detail.slug : null;
        epoch += 1;
        proposals = [];
        version = null;
        updateCount();
        updateToggle(true);
        if (slug) {
            refreshMode();
            refresh();
        }
    }

    function init() {
        const review = byId("btn-proposals");
        if (review) review.addEventListener("click", openSheet);
        const close = byId("proposal-close");
        if (close) close.addEventListener("click", closeSheet);
        const mode = byId("btn-proposals-mode");
        if (mode) {
            mode.disabled = true;
            mode.addEventListener("click", function () {
                setMode(!enabled);
            });
        }
        const sheet = byId("proposal-sheet");
        if (sheet) {
            sheet.addEventListener("click", function (event) {
                if (event.target === sheet) closeSheet();
            });
        }
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && open) closeSheet();
        });
        document.addEventListener("wichy:note-opened", onNoteOpened);
        timer = setInterval(function () {
            if (slug) refresh();
        }, POLL_MS);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
