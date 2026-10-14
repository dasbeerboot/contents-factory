import { access, mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  componentCounts,
  createDocumentModel,
  documentTitle,
  emptyParagraph,
  horizontalLine,
  oglink,
  paragraph,
  quotation,
  seId,
  table,
  text,
  textNode,
} from "./document.mjs";

const FACTORY_HOME = process.env.FACTORY_HOME
  ? path.resolve(process.env.FACTORY_HOME.replace(/^~(?=$|\/)/, homedir()))
  : path.join(homedir(), ".contents-factory");
const POSTS_ROOT = process.env.FACTORY_NAVER_POSTS_ROOT
  ? path.resolve(process.env.FACTORY_NAVER_POSTS_ROOT)
  : path.join(FACTORY_HOME, "outputs", "naver");
const BUILD_ROOT = path.join(FACTORY_HOME, "state", "naver", "build");

function parseError(source, code) {
  const error = new Error(`${code}:${source || "<markdown>"}`);
  error.code = code;
  return error;
}

function normalizePost(post) {
  const value = String(post ?? "").trim();
  if (!/^\d{1,2}$/.test(value)) throw new Error("POST_ID_INVALID");
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1 || number > 99) {
    throw new Error("POST_ID_INVALID");
  }
  return String(number).padStart(2, "0");
}

function stripInlineMarkers(value) {
  return value.replaceAll("`", "").replaceAll("**", "");
}

export function inlineNodes(value, { fontSizeCode = "fs15", forceBold = false } = {}) {
  if (typeof value !== "string") throw new TypeError("INLINE_VALUE_INVALID");
  const clean = value.replaceAll("`", "");
  const nodes = [];
  const boldPattern = /\*\*([\s\S]*?)\*\*/g;
  let cursor = 0;
  let match;

  const append = (part, bold) => {
    const normalized = part.replaceAll("**", "");
    if (normalized.length > 0) nodes.push(textNode(normalized, { bold: forceBold || bold, fontSizeCode }));
  };

  while ((match = boldPattern.exec(clean)) !== null) {
    append(clean.slice(cursor, match.index), false);
    append(match[1], true);
    cursor = match.index + match[0].length;
  }
  append(clean.slice(cursor), false);
  if (nodes.length === 0) nodes.push(textNode("", { fontSizeCode }));
  return nodes;
}

function isBlank(line) {
  return line.trim().length === 0;
}

function isEmptyParagraph(value) {
  return value?.nodes?.length === 1 && value.nodes[0]?.value === "";
}

function splitTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
  return trimmed.slice(1, -1).split("|").map((cell) => cell.trim());
}

function isTableDelimiter(line) {
  const cells = splitTableRow(line);
  return Boolean(cells?.length && cells.every((cell) => /^:?-{3,}:?$/.test(cell)));
}

function isStandaloneUrl(line) {
  const value = line.trim();
  if (!/^https?:\/\/\S+$/.test(value)) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function parseTags(lines, source) {
  const tags = [];
  const textValue = lines.join("\n");
  const pattern = /(?:^|\s)#([^\s#]+)/gu;
  let match;
  while ((match = pattern.exec(textValue)) !== null) tags.push(match[1]);
  if (tags.length === 0) throw parseError(source, "TAGS_MISSING");
  return tags;
}

function imagePlaceholder(line, source) {
  const match = /^\(이미지\s+([^:]+):\s*(img\/[^\s)]+)\s+—.*\)$/u.exec(line.trim());
  if (!match) {
    if (line.trim().startsWith("(이미지")) throw parseError(source, "IMAGE_MARKER_INVALID");
    return null;
  }
  const marker = match[1].trim();
  const file = match[2];
  if (path.posix.normalize(file) !== file || file.includes("..")) {
    throw parseError(source, "IMAGE_PATH_INVALID");
  }
  return {
    component: {
      id: seId(),
      "@ctype": "image",
      _file: file,
      represent: marker === "①",
    },
    file,
    marker,
  };
}

function trimBoundaryBlanks(lines) {
  let start = 0;
  let end = lines.length;
  while (start < end && isBlank(lines[start])) start += 1;
  while (end > start && isBlank(lines[end - 1])) end -= 1;
  return lines.slice(start, end);
}

