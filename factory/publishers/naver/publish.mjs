// Vendored and generalized from pilot/scripts/naver/publish.mjs (2026-10-14).
// Naver has no public write API, so publishing drives the logged in editor through the
// aside browser. Removed from the original: telegram review gating, checkride ledger,
// per-post locks, screenshot review and image upload. Kept: tab discovery, main world
// script injection, ownership marking, document injection, component validation and the
// reservation panel flow (category, tags, reserve, publish, logNo recovery).
//
// usage: node publish.mjs --job <job.json> [--dry-run]
// job.json: { blogId, title, category, tags[], publishAt: "YYYY-MM-DDTHH:MM" (KST),
//             markdown | markdownPath, dryRun? }
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

import { parseMarkdown } from "./md_to_document.mjs";
import { componentCounts } from "./document.mjs";
import { runAsideJson } from "./aside.mjs";
import { startCorsServer } from "./cors_server.mjs";

const RESULT_MARKER = "__FACTORY_NAVER_RESULT__";
const MAIN_RESULT_ATTRIBUTE_TOKEN = "__FACTORY_MAIN_RESULT_ATTRIBUTE__";
const OWNER_KEY = "__factoryNaverOwner";
const MAX_PROGRAM_BYTES = 90 * 1024;
// The document never travels through the aside program: it is served over loopback and
// fetched by the editor tab, so post length is bounded by the browser, not by the REPL.
const MAX_DOCUMENT_BYTES = 8 * 1024 * 1024;
const FACTORY_HOME = process.env.FACTORY_HOME
  ? path.resolve(process.env.FACTORY_HOME.replace(/^~(?=$|\/)/, homedir()))
  : path.join(homedir(), ".contents-factory");
const BUILD_ROOT = path.join(FACTORY_HOME, "state", "naver", "build");

let runContext = null;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function errorCode(error, fallback = "UNEXPECTED") {
  const candidate = typeof error?.code === "string"
    ? error.code
    : typeof error?.message === "string" ? error.message : "";
  return /^[A-Z][A-Z0-9_:-]{0,119}$/.test(candidate) ? candidate : fallback;
}

export function parseArgs(argv) {
  const options = { job: "", dry: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--dry-run" || argument === "--dry") {
      options.dry = true;
    } else if (argument === "--job") {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) fail("JOB_VALUE_REQUIRED");
      options.job = value;
      index += 1;
    } else {
      fail("UNKNOWN_OPTION");
    }
  }
  if (!options.job) fail("JOB_VALUE_REQUIRED");
  return options;
}

export function parsePublishAt(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value ?? "");
  if (!match) fail("PUBLISH_AT_INVALID");
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const stamp = Date.UTC(year, month - 1, day, hour, minute);
  const normalized = new Date(stamp);
  if (
    normalized.getUTCFullYear() !== year
    || normalized.getUTCMonth() !== month - 1
    || normalized.getUTCDate() !== day
    || normalized.getUTCHours() !== hour
    || normalized.getUTCMinutes() !== minute
  ) {
    fail("PUBLISH_AT_INVALID");
  }
  if (minute % 10 !== 0) fail("PUBLISH_MINUTE_NOT_TEN");
  return {
    raw: value,
    epochMs: stamp - (9 * 60 * 60 * 1000),
    year: String(year).padStart(4, "0"),
    month: String(month).padStart(2, "0"),
    day: String(day).padStart(2, "0"),
    hour: String(hour).padStart(2, "0"),
    minute: String(minute).padStart(2, "0"),
    dateLabel: `${String(year).padStart(4, "0")}. ${String(month).padStart(2, "0")}. ${String(day).padStart(2, "0")}`,
  };
}

export function validateJob(job) {
  if (!job || typeof job !== "object") fail("JOB_INVALID");
  const blogId = String(job.blogId ?? "").trim();
  if (!/^[A-Za-z0-9_-]{3,40}$/.test(blogId)) fail("BLOG_ID_INVALID");
  const title = String(job.title ?? "").trim();
  if (!title || title.length > 100) fail("TITLE_INVALID");
  const category = String(job.category ?? "").trim();
  if (!category) fail("CATEGORY_VALUE_REQUIRED");
  const tags = [...new Set((Array.isArray(job.tags) ? job.tags : []).map((tag) => String(tag).trim()).filter(Boolean))];
  if (tags.length === 0 || tags.length > 30 || tags.some((tag) => tag.includes(","))) fail("TAGS_INVALID");
  return { blogId, title, category, tags, publishAt: parsePublishAt(job.publishAt) };
}

