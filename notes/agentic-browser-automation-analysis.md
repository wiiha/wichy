# Agentic Browser Automation: Research & Wichy Enhancement Proposal

**Date:** 2026-03-24
**Author:** Rook
**Status:** Analysis Complete, Recommendations Pending

---

## Executive Summary

This document combines research on modern browser automation tools (Playwright and alternatives) with a detailed analysis of the wichy framework's current browser capabilities. It proposes concrete enhancements to make wichy's browser tooling more effective for AI agents navigating JavaScript-heavy websites.

**Key Finding:** The current `browser_raw` tool is too low-level for reliable agent use. Agents benefit from _pre-parsed, semantic representations_ of page structure rather than raw HTML or arbitrary Playwright code execution.

---

## Part 1: Research Summary - Browser Automation Landscape (2025-2026)

### Is Playwright the Best?

Playwright is excellent for **cross-browser testing** and **dynamic JavaScript sites**, but not universally "best." Choice depends on use case:

| Strength                      | Recommended Tool                    |
| ----------------------------- | ----------------------------------- |
| Cross-browser, modern JS apps | **Playwright**                      |
| Legacy browser support        | **Selenium**                        |
| Chrome-only, maximum speed    | **Puppeteer**                       |
| No-code/low-code testing      | **Cypress / TestGrid / Katalon**    |
| AI-powered selectors          | **AgentQL** (works with Playwright) |

### Playwright vs Selenium (2025)

