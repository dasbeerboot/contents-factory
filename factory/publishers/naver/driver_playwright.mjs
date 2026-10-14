// Playwright backend for the Naver publisher, for hosts without the aside browser.
//
// aside is macOS only ("Aside supports macOS 15.0 or later", docs.aside.com), and Naver
// has no write API, so a Windows or Linux host needs its own logged-in browser. This
// drives Chromium through a *persistent context*: one user-data directory under the
// runtime home that keeps the Naver session between runs, created once by
// `factory naver login` and reused by every publish after it.
//
// The editor manipulation itself is the same code as the aside path: the main-world
// script strings are built by publish.mjs and handed in here, so SmartEditor is driven
// through one implementation whichever backend opened the page. What differs is the
// publish panel, because aside drives it through accessibility-tree refs and Playwright
// drives it through role locators.
//
// Nothing here logs a cookie, a session value or a page body.
import { execFile } from "node:child_process";
import { mkdir, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const LOGIN_URL = "https://nid.naver.com/nidlogin.login";
export const SESSION_COOKIES = ["NID_AUT", "NID_SES"];
export const LOGIN_WAIT_MS = 10 * 60 * 1000;
export const STEP_TIMEOUT_MS = 35_000;
export const NAVIGATION_TIMEOUT_MS = 30_000;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * Resolves the playwright module, which is deliberately not a dependency of this repo.
 *
 * A bare `import("playwright")` only finds a *local* install: ESM ignores NODE_PATH, so
 * the `npm install -g playwright` the docs ask for would never be found. The lookup
 * therefore walks, in order: the explicit override, a local resolution, and the global
 * npm root. A directory override is turned into its entry file, because importing a
 * directory is an ESM error (`ERR_UNSUPPORTED_DIR_IMPORT`).
 */
async function globalNpmRoot() {
  // npm is `npm.cmd` on Windows and execFile cannot start a .cmd shim directly.
  const binary = process.platform === "win32" ? "npm.cmd" : "npm";
  return new Promise((resolve) => {
    execFile(binary, ["root", "-g"], { timeout: 20_000 }, (error, stdout) => {
      resolve(error ? "" : String(stdout ?? "").trim());
    });
  });
}

async function entryFile(target) {
  try {
    const info = await stat(target);
    if (!info.isDirectory()) return target;
  } catch {
    return target;
  }
  const require = createRequire(pathToFileURL(path.join(target, "package.json")));
  try {
    return require.resolve(target);
  } catch {
    return path.join(target, "index.js");
  }
}

export async function playwrightCandidates() {
  const override = String(process.env.FACTORY_PLAYWRIGHT_MODULE ?? "").trim();
  if (override) return [await entryFile(override)];
  const names = ["playwright", "playwright-core"];
  const found = [...names];
  const require = createRequire(import.meta.url);
  for (const name of names) {
    try {
      found.push(require.resolve(name));
    } catch { /* not resolvable from here */ }
  }
  const root = await globalNpmRoot();
  if (root) {
    for (const name of names) {
      found.push(await entryFile(path.join(root, name)));
    }
  }
  return found;
}

export async function loadPlaywright() {
  let loadError = null;
  for (const candidate of await playwrightCandidates()) {
    try {
      const target = path.isAbsolute(candidate) ? pathToFileURL(candidate).href : candidate;
      const module = await import(target);
      if (module?.chromium) return module;
    } catch (error) {
      const code = String(error?.code ?? "");
      // Not found is "try the next candidate"; anything else is a broken install
      // and must not be reported as missing.
      if (!["ERR_MODULE_NOT_FOUND", "MODULE_NOT_FOUND",
            "ERR_UNSUPPORTED_DIR_IMPORT"].includes(code)) {
        loadError = error;
      }
    }
  }
  fail(loadError ? "PLAYWRIGHT_LOAD_FAILED" : "PLAYWRIGHT_MISSING");
}

export async function openContext({ userDataDir, headless = false }) {
  if (!userDataDir) fail("NAVER_PROFILE_MISSING");
  const { chromium } = await loadPlaywright();
  try {
    // The Naver session cookie store lives in here, so the directory is as sensitive
    // as credentials.env: owner only.
    await mkdir(userDataDir, { recursive: true, mode: 0o700 });
  } catch {
    fail("NAVER_PROFILE_MISSING");
  }
  try {
    return await chromium.launchPersistentContext(userDataDir, {
      headless,
      viewport: null,
      locale: "ko-KR",
      timezoneId: "Asia/Seoul",
      args: ["--disable-blink-features=AutomationControlled"],
    });
  } catch (error) {
    // "the browser binary is not installed" is a different user action from "the
    // launch failed", so they are different codes. Installing the package does not
    // download Chromium; `npx playwright install chromium` does.
    const message = String(error?.message ?? "");
    fail(/executable doesn'?t exist|playwright install/i.test(message)
      ? "PLAYWRIGHT_BROWSER_MISSING" : "PLAYWRIGHT_LAUNCH_FAILED");
  }
}

export async function hasSession(context) {
  let cookies = [];
  try {
    cookies = await context.cookies("https://blog.naver.com");
  } catch {
    return false;
  }
  const names = new Set(cookies.filter((cookie) => String(cookie.value ?? "").trim())
    .map((cookie) => cookie.name));
  return SESSION_COOKIES.some((name) => names.has(name));
}

/**
 * Opens a window on the Naver login page and waits for the user to finish. Returns
 * once the session cookie exists, so the caller can say "logged in" truthfully.
 * The browser is left open for the user to close unless `closeWhenDone` is set.
 */
export async function openLoginWindow({ userDataDir, waitMs = LOGIN_WAIT_MS,
                                        closeWhenDone = true, url = LOGIN_URL }) {
  const context = await openContext({ userDataDir, headless: false });
  try {
    const page = context.pages()[0] ?? await context.newPage();
    try {
      await page.goto(url, { waitUntil: "domcontentloaded",
                             timeout: NAVIGATION_TIMEOUT_MS });
    } catch { /* the user can navigate; cookie polling below decides */ }
    const deadline = Date.now() + waitMs;
    let closed = false;
    while (Date.now() < deadline) {
      if (await hasSession(context)) {
        return { logged_in: true, user_data_dir: userDataDir };
      }
      if (context.pages().length === 0) {
        closed = true;
        break;
      }
      await delay(1_000);
    }
    const logged_in = await hasSession(context);
    return { logged_in, user_data_dir: userDataDir,
             ...(logged_in ? {} : closed ? { closed: true } : { timed_out: true }) };
  } finally {
    if (closeWhenDone) await context.close().catch(() => {});
  }
}

export async function sessionState({ userDataDir }) {
  const context = await openContext({ userDataDir, headless: true });
  try {
    return { logged_in: await hasSession(context), user_data_dir: userDataDir };
  } finally {
    await context.close().catch(() => {});
  }
}

function isEditorUrl(url, blogId) {
  const text = String(url ?? "").toLowerCase();
  const handle = String(blogId ?? "").toLowerCase();
  if (!text.includes("blog.naver.com")) return false;
  return (text.includes(`/${handle}`) && (text.includes("postwrite")
          || text.includes("redirect=write")))
    || (text.includes("postwrite") && text.includes(`blogid=${handle}`));
}

async function editorFrame(page) {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const frame = page.frames().find((candidate) => String(candidate.url())
      .toLowerCase().includes("postwrite"));
    if (frame) return frame;
    await delay(100);
  }
  fail("EDITOR_FRAME_TIMEOUT");
}