export function editorUrl(blogId) {
  return `https://blog.naver.com/${blogId}?Redirect=Write&`;
}

function browserHelpers(blogId) {
  return String.raw`
const __delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
const __tabUrl = (tab) => String(tab?.url ?? tab?.pageUrl ?? "");
const __blogId = ${JSON.stringify(blogId.toLowerCase())};
const __isEditorTab = (tab) => {
  const url = __tabUrl(tab).toLowerCase();
  return url.includes("blog.naver.com") && (
    (url.includes("/" + __blogId) && (url.includes("postwrite") || url.includes("redirect=write")))
    || (url.includes("postwrite") && url.includes("blogid=" + __blogId))
  );
};
const __tabIdOf = (tab) => tab?.targetId ?? tab?.tabId ?? tab?.providerTabId ?? String(tab?.id ?? "").replace(/^tab:/, "");
async function __editorContext(allowOpen = false, claim = false) {
  const listed = await listBrowserTabs();
  const tabs = Array.isArray(listed) ? listed : (listed?.tabs ?? []);
  const matches = tabs.filter(__isEditorTab);
  if (matches.length > 1) throw new Error("MULTIPLE_EDITOR_TABS");
  let editorPage;
  if (matches.length === 1) {
    const tabId = __tabIdOf(matches[0]);
    if (!tabId) throw new Error("EDITOR_TAB_ID_MISSING");
    editorPage = await attachBrowserTab(tabId);
  } else if (allowOpen) {
    // openTab 으로 연 탭은 REPL 프로세스가 끝나면 닫힌다 — 사용자 소유 탭을 붙잡아 goto 한다.
    const host = tabs.find((tab) => /^https:\/\/blog\.naver\.com\//i.test(__tabUrl(tab)))
      || tabs.find((tab) => /naver\.com/i.test(__tabUrl(tab)))
      || tabs[0];
    if (!host) throw new Error("HOST_TAB_MISSING");
    const hostId = __tabIdOf(host);
    if (!hostId) throw new Error("HOST_TAB_ID_MISSING");
    editorPage = await attachBrowserTab(hostId);
    try {
      await editorPage.goto(${JSON.stringify(editorUrl(blogId))}, { waitUntil: "domcontentloaded", timeout: 20000 });
    } catch { /* frame polling below decides readiness */ }
  } else {
    throw new Error("EDITOR_TAB_MISSING");
  }
  let editorFrame = null;
  for (let attempt = 0; attempt < 300; attempt += 1) {
    editorFrame = editorPage.frames().find((candidate) => candidate.url().toLowerCase().includes("postwrite"));
    if (editorFrame) break;
    await __delay(100);
  }
  if (!editorFrame) throw new Error("EDITOR_FRAME_TIMEOUT");
  if (__runToken) {
    let state = null;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      try {
        editorFrame = editorPage.frames().find((candidate) => candidate.url().toLowerCase().includes("postwrite")) || editorFrame;
        state = await editorFrame.evaluate((key) => {
          let prior = null;
          try { prior = JSON.parse(sessionStorage.getItem(key) || "null"); } catch { /* invalid */ }
          return { prior, documentId: document.documentElement.getAttribute("data-factory-document-id") };
        }, ${JSON.stringify(OWNER_KEY)});
        break;
      } catch (error) {
        if (attempt === 5) throw error;
        await __delay(1500);
      }
    }
    const prior = state.prior;
    if (claim) {
      let currentUrl = "";
      try { currentUrl = String(editorPage.url()) + " " + String(editorFrame.url()); } catch { /* checked below */ }
      const existingPost = /[?&]logNo=/i.test(currentUrl) || /redirect=update/i.test(currentUrl);
      if (existingPost && prior?.run !== __runToken) throw new Error("EDITOR_EXISTING_POST");
    } else if (
      prior?.token !== __runToken
      || (prior?.documentId && state.documentId !== prior.documentId)
    ) {
      throw new Error("EDITOR_OWNERSHIP_LOST");
    }
  }
  return { page: editorPage, frame: editorFrame };
}
async function __runMain(frame, code, timeoutMilliseconds = 35000) {
  const attribute = "data-factory-main-" + __asideCallToken;
  const invocationCode = code.replaceAll(${JSON.stringify(MAIN_RESULT_ATTRIBUTE_TOKEN)}, attribute);
  const raw = await frame.evaluate(async ({ mainCode, timeout, resultAttribute }) => {
    document.documentElement.removeAttribute(resultAttribute);
    const script = document.createElement("script");
    script.textContent = mainCode;
    document.documentElement.appendChild(script);
    const attempts = Math.ceil(timeout / 100);
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
      const value = document.documentElement.getAttribute(resultAttribute);
      if (value) {
        document.documentElement.removeAttribute(resultAttribute);
        script.remove();
        return value;
      }
    }
    script.remove();
    return "TIMEOUT";
  }, { mainCode: invocationCode, timeout: timeoutMilliseconds, resultAttribute: attribute });
  if (raw === "TIMEOUT") throw new Error("MAIN_WORLD_TIMEOUT");
  let envelope;
  try { envelope = JSON.parse(raw); } catch { throw new Error("MAIN_WORLD_RESULT_INVALID"); }
  if (!envelope?.ok) throw new Error(envelope?.error || "MAIN_WORLD_FAILED");
  return envelope.result;
}
`;
}