export function parseMarkdown(markdown, { post = "", source = "<markdown>" } = {}) {
  if (typeof markdown !== "string") throw new TypeError("MARKDOWN_INVALID");
  const normalizedPost = post ? normalizePost(post) : "";
  const lines = markdown.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  // 섹션 머리말은 ko/en 둘 다 받는다 (원본은 한국어 전용이었다).
  const SECTION = {
    title: ["## 제목", "## title"],
    body: ["## 본문", "## body"],
    tags: ["## 해시태그", "## tags"],
  };
  const isSection = (line, kind) => SECTION[kind].includes(line.trim().toLowerCase())
    || SECTION[kind].includes(line.trim());
  const titleHeading = lines.findIndex((line) => isSection(line, "title"));
  const bodyHeading = lines.findIndex((line, index) => index > titleHeading && isSection(line, "body"));
  const tagsHeading = lines.findIndex((line, index) => index > bodyHeading && isSection(line, "tags"));
  if (titleHeading < 0) throw parseError(source, "TITLE_SECTION_MISSING");
  if (bodyHeading < 0) throw parseError(source, "BODY_SECTION_MISSING");
  if (tagsHeading < 0) throw parseError(source, "TAGS_SECTION_MISSING");

  const titleLine = lines.slice(titleHeading + 1, bodyHeading).find((line) => !isBlank(line));
  const titleValue = titleLine ? stripInlineMarkers(titleLine).trim() : "";
  if (!titleValue) throw parseError(source, "TITLE_MISSING");

  const bodyLines = trimBoundaryBlanks(lines.slice(bodyHeading + 1, tagsHeading));
  if (bodyLines.length === 0) throw parseError(source, "BODY_MISSING");
  const tags = parseTags(lines.slice(tagsHeading + 1), source);
  const components = [documentTitle(titleValue)];
  const images = [];
  const links = [];
  let textRun = [];

  const appendEmpty = () => {
    if (!isEmptyParagraph(textRun.at(-1))) textRun.push(emptyParagraph());
  };
  const appendParagraph = (value, fontSizeCode = "fs15", forceBold = false) => {
    textRun.push(paragraph(inlineNodes(value, { fontSizeCode, forceBold })));
  };
  const flushText = () => {
    if (textRun.length > 0) {
      components.push(text(textRun));
    }
    textRun = [];
  };
  const appendComponent = (component) => {
    flushText();
    components.push(component);
  };

  for (let index = 0; index < bodyLines.length; index += 1) {
    const line = bodyLines[index];
    const trimmed = line.trim();
    if (isBlank(line)) {
      appendEmpty();
      continue;
    }

    const placeholder = imagePlaceholder(line, source);
    if (placeholder) {
      appendComponent(placeholder.component);
      images.push(placeholder.file);
      continue;
    }

    if (trimmed === "---") {
      appendComponent(horizontalLine());
      continue;
    }

    if (isStandaloneUrl(line)) {
      appendComponent(oglink(trimmed));
      links.push(trimmed);
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quoteParagraphs = [];
      while (index < bodyLines.length && bodyLines[index].trim().startsWith(">")) {
        const quoteValue = bodyLines[index].trim().replace(/^>\s?/, "");
        quoteParagraphs.push(paragraph(inlineNodes(quoteValue), { style: false }));
        index += 1;
      }
      index -= 1;
      appendComponent(quotation(quoteParagraphs));
      continue;
    }

    const tableHeader = splitTableRow(line);
    if (tableHeader && index + 1 < bodyLines.length && isTableDelimiter(bodyLines[index + 1])) {
      const delimiter = splitTableRow(bodyLines[index + 1]);
      if (tableHeader.length !== 2 || delimiter?.length !== 2) {
        throw parseError(source, "TABLE_COLUMN_COUNT_INVALID");
      }
      const rows = [tableHeader.map((cell) => inlineNodes(cell))];
      index += 2;
      while (index < bodyLines.length) {
        const row = splitTableRow(bodyLines[index]);
        if (!row) break;
        if (row.length !== 2) throw parseError(source, "TABLE_COLUMN_COUNT_INVALID");
        rows.push(row.map((cell) => inlineNodes(cell)));
        index += 1;
      }
      index -= 1;
      if (rows.length < 2) throw parseError(source, "TABLE_ROWS_INVALID");
      appendComponent(table(rows));
      continue;
    }

    if (trimmed.startsWith("### ")) {
      appendEmpty();
      appendParagraph(trimmed.slice(4).trim(), "fs19", true);
      appendEmpty();
      continue;
    }

    if (/^-\s+\S/u.test(trimmed)) {
      while (index < bodyLines.length) {
        const match = /^-\s+(.+)$/u.exec(bodyLines[index].trim());
        if (!match) break;
        appendParagraph(`· ${match[1]}`);
        index += 1;
      }
      index -= 1;
      continue;
    }

    if (/^\d+\.\s+\S/u.test(trimmed)) {
      while (index < bodyLines.length) {
        const match = /^(\d+)\.\s+(.+)$/u.exec(bodyLines[index].trim());
        if (!match) break;
        appendParagraph(`${match[1]}. ${match[2]}`);
        index += 1;
      }
      index -= 1;
      continue;
    }

    if (/^\(출처:\s*.*\)$/u.test(trimmed)) {
      appendParagraph(trimmed, "fs13");
      continue;
    }

    appendParagraph(line.trim());
  }
  flushText();

  if (images.length > 0) {
    const imageComponents = components.filter((component) => component["@ctype"] === "image");
    const representatives = imageComponents.filter((component) => component.represent);
    if (representatives.length !== 1 || imageComponents[0]?.represent !== true) {
      throw parseError(source, "IMAGE_REPRESENTATIVE_INVALID");
    }
  }

  const documentModel = createDocumentModel(components);
  return {
    documentModel,
    meta: {
      post: normalizedPost,
      source,
      title: titleValue,
      tags,
      tagsCsv: tags.join(","),
      images,
      links,
    },
  };
}