/**
 * Runs one main-world script in the editor frame and returns its envelope value.
 * Same contract as the aside path: the script sets a result attribute on <html>,
 * because the editor's SmartEditor object is not reachable from an isolated world.
 */
async function runMain(frame, code, timeoutMs = STEP_TIMEOUT_MS) {
  const attribute = `data-factory-main-${Math.random().toString(36).slice(2)}`;
  const invocation = code.replaceAll("__FACTORY_MAIN_RESULT_ATTRIBUTE__", attribute);
  const raw = await frame.evaluate(async ({ mainCode, timeout, resultAttribute }) => {
    document.documentElement.removeAttribute(resultAttribute);
    const script = document.createElement("script");
    script.textContent = mainCode;
    document.documentElement.appendChild(script);
    const attempts = Math.ceil(timeout / 100);
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const value = document.documentElement.getAttribute(resultAttribute);
      if (value) {
        document.documentElement.removeAttribute(resultAttribute);
        script.remove();
        return value;
      }
    }
    script.remove();
    return "TIMEOUT";
  }, { mainCode: invocation, timeout: timeoutMs, resultAttribute: attribute });
  if (raw === "TIMEOUT") fail("MAIN_WORLD_TIMEOUT");
  let envelope;
  try {
    envelope = JSON.parse(raw);
  } catch {
    fail("MAIN_WORLD_RESULT_INVALID");
  }
  if (!envelope?.ok) fail(envelope?.error || "MAIN_WORLD_FAILED");
  return envelope.result;
}

/**
 * The reservation date sits in the control's accessible name ("2026. 10. 20"), not in
 * its value, which is what the aside path matched on too. `inputValue()` is the
 * fallback for a build where the name is empty.
 */
async function dateLabelOf(locator) {
  try {
    const snapshot = String(await locator.ariaSnapshot());
    const found = /"(\d{4}\D+\d{2}\D+\d{2})"/.exec(snapshot);
    if (found) return found[1].trim();
  } catch { /* older playwright, fall through */ }
  return String(await locator.inputValue().catch(() => "")).trim();
}