export function mainWorldCode(body) {
  return `void (async () => {
    try {
      const result = await (async () => { ${body} })();
      document.documentElement.setAttribute(${JSON.stringify(MAIN_RESULT_ATTRIBUTE_TOKEN)}, JSON.stringify({ ok: true, result }));
    } catch (error) {
      const candidate = error instanceof Error ? error.message : "MAIN_WORLD_FAILED";
      const safe = /^[A-Z][A-Z0-9_:-]{0,119}$/.test(candidate) ? candidate : "MAIN_WORLD_FAILED";
      document.documentElement.setAttribute(${JSON.stringify(MAIN_RESULT_ATTRIBUTE_TOKEN)}, JSON.stringify({ ok: false, error: safe }));
    }
  })();`;
}

export function asideProgram(body, context = runContext) {
  return `
const __runToken = ${JSON.stringify(context?.token ?? "")};
const __asideCallToken = ${JSON.stringify(randomUUID().replaceAll("-", ""))};
${browserHelpers(context?.blogId ?? "")}
try {
  const result = await (async () => { ${body} })();
  return { ok: true, result };
} catch (error) {
  const candidate = (error && typeof error.message === "string" && error.message) ? error.message : String(error ?? "ASIDE_STEP_FAILED");
  const safe = /^[A-Z][A-Z0-9_:-]{0,119}$/.test(candidate) ? candidate : "ASIDE_STEP_FAILED";
  return { ok: false, error: safe };
}`;
}

async function runAsideStep(body) {
  if (!runContext) fail("ASIDE_RUN_CONTEXT_MISSING");
  const program = asideProgram(body);
  if (Buffer.byteLength(program, "utf8") > MAX_PROGRAM_BYTES) fail("DOCUMENT_TOO_LARGE");
  const envelope = await runAsideJson(program);
  if (!envelope?.ok) fail(errorCode({ code: envelope?.error }, "ASIDE_STEP_FAILED"));
  return envelope.result;
}

