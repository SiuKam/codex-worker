## Browser preflight (mandatory; added by codex_task.py --browser)

This task uses the user's own browser (with the user's logins) through the Codex Chrome extension. Act in it only as the task requires.

The usual `cua_repl` route fails outside the ChatGPT desktop app ("The browser access host is unavailable", openai/codex#57641). Use the `node_repl` tool's `js` function instead, never a shell command, Playwright-launched Chromium, curl or python requests, unless the task explicitly allows that.

Before any other browser step:
1. Connect once in `node_repl` (`process` is not defined there; do not use it):
   ```js
   const { homedir } = await import("node:os");
   const { join } = await import("node:path");
   const { pathToFileURL } = await import("node:url");
   const { setupBrowserRuntime } = await import(pathToFileURL(
     join(homedir(), ".codex", "plugins", "cache", "openai-bundled", "chrome", "latest", "scripts", "browser-client.mjs")).href);
   const agent = await setupBrowserRuntime();
   const all = (await agent.browsers.list()).filter((b) => b.type === "extension");
   ```
   Pick the browser the task names; otherwise the single entry whose name contains "Chrome". If there is no match, or more than one (several profiles), reply `BROWSER_PREFLIGHT: FAIL <reason>` with the names and profiles you saw, and stop. Do not switch to another browser.
2. `const browser = await agent.browsers.get(<chosen id>);` then read `await browser.documentation()` and use only the methods it describes. Reuse `agent` and `browser` for the rest of the run.
3. Open exactly one new tab for this run, then send an agent message whose first line is exactly `BROWSER_PREFLIGHT: OK browser=<name> profile=<profileName>`. Any failure: `BROWSER_PREFLIGHT: FAIL <reason>` and stop.

While working:
- Use only the tab you opened; do not read, change or close the user's other tabs.
- Read the page again before each click; element numbers change with each state.
- Never enter passwords or other credentials, solve CAPTCHAs, make purchases, send messages or posts, accept new terms, or change account settings. If a page needs any of these, record it and move on (or stop if the task cannot continue).
- Never edit plugin files, authorization checks or `BROWSER_USE_ACCESS_*` variables.
- Close the tab you opened as the last action.