/**
 * The day button inside the calendar, not the first "15" on the page.
 *
 * The calendar renders leading days of the previous month with the same names, and the
 * panel has other numeric buttons, so the cells are taken in DOM order after the
 * "다음달" control and counted from the first "1" — the same cut the aside path makes
 * on its accessibility tree.
 */
async function dayCell(frame, day) {
  const buttons = frame.getByRole("button");
  const total = await buttons.count();
  const names = [];
  for (let index = 0; index < total; index += 1) {
    names.push(String(await buttons.nth(index).textContent().catch(() => "")).trim());
  }
  const calendarStart = names.findIndex((name) => name === "다음달");
  if (calendarStart < 0) fail("RESERVATION_CALENDAR_MISSING");
  const cells = names.slice(calendarStart);
  const firstOfMonth = cells.findIndex((name) => name === "1");
  if (firstOfMonth < 0) fail("RESERVATION_CALENDAR_INVALID");
  const offset = cells.slice(firstOfMonth)
    .findIndex((name) => /^\d{1,2}$/.test(name) && Number(name) === day);
  if (offset < 0) fail("RESERVATION_DAY_MISSING");
  return buttons.nth(calendarStart + firstOfMonth + offset);
}

/** Entry point for `factory naver login|status`: open a window, or just look. */
export async function runPlaywrightLogin({ userDataDir, status = false, waitMs = 0 }) {
  if (status) return sessionState({ userDataDir });
  return openLoginWindow({ userDataDir,
                           waitMs: waitMs > 0 ? waitMs : LOGIN_WAIT_MS });
}