async function ensureEditor() {
  const readyCode = mainWorldCode(String.raw`
    let editorReady = false;
    for (let attempt = 0; attempt < 150; attempt += 1) {
      const editor = window.SmartEditor?._editors?.blogpc001;
      // API 객체만 있고 렌더가 끝나기 전에 setDocumentData 하면 내부에서 죽는다.
      const rendered = document.querySelectorAll(".se-component").length > 0;
      if (editor?.getDocumentData && editor?._documentService?.setDocumentData && rendered) {
        editorReady = true;
        const documentModel = editor.getDocumentData();
        const components = documentModel?.document?.components || [];
        if (components.length === 0) { await new Promise((r) => setTimeout(r, 100)); continue; }
        const hasParagraphText = (value) => (value?.nodes || []).some((node) => String(node?.value ?? "").trim().length > 0);
        const hasContent = components.some((component) => {
          if (component?.["@ctype"] === "documentTitle") return component.title?.some(hasParagraphText);
          if (component?.["@ctype"] === "text") return component.value?.some(hasParagraphText);
          return !["documentTitle", "text"].includes(component?.["@ctype"]);
        });
        const existingPost = /[?&]logNo=/i.test(location.href) || /redirect=update/i.test(location.href);
        if (!hasContent && !existingPost) {
          await new Promise((r) => setTimeout(r, 1000));
          sessionStorage.setItem(OWNER_KEY_TOKEN, JSON.stringify({ token: RUN_TOKEN_VALUE, run: RUN_TOKEN_VALUE }));
          return { ready: true };
        }
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error(editorReady ? "EDITOR_NOT_EMPTY" : "EDITOR_API_TIMEOUT");
  `).replaceAll("OWNER_KEY_TOKEN", JSON.stringify(OWNER_KEY))
    .replaceAll("RUN_TOKEN_VALUE", JSON.stringify(runContext.token));
  return runAsideStep(`
    const { page, frame } = await __editorContext(true, true);
    for (const context of [page, ...page.frames()]) {
      const resumeText = context.getByText("이어서 작성", { exact: false }).first();
      if (await resumeText.count() && await resumeText.isVisible().catch(() => false)) {
        const cancel = context.getByRole("button", { name: "취소", exact: true }).first();
        if (!await cancel.count()) throw new Error("RESUME_CANCEL_MISSING");
        await cancel.click();
        break;
      }
    }
    return await __runMain(frame, ${JSON.stringify(readyCode)}, 35000);
  `);
}

async function injectDocument(bundleUrl) {
  const code = mainWorldCode(String.raw`
    const response = await fetch(BUNDLE_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("BUILD_FETCH_FAILED");
    const bundle = await response.json();
    const editor = window.SmartEditor?._editors?.blogpc001;
    if (!editor?._documentService?.setDocumentData) throw new Error("EDITOR_API_MISSING");
    // 현재 문서의 id·di 는 유지하고 components 만 교체한다 (빌드본 id 주입은 에디터 스토어를 깨뜨린다).
    const documentModel = editor.getDocumentData();
    documentModel.document.components = structuredClone(bundle.document.components);
    const expectedCount = documentModel.document.components.length;
    let applyError = null;
    try { await editor._documentService.setDocumentData(documentModel); } catch (error) { applyError = error; }
    const after = editor.getDocumentData();
    const applied = after?.document?.components?.length;
    if (applied !== expectedCount) throw new Error("INJECT_NOT_APPLIED");
    document.documentElement.setAttribute("data-factory-document-id", String(after.document.id));
    sessionStorage.setItem(OWNER_KEY_TOKEN, JSON.stringify({ token: RUN_TOKEN_VALUE, run: RUN_TOKEN_VALUE, documentId: String(after.document.id) }));
    return { componentCount: applied };
  `).replace("BUNDLE_URL", JSON.stringify(bundleUrl))
    .replaceAll("OWNER_KEY_TOKEN", JSON.stringify(OWNER_KEY))
    .replaceAll("RUN_TOKEN_VALUE", JSON.stringify(runContext.token));
  return runAsideStep(`
    const { frame } = await __editorContext(false);
    return await __runMain(frame, ${JSON.stringify(code)});
  `);
}

async function validateEditor(expectedCounts) {
  const code = mainWorldCode(String.raw`
    const editor = window.SmartEditor?._editors?.blogpc001;
    if (!editor?.getDocumentData) throw new Error("EDITOR_API_MISSING");
    const documentModel = editor.getDocumentData();
    const counts = {};
    for (const component of documentModel.document.components) {
      const type = component["@ctype"];
      counts[type] = (counts[type] || 0) + 1;
    }
    return { counts };
  `);
  return runAsideStep(`
    const { frame } = await __editorContext(false);
    const model = await __runMain(frame, ${JSON.stringify(code)});
    const expected = ${JSON.stringify(expectedCounts)};
    const types = new Set([...Object.keys(model.counts), ...Object.keys(expected)]);
    if ([...types].some((type) => model.counts[type] !== expected[type])) throw new Error("COMPONENT_COUNT_MISMATCH");
    return { counts: model.counts };
  `);
}