export const markdownToDocument = parseMarkdown;
export const convertMarkdown = parseMarkdown;

async function findPostPath(post) {
  const normalizedPost = normalizePost(post);
  const entries = await readdir(POSTS_ROOT, { withFileTypes: true });
  const matches = entries
    .filter((entry) => entry.isFile() && entry.name.startsWith(`${normalizedPost}-`) && entry.name.endsWith(".md"))
    .map((entry) => entry.name);
  if (matches.length === 0) throw parseError(normalizedPost, "POST_NOT_FOUND");
  if (matches.length > 1) throw parseError(normalizedPost, "POST_AMBIGUOUS");
  return { normalizedPost, filePath: path.join(POSTS_ROOT, matches[0]) };
}

async function validateImages(images) {
  const imageRoot = path.join(POSTS_ROOT, "img");
  for (const relativePath of images) {
    const resolved = path.resolve(POSTS_ROOT, relativePath);
    if (!resolved.startsWith(`${imageRoot}${path.sep}`)) throw parseError(relativePath, "IMAGE_PATH_INVALID");
    try {
      await access(resolved);
    } catch {
      throw parseError(relativePath, "IMAGE_FILE_MISSING");
    }
  }
}

export async function convertPost(post) {
  const { normalizedPost, filePath } = await findPostPath(post);
  const source = path.relative(POSTS_ROOT, filePath).split(path.sep).join("/");
  const bundle = parseMarkdown(await readFile(filePath, "utf8"), {
    post: normalizedPost,
    source,
  });
  await validateImages(bundle.meta.images);
  return bundle;
}

export function buildPath(post) {
  return path.join(BUILD_ROOT, `${normalizePost(post)}.json`);
}

export async function writeBuildBundle(post, bundle) {
  if (!bundle?.documentModel?.document || !bundle?.meta) {
    throw new TypeError("BUILD_BUNDLE_INVALID");
  }
  const outputPath = buildPath(post);
  await mkdir(path.dirname(outputPath), { recursive: true });
  const temporaryPath = `${outputPath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(bundle, null, 2)}\n`, "utf8");
  await rename(temporaryPath, outputPath);
  return outputPath;
}

export async function buildPost(post, { write = true } = {}) {
  const normalizedPost = normalizePost(post);
  const bundle = await convertPost(normalizedPost);
  if (write) await writeBuildBundle(normalizedPost, bundle);
  return bundle;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1) throw new Error("USAGE: node scripts/naver/md_to_document.mjs <post>");
  const post = normalizePost(args[0]);
  const bundle = await buildPost(post, { write: true });
  const counts = componentCounts(bundle.documentModel);
  console.log(
    `OK post=${post} components=${bundle.documentModel.document.components.length}` +
    ` images=${counts.image ?? 0} links=${counts.oglink ?? 0} tags=${bundle.meta.tags.length}` +
    ` output=${buildPath(post)}`,
  );
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isMain) {
  main().catch((error) => {
    console.error(`FAIL ${error instanceof Error ? error.message : "UNKNOWN"}`);
    process.exitCode = 1;
  });
}