export async function createPlaywrightDriver({ blogId, userDataDir, editorUrl,
                                               headless = false }) {
  const context = await openContext({ userDataDir, headless });
  if (!await hasSession(context)) {
    await context.close().catch(() => {});
    fail("NAVER_SESSION_MISSING");
  }
  let page = null;
  let frame = null;

  const open = async () => {
    if (page && !page.isClosed()) return page;
    const matches = context.pages().filter((candidate) =>
      isEditorUrl(candidate.url(), blogId));
    if (matches.length > 1) fail("MULTIPLE_EDITOR_TABS");
    page = matches[0] ?? await context.newPage();
    if (!isEditorUrl(page.url(), blogId)) {
      try {
        await page.goto(editorUrl, { waitUntil: "domcontentloaded",
                                     timeout: NAVIGATION_TIMEOUT_MS });
      } catch { /* frame polling decides readiness */ }
    }
    return page;
  };

  return {
    backend: "playwright",

    async ensureEditor(readyCode) {
      await open();
      // A draft prompt blocks the editor; the aside path cancels it the same way.
      for (const scope of [page, ...page.frames()]) {
        try {
          const resume = scope.getByText("이어서 작성", { exact: false }).first();
          if (await resume.count() && await resume.isVisible().catch(() => false)) {
            const cancel = scope.getByRole("button", { name: "취소", exact: true }).first();
            if (!await cancel.count()) fail("RESUME_CANCEL_MISSING");
            await cancel.click();
            break;
          }
        } catch { /* a detached frame is not the draft prompt */ }
      }
      frame = await editorFrame(page);
      return runMain(frame, readyCode);
    },

    async injectDocument(code) {
      frame = await editorFrame(page);
      return runMain(frame, code);
    },

    async validateEditor(code, expected) {
      frame = await editorFrame(page);
      const model = await runMain(frame, code);
      const types = new Set([...Object.keys(model.counts ?? {}),
                             ...Object.keys(expected ?? {})]);
      for (const type of types) {
        if ((model.counts ?? {})[type] !== (expected ?? {})[type]) {
          fail("COMPONENT_COUNT_MISMATCH");
        }
      }
      return { counts: model.counts };
    },

    // Role locators rather than accessibility refs: same element names the aside
    // snapshot matched on, reached without a tree dump.
    async publishPanel({ category, tags, publishAt, title = "" }) {
      let publishClicked = false;
      let phase = "OPEN_PANEL";
      try {
        frame = await editorFrame(page);
        const openButton = frame.getByRole("button", { name: "발행", exact: true }).first();
        if (!await openButton.count()) fail("PUBLISH_BUTTON_MISSING");
        await openButton.click();

        phase = "CATEGORY";
        const categoryButton = frame.getByRole("button", { name: "카테고리 목록 버튼" }).first();
        await categoryButton.waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS })
          .catch(() => fail("PUBLISH_PANEL_MISSING"));
        const current = String(await categoryButton.textContent().catch(() => "")).trim();
        if (current !== category) {
          await categoryButton.click();
          await delay(800);
          const option = frame.getByText(category, { exact: true }).first();
          if (!await option.count()) fail("CATEGORY_OPTION_MISSING");
          await option.click();
          await delay(800);
          const applied = String(await categoryButton.textContent().catch(() => "")).trim();
          if (applied !== category) fail("CATEGORY_NOT_APPLIED");
        }

        phase = "TAGS";
        const tagInput = frame.getByRole("combobox", { name: /태그 입력/ }).first();
        if (!await tagInput.count()) fail("TAG_INPUT_MISSING");
        for (const tag of [...new Set(tags)]) {
          await tagInput.fill(tag);
          await tagInput.press("Enter");
          await delay(150);
        }
        for (const tag of [...new Set(tags)]) {
          if (!await frame.getByText(`#${tag}`, { exact: false }).count()) {
            fail("TAG_NOT_APPLIED");
          }
        }

        phase = "RESERVE";
        const reserve = frame.getByText("예약", { exact: true }).first();
        if (!await reserve.count()) fail("RESERVATION_OPTION_MISSING");
        await reserve.click();
        const dateBox = frame.getByRole("textbox", { name: /\d{4}\. \d{2}\. \d{2}/ }).first();
        await dateBox.waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS })
          .catch(() => fail("RESERVATION_DATE_MISSING"));
        const shown = await dateLabelOf(dateBox);
        if (shown !== publishAt.dateLabel) {
          const parts = /^(\d{4})\D+(\d{2})\D+(\d{2})$/.exec(shown);
          if (!parts) fail("RESERVATION_DATE_UNREADABLE");
          const currentIndex = Number(parts[1]) * 12 + Number(parts[2]);
          const targetIndex = Number(publishAt.year) * 12 + Number(publishAt.month);
          if (targetIndex < currentIndex) fail("RESERVATION_DATE_PAST_MONTH");
          if (targetIndex - currentIndex > 3) fail("RESERVATION_DATE_TOO_FAR");
          await dateBox.click();
          const nextMonth = frame.getByRole("button", { name: "다음달" }).first();
          await nextMonth.waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS })
            .catch(() => fail("RESERVATION_CALENDAR_MISSING"));
          for (let step = 0; step < targetIndex - currentIndex; step += 1) {
            await nextMonth.click();
            await delay(400);
          }
          const day = await dayCell(frame, Number(publishAt.day));
          await day.click();
          await delay(500);
          if (await dateLabelOf(dateBox) !== publishAt.dateLabel) {
            fail("RESERVATION_DATE_NOT_APPLIED");
          }
        }

        phase = "TIME";
        const combos = frame.getByRole("combobox");
        const total = await combos.count();
        const timeBoxes = [];
        for (let index = 0; index < total; index += 1) {
          const option = combos.nth(index);
          const value = String(await option.inputValue().catch(() => ""));
          if (/^\d{2}$/.test(value)) timeBoxes.push(option);
        }
        if (timeBoxes.length < 2) fail("RESERVATION_TIME_MISSING");
        await timeBoxes[0].selectOption(publishAt.hour);
        await timeBoxes[1].selectOption(publishAt.minute);
        await delay(300);

        phase = "PUBLISH";
        const confirms = frame.getByRole("button", { name: "발행", exact: true });
        const count = await confirms.count();
        if (count < 2) fail("PANEL_PUBLISH_BUTTON_MISSING");
        publishClicked = true;
        await confirms.nth(count - 1).click();

        let afterWrite = null;
        for (let attempt = 0; attempt < 120; attempt += 1) {
          await delay(500);
          let urls = [];
          try {
            urls = [page.url(), ...page.frames().map((candidate) => candidate.url())];
          } catch { urls = []; }
          afterWrite = urls.find((url) => /isAfterWrite/i.test(url)
            && /[?&]logNo=\d+/i.test(url)) || null;
          if (afterWrite) break;
        }
        if (!afterWrite) fail("PUBLISH_REDIRECT_TIMEOUT");
        return {
          logNo: /[?&]logNo=(\d+)/i.exec(afterWrite)[1],
          reservedStamp: `${publishAt.year}.${publishAt.month}.${publishAt.day} `
            + `${publishAt.hour}:${publishAt.minute}`,
          titleProbe: String(title).replace(/\s+/g, " ").trim().slice(0, 24),
          afterWriteUrl: afterWrite,
        };
      } catch (error) {
        const message = (error && typeof error.message === "string") ? error.message : "";
        if (!publishClicked) {
          fail(/^[A-Z][A-Z0-9_]*$/.test(message) ? message : `PUBLISH_NOT_SENT:${phase}`);
        }
        fail(`PUBLISH_OUTCOME_UNCERTAIN:${phase}`);
      }
    },

    async close() {
      await context.close().catch(() => {});
    },
  };
}