export function publishPanelProgram({ category, tags, publishAt, title = "" }) {
  const uniqueTags = [...new Set(tags)];
  const reservedStamp = `${publishAt.year}.${publishAt.month}.${publishAt.day} ${publishAt.hour}:${publishAt.minute}`;
  const titleProbe = String(title).replace(/\s+/g, " ").trim().slice(0, 24);
  return `
    let publishClicked = false;
    let phase = "OPEN_PANEL";
    try {
    const { page, frame } = await __editorContext(false);
    const escapeRegex = (value) => String(value).replace(/[.*+?^\${}()|[\\]\\\\]/g, "\\\\$&");
    const snap = async () => String((await snapshot(page, { interactive: true })).tree);
    const refOf = (tree, regex) => { const match = regex.exec(tree); return match ? match[1] : null; };
    const waitTree = async (regex, attempts = 30) => {
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        const tree = await snap();
        if (regex.test(tree)) return tree;
        await __delay(500);
      }
      return null;
    };

    let tree = await snap();
    const openRef = refOf(tree, /button "발행" \\[ref=([^\\]]+)\\]/);
    if (!openRef) throw new Error("PUBLISH_BUTTON_MISSING");
    await page.locator(openRef).click();
    tree = await waitTree(/button "카테고리 목록 버튼"/);
    if (!tree) throw new Error("PUBLISH_PANEL_MISSING");

    phase = "CATEGORY";
    const categoryPattern = /button "카테고리 목록 버튼" \\[ref=([^\\]]+)\\]: "([^"]*)"/;
    let categoryMatch = categoryPattern.exec(tree);
    if (!categoryMatch) throw new Error("CATEGORY_BUTTON_MISSING");
    if (categoryMatch[2].trim() !== ${JSON.stringify(category)}) {
      await page.locator(categoryMatch[1]).click();
      await __delay(800);
      tree = await snap();
      const optionRegex = new RegExp('(?:radio|label|button|option|menuitem|link) "' + escapeRegex(${JSON.stringify(category)}) + '" \\\\[ref=([^\\\\]]+)\\\\]', "g");
      const optionRefs = [...tree.matchAll(optionRegex)].map((match) => match[1]);
      if (optionRefs.length === 0) throw new Error("CATEGORY_OPTION_MISSING");
      await page.locator(optionRefs[0]).click();
      await __delay(800);
      tree = await snap();
      categoryMatch = categoryPattern.exec(tree);
      if (!categoryMatch || categoryMatch[2].trim() !== ${JSON.stringify(category)}) throw new Error("CATEGORY_NOT_APPLIED");
    }

    phase = "TAGS";
    const tagRef = refOf(tree, /combobox "태그 입력 \\(최대 30개\\)" \\[ref=([^\\]]+)\\]/);
    if (!tagRef) throw new Error("TAG_INPUT_MISSING");
    for (const tag of ${JSON.stringify(uniqueTags)}) {
      const tagInput = page.locator(tagRef);
      await tagInput.fill(tag);
      await tagInput.press("Enter");
      await __delay(150);
    }
    tree = await snap();
    for (const tag of ${JSON.stringify(uniqueTags)}) {
      if (!tree.includes("#" + tag)) throw new Error("TAG_NOT_APPLIED");
    }

    phase = "RESERVE";
    const reserveRef = refOf(tree, /label "예약" \\[ref=([^\\]]+)\\]/);
    if (!reserveRef) throw new Error("RESERVATION_OPTION_MISSING");
    await page.locator(reserveRef).click();
    tree = await waitTree(/textbox "\\d{4}\\. \\d{2}\\. \\d{2}" \\[ref=/, 10);
    if (!tree) throw new Error("RESERVATION_DATE_MISSING");
    const datePattern = /textbox "(\\d{4})\\. (\\d{2})\\. (\\d{2})" \\[ref=([^\\]]+)\\]/;
    let dateMatch = datePattern.exec(tree);
    const targetLabel = ${JSON.stringify(publishAt.dateLabel)};
    if (dateMatch[1] + ". " + dateMatch[2] + ". " + dateMatch[3] !== targetLabel) {
      const currentIndex = Number(dateMatch[1]) * 12 + Number(dateMatch[2]);
      const targetIndex = ${Number(publishAt.year) * 12 + Number(publishAt.month)};
      if (targetIndex < currentIndex) throw new Error("RESERVATION_DATE_PAST_MONTH");
      if (targetIndex - currentIndex > 3) throw new Error("RESERVATION_DATE_TOO_FAR");
      await page.locator(dateMatch[4]).click();
      tree = await waitTree(/button "다음달" \\[ref=/, 10);
      if (!tree) throw new Error("RESERVATION_CALENDAR_MISSING");
      for (let step = 0; step < targetIndex - currentIndex; step += 1) {
        const nextRef = refOf(tree, /button "다음달" \\[ref=([^\\]]+)\\]/);
        await page.locator(nextRef).click();
        await __delay(400);
        tree = await snap();
      }
      const calendarStart = tree.indexOf('button "다음달"');
      const dayLines = [...tree.slice(calendarStart).matchAll(/button "(\\d{1,2})" \\[ref=([^\\]]+)\\]/g)];
      const firstOfMonth = dayLines.findIndex((match) => match[1] === "1");
      if (firstOfMonth < 0) throw new Error("RESERVATION_CALENDAR_INVALID");
      const dayMatch = dayLines.slice(firstOfMonth).find((match) => Number(match[1]) === ${Number(publishAt.day)});
      if (!dayMatch) throw new Error("RESERVATION_DAY_MISSING");
      await page.locator(dayMatch[2]).click();
      await __delay(500);
      tree = await snap();
      dateMatch = datePattern.exec(tree);
      if (!dateMatch || dateMatch[1] + ". " + dateMatch[2] + ". " + dateMatch[3] !== targetLabel) {
        throw new Error("RESERVATION_DATE_NOT_APPLIED");
      }
    }

    phase = "TIME";
    const afterDate = tree.slice(tree.indexOf(dateMatch[0]));
    const comboRefs = [...afterDate.matchAll(/combobox "(\\d{2})" \\[ref=([^\\]]+)\\]/g)].map((match) => match[2]);
    if (comboRefs.length < 2) throw new Error("RESERVATION_TIME_MISSING");
    await page.locator(comboRefs[0]).selectOption(${JSON.stringify(publishAt.hour)});
    await page.locator(comboRefs[1]).selectOption(${JSON.stringify(publishAt.minute)});
    await __delay(300);
    tree = await snap();

    phase = "PUBLISH";
    const publishRefs = [...tree.matchAll(/button "발행" \\[ref=([^\\]]+)\\]/g)].map((match) => match[1]);
    if (publishRefs.length < 2) throw new Error("PANEL_PUBLISH_BUTTON_MISSING");
    publishClicked = true;
    await page.locator(publishRefs[publishRefs.length - 1]).click();

    let afterWriteUrl = null;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await __delay(500);
      let urls = [];
      try { urls = [page.url(), ...page.frames().map((candidate) => candidate.url())]; } catch { urls = []; }
      afterWriteUrl = urls.find((url) => /isAfterWrite/i.test(url) && /[?&]logNo=\\d+/i.test(url)) || null;
      if (afterWriteUrl) break;
    }
    if (!afterWriteUrl) throw new Error("PUBLISH_REDIRECT_TIMEOUT");
    const logNo = /[?&]logNo=(\\d+)/i.exec(afterWriteUrl)[1];
    return { logNo, reservedStamp: ${JSON.stringify(reservedStamp)}, titleProbe: ${JSON.stringify(titleProbe)}, afterWriteUrl };
    } catch (error) {
      const message = (error && typeof error.message === "string") ? error.message : String(error);
      if (!publishClicked) throw new Error(/^[A-Z][A-Z0-9_]*$/.test(message) ? message : "PUBLISH_NOT_SENT:" + phase);
      throw new Error("PUBLISH_OUTCOME_UNCERTAIN:" + phase);
    }
  `;
}