- **Speed:** Playwright 2-3x faster for typical workflows (WebSocket vs HTTP)
- **Reliability:** Playwright auto-waits; Selenium requires explicit waits
- **Setup:** Playwright bundles browsers; Selenium needs driver management
- **Languages:** Selenium supports more (Java, Python, C#, Ruby, JS); Playwright (JS/TS, Python, Java, .NET)
- **Use case:** Selenium still dominates enterprise with legacy systems; Playwright preferred for new greenfield projects

### Playwright vs Puppeteer

- **Browser support:** Playwright = Chromium/Firefox/WebKit; Puppeteer = Chromium-only
- **Language:** Both primarily JavaScript; Playwright has official Python/.NET/Java bindings
- **Performance:** Puppeteer slightly faster for simple tasks (less abstraction)
- **Features:** Playwright has built-in test runner, tracing, video; Puppeteer needs external libs
- **Recommendation:** Use Puppeteer for Chrome-only scraping/PDFs; Playwright for anything multi-browser

### Agentic Web Navigation: Key Patterns

**What emerged from multiple sources (Playwright MCP, autonomous scraping articles, AgentQL):**

1. **Don't feed agents raw HTML** - token limits, noise, poor reasoning
2. **Pre-parse page into semantic structure** - headings, links, buttons, forms, tables with _natural language labels_
3. **Use AI to generate selectors, not to parse HTML** - let LLM decide _what_ to extract; use deterministic code to _extract_ it
4. **Layered approach:**
   - Layer 1: Navigate & render (Playwright)
   - Layer 2: Summarize page structure (custom code or LLM)
   - Layer 3: Agent decides next action (LLM reasoning)
   - Layer 4: Execute (safe Playwright wrappers)
5. **Self-healing selectors** - tools like AgentQL use AI to locate elements even when DOM changes
6. **State isolation via browser contexts** - multiple sessions in one browser without cookies bleeding

### Commercial & Emerging Tools

| Tool               | Purpose                                       | Notes                                                                         |
| ------------------ | --------------------------------------------- | ----------------------------------------------------------------------------- |
| **Playwright MCP** | Model Context Protocol server for AI agents   | Bridges Playwright to LLMs; intelligent navigation, dynamic test generation   |
| **AgentQL**        | AI-powered query language for HTML            | Works _with_ Playwright; replaces CSS selectors with natural language queries |
| **Testomat.io**    | AI-enhanced test management                   | Not a browser engine, but layer on top                                        |
| **Browserless**    | Cloud-hosted Playwright/Selenium              | Scaling, stealth, proxy rotation                                              |
| **ZenRows**        | Scraping infrastructure with anti-bot evasion | Uses Playwright under the hood                                                |

---

## Part 2: Wichy Browser Tools - Current State

### Existing Tools (fetch_webpage.py)

1. **`web_fetch`** - Navigate + return page as markdown
   - Supports `limit` (default 20000 chars)
   - If limit exceeded, returns _headings overview_ + truncated content
   - Uses `markdownify` to convert HTML → markdown
2. **`browser_navigate`** - Navigate to URL (no content return)

3. **`browser_status`** - Return current URL + title

4. **`browser_screenshot`** - Save PNG or return base64

5. **`browser_raw`** - Execute arbitrary Playwright Page expressions
   - Examples: `title()`, `.url`, `query_selector('h1').text_content()`
   - Returns string representation of result
   - Error-prone: bad syntax, null values, timing issues

### The Problem: Agents Struggle with `browser_raw`

**Why it fails in practice:**

- Requires the LLM to know Playwright's API surface (which methods exist, return types)
- Forces the agent to _invent selectors_ without seeing page structure first
- No feedback loop: agent must guess `query_selector` vs `query_selector_all`, handle None checks
- Easy to write invalid Python code → runtime errors
- No semantic understanding: "click the submit button" becomes `page.query_selector('button[type="submit"]').click()`, but what if button is `<input type="submit">` or has no type?
- Multi-step operations require chaining multiple `browser_raw` calls, each brittle

**Observed failure patterns:**

- Agent tries `query_selector('button')` and gets ElementHandle, then tries to return it directly (should call `.text_content()` or `.click()`)
- Agent doesn't wait for dynamic content → elements not found
- Agent misremembers attribute names (e.g., `class_name` vs `get_attribute('class')`)
- Agent generates code that works in one context but not another (inconsistent mental model)

---

## Part 3: Proposed Enhancements for Agentic Use

### Philosophy

**Goal:** Make browser tools _declare what you want, not how to do it_.

Current: Agent must write Playwright code (imperative, fragile)
Desired: Agent says "fill the email field" and tool figures out how (declarative, robust)

### Proposal 1: Add `browser_summarize` (Highest Impact)

**Purpose:** Give agent a structured, semantic overview of the current page without raw HTML noise.

**Parameters:** None (or optional filters like `include=['links','forms']`)

**Returns:** JSON-like structure:

```json
{
  "title": "Login - Example.com",
  "url": "https://example.com/login",
  "headings": [
    { "level": 1, "text": "Sign In" },
    { "level": 2, "text": "New users" }
  ],
  "links": [
    { "text": "Forgot password?", "href": "/reset", "visible": true },
    { "text": "Create account", "href": "/signup", "visible": true }
  ],
  "buttons": [
    { "text": "Log in", "type": "submit", "visible": true, "id": "login-btn" },
    { "text": "Cancel", "type": "button", "visible": true }
  ],
  "inputs": [
    {
      "name": "email",
      "type": "email",
      "placeholder": "you@example.com",
      "required": true
    },
    {
      "name": "password",
      "type": "password",
      "placeholder": "Password",
      "required": true
    }
  ],
  "regions": [
    { "id": "main", "classes": ["content"], "purpose": "login form" },
    { "id": "footer", "purpose": "site links" }
  ],
  "text_blocks": [
    { "type": "paragraph", "content": "Enter your credentials..." },
    { "type": "list", "items": ["SSO available", "2FA enabled"] }
  ]
}
```

**Benefits:**

- Agent sees _what_ is on page, not raw HTML
- Can reason about: "Is there a login form? Yes, inputs: email, password. Submit button present."
- Enables planning: "I need to fill inputs, then click the login button"
- Token-efficient: structured data < raw HTML

**Implementation:**

- Use Playwright to query all elements (`page.query_selector_all('*')`)
- Filter to visible, interactive elements + text content
- Extract natural labels (button text, input placeholders, aria-labels)
- Group by semantic categories
- Return as formatted JSON string

---

### Proposal 2: Add Action-Specific Tools (Replace `browser_raw` for Common Tasks)

Instead of arbitrary code, provide purpose-built tools:

#### `browser_click_text(text, exact=False, index=0)`

- Finds element(s) containing `text` (exact match if `exact=True`)
- Clicks the `index`-th match (0-based)
- Auto-waits for element to be visible/clickable
- Example: `browser_click_text("Accept cookies")`

#### `browser_fill_input(selector, value, clear=True)`

- `selector` can be: input `name`, `id`, `placeholder`, or `label text`
- Finds `<input>` or `<textarea>` matching selector
- Optionally clears existing text, then types `value`
- Example: `browser_fill_input("email", "user@example.com")`

#### `browser_extract_table(index_or_selector)`

- Finds a `<table>` element (by index among tables or by CSS selector)
- Extracts headers + rows as structured list of dicts
- Returns JSON:

```json
{
  "headers": ["Name", "Price"],
  "rows": [{"Name": "Widget", "Price": "$10"}, ...]
}
```

#### `browser_scroll_to(selector_or_text)`

- Scrolls element into view
- Accepts CSS selector or visible text match
- Returns viewport info after scroll

#### `browser_wait_for(selector, state='visible', timeout=5000)`

- Waits for element matching `selector` to reach `state` ('visible', 'attached', 'enabled')
- Returns success/failure

#### `browser_get_visible_elements(category=None)`

- Returns simplified list of currently visible interactive elements
- Categories: `links`, `buttons`, `inputs`, `regions`
- Each entry: `{text, selector, element_type}`

---

### Proposal 3: Add `browser_query` - Structured Query Language (Advanced)

Inspired by AgentQL, but simpler. Agent writes:

```
{
  search_box(input[type="search"]) => value="",
  add_to_cart_buttons(button:contains("Add to cart")) => click,
  product_items[.product] => [
    title = h3,
    price = .price,
    link = a.learn-more
  ]
}
```

The tool:

- Parses this DSL
- Executes queries against current page
- Performs actions (fill, click) or extracts data
- Returns structured results

**Why not use `browser_raw` for this?** Because `browser_query`:

- Enforces safety (no arbitrary Python)
- Provides consistent return types
- Handles timing/awaiting internally
- More LLM-friendly syntax (closer to natural language)

---

### Proposal 4: Optional AI Summarization in `web_fetch`

Add flag: `web_fetch(url, summarize=False, limit=20000)`

When `summarize=True`:

1. Fetch full HTML (no markdown conversion yet)
2. Send _just the body_ to LLM with prompt:
   "Summarize this page for a browsing agent. Extract: (1) page purpose, (2) main interactive elements (forms, buttons, links), (3) suggested next actions, (4) any authentication/paywall hints. Keep under 500 words."
3. Return LLM's summary + truncated markdown fallback

**Trade-off:** Requires LLM call (cost/latency), but dramatically improves agent understanding.

---

## Part 4: Migration Strategy

### Phase 1 (Immediate - High ROI)

1. Implement `browser_summarize`
2. Implement `browser_click_text` and `browser_fill_input`
3. Update default agent root description to _prefer_ these tools over `browser_raw`
4. Document new tools in README

### Phase 2 (Medium-Term)

5. Implement `browser_extract_table`
6. Add `browser_get_visible_elements` for quick context
7. Enhance `web_fetch` with optional `summarize=True` (configurable)

### Phase 3 (Long-Term)

8. Design and implement `browser_query` DSL
9. Deprecate `browser_raw` for agent workflows (keep for advanced users)
10. Integrate memory: `browser_save_state` / `browser_restore_state` for multi-step workflows

### Backward Compatibility

- All new tools are _additive_
- Existing `browser_raw` remains unchanged
- No breaking changes to current APIs

---

## Part 5: Key Learnings & Takeaways

### Technical Insights

1. **The "raw query" problem is real** - Agents cannot reliably write low-level browser automation code without extensive prompting and even then it's brittle.
2. **Semantic over syntactic** - Providing a _description_ of the page (headings, buttons, inputs) is vastly more useful than raw HTML or markdown.
3. **Action granularity matters** - Tools should match _agent intentions_ ("click button", "fill form") not programming primitives ("query_selector", "click").
4. **State awareness is critical** - Agents need to know _what's visible now_ vs _what exists on page_. Tools should filter by visibility by default.
5. **Auto-waiting must be built-in** - Every tool should handle async rendering gracefully; agents shouldn't need to insert waits manually.

### Architectural Insights

6. **Layered design works:**
   - Bottom: Playwright (reliable browser control)
   - Middle: Semantic adapters (`browser_summarize`, `browser_click_text`)
   - Top: LLM agent (reasoning, planning)
7. **Token efficiency** - Sending full page content to LLM is wasteful. Pre-filter with structured summaries.
8. **Error recovery** - Tools should return clear error messages ("element not found", "not visible") not Python tracebacks.
9. **Multi-page workflows** need state persistence (cookies, localStorage) - browser contexts help but explicit state save/restore is user-friendly.

### Research Validate Patterns

- **Playwright MCP** proves the concept: AI agents + Playwright works when you add a semantic layer
- **AgentQL** shows that AI-powered selectors are viable and robust to DOM changes
- **Autonomous scraping articles** consistently recommend: LLM decides _what_, script decides _how_ - not the other way around

### For Wichy Specifically

- The current tool set is _engineer-focused_ (direct Playwright access) not _agent-focused_ (declarative actions)
- Missing: high-level navigation tools, page understanding, error-safe interactions
- Opportunity: wichy's local-first, modular design is perfect for layering these enhancements without rewriting core

---

## Part 6: Implementation Recommendations

### Start with `browser_summarize`

**Reason:** It's the foundation. Once agent knows page structure, all other decisions improve.

**Effort:** ~200 lines of Python using Playwright's DOM queries.

**Impact:** Enables smarter tool selection, reduces failures in subsequent steps.

### Follow with `browser_click_text` and `browser_fill_input`

**Reason:** Two most common actions. Making them robust (auto-wait, clear errors) prevents 80% of agent failures.

**Effort:** ~100 lines each.

### Test with Real Agent Workflows

Examples to validate:

- Login to a site (detect form, fill, submit)
- Scrape a table (detect table, extract)
- Navigate pagination (find "Next" link, click, repeat)

---

## Appendix: Tool Specification Drafts

### Tool: `browser_summarize`

```python
class BrowserSummarizeParameters(ParametersModel):
    pass

class BrowserSummarizeTool(BaseTool):
    name = "browser_summarize"
    description = "Return a structured summary of the current page: headings, links, buttons, inputs, text blocks."
    parameters_model = BrowserSummarizeParameters

    def execute(self) -> str:
        # Use Playwright to query all relevant elements
        # Build dict with keys: title, url, headings, links, buttons, inputs, regions, text_blocks
        # Return as pretty-printed JSON
        pass
```

### Tool: `browser_click_text`

```python
class BrowserClickTextParameters(ParametersModel):
    text: str = Field(..., description="Exact or partial text of element to click")
    exact: bool = Field(False, description="Require exact text match")
    index: int = Field(0, description="Which match to click if multiple (0-based)")
    wait_for: str = Field("navigation", description="Wait for 'navigation', 'load', or 'none'")

class BrowserClickTextTool(BaseTool):
    name = "browser_click_text"
    description = "Click an element containing the given text. Auto-waits for element to be visible."
    # Implementation: page.get_by_text(text, exact=exact).nth(index).click()
```

---

**Report Location:** `notes/agentic-browser-automation-analysis.md`
