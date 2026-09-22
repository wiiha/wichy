/**
 * Custom Editor.js block tools for the notes editor.
 *
 * Three block types that have no Editor.js plugin: `question`, `decision` and
 * `todo`. Each is a semantic marker rather than a formatting choice, so they
 * get their own classes rather than being encoded as a paragraph with a prefix.
 *
 * Each class follows the contract Editor.js requires:
 *  - `static get isReadOnlySupported()` -- without it, initialising read-only throws.
 *  - `static get toolbox()` -- the icon and title shown in the `/` picker.
 *  - `static get sanitize()` -- REQUIRED for a custom block to be cleaned at all;
 *    a block with no sanitize getter is left untouched.
 *  - `render()` returns the root element, `save(el)` returns the data.
 *
 * The sanitize getters here are a defence-in-depth measure only. The real trust
 * boundary is the server: every block's data is validated against a strict
 * per-type schema before it is stored, so anything that slips past these
 * declarations is rejected on the way in rather than persisted.
 *
 * Loaded after the plugin bundles, since it extends nothing but must run after
 * Editor.js itself is defined (it registers via the `toolbox` getters the editor
 * reads at init).
 */
(function () {
    "use strict";

    /** Shared markup: a wrapper, an editable area, and an optional footer. */
    function buildRoot(className, placeholder) {
        const wrapper = document.createElement("div");
        wrapper.classList.add("cdx-block", className);
        const editable = document.createElement("div");
        editable.classList.add(className + "__text");
        editable.contentEditable = "true";
        if (placeholder) {
            editable.dataset.placeholder = placeholder;
        }
        wrapper.appendChild(editable);
        return { wrapper: wrapper, editable: editable };
    }

    /**
     * Read the text out of an editable area, normalising what a user may paste.
     * `innerText` rather than `innerHTML`, so pasted markup becomes text instead
     * of being stored as HTML.
     */
    function readText(element) {
        if (!element) {
            return "";
        }
        return (element.innerText || element.textContent || "").trim();
    }

    class Question {
        static get isReadOnlySupported() {
            return true;
        }

        static get sanitize() {
            // Declared, so this block IS cleaned. `answered` is a boolean we own,
            // and `text` is plain text: no tags are allowed through.
            return { text: false, answered: false };
        }

        static get toolbox() {
            return {
                title: "Question",
                icon:
                    '<svg width="17" height="15" viewBox="0 0 17 15" xmlns="http://www.w3.org/2000/svg">' +
                    '<path d="M8.5 1C4.36 1 1 3.46 1 6.5c0 1.6.86 3.03 2.23 4.02V13l2.4-1.3c.88.25 1.85.38 2.87.38 4.14 0 7.5-2.46 7.5-5.5S12.64 1 8.5 1z" ' +
                    'fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
            };
        }

        constructor({ data, readOnly }) {
            this.data = {
                text: (data && data.text) || "",
                answered: Boolean(data && data.answered),
            };
            this.readOnly = readOnly;
        }

        render() {
            const built = buildRoot("cdx-question", "Ask a question...");
            this.wrapper = built.wrapper;
            this.editable = built.editable;
            this.editable.innerText = this.data.text;

            // A question is a semantic marker, not a request for a reply. The
            // label says so, and `answered` records that the agent has dealt
            // with it -- it does not prompt anyone.
            const badge = document.createElement("span");
            badge.classList.add("cdx-question__badge");
            badge.textContent = this.data.answered ? "[QUESTION answered]" : "[QUESTION]";
            this.wrapper.insertBefore(badge, this.editable);

            if (this.readOnly) {
                this.editable.contentEditable = "false";
            }
            return this.wrapper;
        }

        save() {
            return { text: readText(this.editable), answered: this.data.answered };
        }
    }

    class Decision {
        static get isReadOnlySupported() {
            return true;
        }

        static get sanitize() {
            return { text: false };
        }

        static get toolbox() {
            return {
                title: "Decision",
                icon:
                    '<svg width="17" height="15" viewBox="0 0 17 15" xmlns="http://www.w3.org/2000/svg">' +
                    '<path d="M2 3.5h13M2 7.5h9M2 11.5h13" stroke="currentColor" stroke-width="1.6" fill="none"/>' +
                    '<circle cx="14" cy="7.5" r="2" fill="currentColor"/></svg>',
            };
        }

        constructor({ data, readOnly }) {
            this.data = { text: (data && data.text) || "" };
            this.readOnly = readOnly;
        }

        render() {
            const built = buildRoot("cdx-decision", "Record a decision...");
            this.wrapper = built.wrapper;
            this.editable = built.editable;
            this.editable.innerText = this.data.text;

            const badge = document.createElement("span");
            badge.classList.add("cdx-decision__badge");
            badge.textContent = "[DECISION]";
            this.wrapper.insertBefore(badge, this.editable);

            if (this.readOnly) {
                this.editable.contentEditable = "false";
            }
            return this.wrapper;
        }

        save() {
            return { text: readText(this.editable) };
        }
    }

    class Todo {
        static get isReadOnlySupported() {
            return true;
        }

        static get sanitize() {
            return { text: false, checked: false };
        }

        static get toolbox() {
            return {
                title: "Todo",
                icon:
                    '<svg width="17" height="15" viewBox="0 0 17 15" xmlns="http://www.w3.org/2000/svg">' +
                    '<rect x="2" y="4" width="10" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
                    '<path d="M4.5 8.5l2 2 4-4.5" stroke="currentColor" stroke-width="1.6" fill="none"/></svg>',
            };
        }

        constructor({ data, readOnly }) {
            this.data = {
                text: (data && data.text) || "",
                checked: Boolean(data && data.checked),
            };
            this.readOnly = readOnly;
        }

        render() {
            const built = buildRoot("cdx-todo", "What needs doing?");
            this.wrapper = built.wrapper;
            this.editable = built.editable;
            this.editable.innerText = this.data.text;

            // The checkbox is a real control, not decoration: `checked` is a
            // field the agent reads and writes.
            const box = document.createElement("input");
            box.type = "checkbox";
            box.classList.add("cdx-todo__box");
            box.checked = this.data.checked;
            box.setAttribute("aria-label", "Done");
            box.disabled = Boolean(this.readOnly);
            box.addEventListener("change", () => {
                this.data.checked = box.checked;
                label.textContent = box.checked ? "[TODO checked]" : "[TODO unchecked]";
                // Tell the editor something changed, so the save debounce starts.
                if (typeof this.api?.blocks?.blockDidMutated === "function") {
                    this.api.blocks.blockDidMutated(box);
                }
            });

            // A visible text label, like the question and decision badges. A bare
            // checkbox conveys "todo" by shape alone, which is not legible in
            // monochrome and says nothing to a screen reader.
            const label = document.createElement("span");
            label.classList.add("cdx-todo__badge");
            label.textContent = this.data.checked ? "[TODO checked]" : "[TODO unchecked]";

            this.wrapper.insertBefore(label, this.editable);
            this.wrapper.insertBefore(box, label);

            if (this.readOnly) {
                this.editable.contentEditable = "false";
            }
            return this.wrapper;
        }

        save() {
            return { text: readText(this.editable), checked: this.data.checked };
        }
    }

    // Exposed so the page can pass them to Editor.js as its `tools`.
    window.WichyCustomBlocks = { Question: Question, Decision: Decision, Todo: Todo };
})();