async function loadMarkdown(job) {
  if (typeof job.markdown === "string" && job.markdown.trim()) return job.markdown;
  if (typeof job.markdownPath === "string" && job.markdownPath.trim()) {
    try {
      return await readFile(job.markdownPath, "utf8");
    } catch {
      fail("MARKDOWN_UNREADABLE");
    }
  }
  return fail("MARKDOWN_REQUIRED");
}

export async function main(argv) {
  const options = parseArgs(argv);
  let job;
  try {
    job = JSON.parse(await readFile(options.job, "utf8"));
  } catch {
    return { ok: false, code: "JOB_UNREADABLE" };
  }
  let spec;
  let documentModel;
  try {
    spec = validateJob(job);
    const markdown = await loadMarkdown(job);
    const parsed = parseMarkdown(markdown, { source: options.job });
    documentModel = parsed.documentModel ?? parsed;
    if (componentCounts(documentModel).image) fail("IMAGE_UPLOAD_UNSUPPORTED");
  } catch (error) {
    return { ok: false, code: errorCode(error, "JOB_INVALID") };
  }
  const counts = componentCounts(documentModel);
  const bundleBody = `${JSON.stringify(documentModel)}\n`;
  if (Buffer.byteLength(bundleBody, "utf8") > MAX_DOCUMENT_BYTES) {
    return { ok: false, code: "DOCUMENT_TOO_LARGE" };
  }

  if (options.dry || job.dryRun) {
    return {
      ok: true,
      code: "DRY_RUN",
      dry_run: true,
      plan: {
        editor_url: editorUrl(spec.blogId),
        title: spec.title,
        category: spec.category,
        tags: spec.tags,
        publish_at_kst: spec.publishAt.raw,
        publish_at_epoch_ms: spec.publishAt.epochMs,
        reserved_stamp: `${spec.publishAt.year}.${spec.publishAt.month}.${spec.publishAt.day} ${spec.publishAt.hour}:${spec.publishAt.minute}`,
        component_counts: counts,
        document_bytes: Buffer.byteLength(bundleBody, "utf8"),
        transport: "loopback_cors",
        steps: ["start_cors_server", "ensure_editor", "inject_document",
                "validate_editor", "publish_panel"],
      },
    };
  }

  runContext = { token: randomUUID().replaceAll("-", ""), blogId: spec.blogId };
  const bundleName = `${runContext.token}.json`;
  const bundlePath = path.join(BUILD_ROOT, bundleName);
  let cors = null;
  try {
    await mkdir(BUILD_ROOT, { recursive: true });
    await writeFile(bundlePath, bundleBody, { encoding: "utf8", mode: 0o600 });
    cors = await startCorsServer({ buildRoot: BUILD_ROOT });
    await ensureEditor();
    await injectDocument(`${cors.origin}/build/${bundleName}`);
    await validateEditor(counts);
    const result = await runAsideStep(publishPanelProgram({
      category: spec.category,
      tags: spec.tags,
      publishAt: spec.publishAt,
      title: spec.title,
    }));
    return { ok: true, code: "OK", log_no: result.logNo, reserved: result.reservedStamp };
  } catch (error) {
    const code = errorCode(error, "PUBLISH_FAILED");
    const sessionCodes = ["HOST_TAB_MISSING", "EDITOR_TAB_MISSING", "EDITOR_FRAME_TIMEOUT",
                          "ASIDE_UNAVAILABLE", "ASIDE_RESULT_MISSING", "ASIDE_TIMEOUT"];
    return { ok: false, code: sessionCodes.includes(code) ? "NAVER_SESSION_MISSING" : code };
  } finally {
    if (cors) await cors.close().catch(() => {});
    await rm(bundlePath, { force: true }).catch(() => {});
  }
}

const invokedDirectly = process.argv[1] && process.argv[1].endsWith("publish.mjs");
if (invokedDirectly) {
  main(process.argv.slice(2)).then((result) => {
    process.stdout.write(RESULT_MARKER + JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 1);
  }).catch((error) => {
    process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false, code: errorCode(error) }) + "\n");
    process.exit(1);
  });
}

export { RESULT_MARKER };
