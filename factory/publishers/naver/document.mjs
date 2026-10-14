import { randomBytes, randomUUID } from "node:crypto";

const ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const DOCUMENT_DI = {
  dif: false,
  dio: [{ dis: "N", dia: { t: 0, p: 0, st: 780, sk: 3 } }],
};

export function seId() {
  return `SE-${randomUUID()}`;
}

function encodeBase32(value, length) {
  let encoded = "";
  let remaining = value;
  for (let index = 0; index < length; index += 1) {
    encoded = ULID_ALPHABET[Number(remaining & 31n)] + encoded;
    remaining >>= 5n;
  }
  return encoded;
}

export function createDocumentId(timestamp = Date.now()) {
  if (!Number.isSafeInteger(timestamp) || timestamp < 0 || timestamp > 0xffffffffffff) {
    throw new RangeError("DOCUMENT_TIMESTAMP_INVALID");
  }

  const randomness = randomBytes(10).reduce(
    (value, byte) => (value << 8n) | BigInt(byte),
    0n,
  );
  return `${encodeBase32(BigInt(timestamp), 10)}${encodeBase32(randomness, 16)}`;
}

export function textNode(value, { bold = false, fontSizeCode = "fs15" } = {}) {
  if (typeof value !== "string") throw new TypeError("TEXT_NODE_VALUE_INVALID");
  return {
    id: seId(),
    value,
    style: {
      fontSizeCode,
      ...(bold ? { bold: true } : {}),
      "@ctype": "nodeStyle",
    },
    "@ctype": "textNode",
  };
}

export function emptyTextNode() {
  return { id: seId(), value: "", "@ctype": "textNode" };
}

export function paragraph(nodes, { style = true } = {}) {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    throw new TypeError("PARAGRAPH_NODES_INVALID");
  }
  return {
    id: seId(),
    nodes,
    ...(style
      ? { style: { lineHeight: 1.8, "@ctype": "paragraphStyle" } }
      : {}),
    "@ctype": "paragraph",
  };
}

export function emptyParagraph() {
  return paragraph([emptyTextNode()]);
}

export function text(paragraphs) {
  if (!Array.isArray(paragraphs) || paragraphs.length === 0) {
    throw new TypeError("TEXT_PARAGRAPHS_INVALID");
  }
  return {
    id: seId(),
    layout: "default",
    value: paragraphs,
    "@ctype": "text",
  };
}

export function quotation(paragraphs, layout = "quotation_line") {
  if (!Array.isArray(paragraphs) || paragraphs.length === 0) {
    throw new TypeError("QUOTATION_PARAGRAPHS_INVALID");
  }
  return {
    id: seId(),
    layout,
    value: paragraphs,
    source: null,
    "@ctype": "quotation",
  };
}

export function horizontalLine(layout = "line1") {
  return {
    id: seId(),
    layout,
    align: "left",
    "@ctype": "horizontalLine",
  };
}

export function oglink(link) {
  const parsed = new URL(link);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new TypeError("OGLINK_PROTOCOL_INVALID");
  }
  return {
    id: seId(),
    layout: "text",
    link,
    domain: parsed.host,
    title: "",
    description: "",
    video: false,
    "@ctype": "oglink",
  };
}

export function image(upload, represent = false) {
  const value = upload?.response ?? upload;
  const originalWidth = Number(value?.width);
  const originalHeight = Number(value?.height);
  const fileSize = Number(value?.fileSize);
  if (
    !value ||
    typeof value.url !== "string" ||
    typeof value.domain !== "string" ||
    typeof value.fileName !== "string" ||
    !Number.isFinite(originalWidth) ||
    originalWidth <= 0 ||
    !Number.isFinite(originalHeight) ||
    originalHeight <= 0 ||
    !Number.isFinite(fileSize) ||
    fileSize < 0
  ) {
    throw new TypeError("IMAGE_UPLOAD_INVALID");
  }

  const width = Math.min(693, originalWidth);
  return {
    id: seId(),
    layout: "default",
    src: `${value.domain}${value.url}?type=w1`,
    internalResource: false,
    represent: Boolean(represent),
    path: value.url,
    domain: value.domain,
    fileSize,
    width,
    widthPercentage: 0,
    height: Math.round((originalHeight * width) / originalWidth),
    originalWidth,
    originalHeight,
    fileName: value.fileName,
    caption: null,
    format: "normal",
    displayFormat: "normal",
    imageLoaded: true,
    contentMode: "fit",
    origin: { srcFrom: "local", "@ctype": "imageOrigin" },
    ai: false,
    "@ctype": "image",
  };
}

export function documentTitle(title) {
  if (typeof title !== "string" || title.length === 0) {
    throw new TypeError("DOCUMENT_TITLE_INVALID");
  }
  return {
    id: seId(),
    layout: "default",
    title: [{
      id: seId(),
      nodes: [{ id: seId(), value: title, "@ctype": "textNode" }],
      "@ctype": "paragraph",
    }],
    subTitle: null,
    align: "left",
    "@ctype": "documentTitle",
  };
}

function normalizeCellNodes(cell) {
  if (typeof cell === "string") return [textNode(cell)];
  if (!Array.isArray(cell) || cell.length === 0) {
    throw new TypeError("TABLE_CELL_INVALID");
  }
  return cell;
}

function tableCell(cell, { header, width }) {
  let nodes = normalizeCellNodes(cell);
  if (header) {
    nodes = nodes.map((node) => ({
      ...node,
      style: {
        ...(node.style ?? { fontSizeCode: "fs15" }),
        backgroundColor: "#f5f5f2",
        bold: true,
        "@ctype": "nodeStyle",
      },
    }));
  }

  const cellParagraph = {
    id: seId(),
    nodes,
    ...(header ? { style: { align: "center", "@ctype": "paragraphStyle" } } : {}),
    "@ctype": "paragraph",
  };
  return {
    id: seId(),
    colSpan: 1,
    rowSpan: 1,
    width,
    height: 40,
    ...(header ? { backgroundColor: "#f5f5f2" } : {}),
    value: [cellParagraph],
    "@ctype": "tableCell",
  };
}

export function table(rows) {
  if (!Array.isArray(rows) || rows.length < 2) {
    throw new TypeError("TABLE_ROWS_INVALID");
  }
  const columnCount = rows[0]?.length;
  if (columnCount !== 2 || rows.some((row) => !Array.isArray(row) || row.length !== columnCount)) {
    throw new TypeError("TABLE_COLUMN_COUNT_INVALID");
  }
  const cellWidth = 100 / columnCount;
  return {
    id: seId(),
    layout: "table_layout1",
    width: 100,
    rows: rows.map((row, rowIndex) => ({
      cells: row.map((cell) => tableCell(cell, { header: rowIndex === 0, width: cellWidth })),
      "@ctype": "tableRow",
    })),
    columnCount,
    borderStyleName: "thinLine",
    "@ctype": "table",
  };
}

export function createDocumentModel(components) {
  if (!Array.isArray(components) || components.length === 0) {
    throw new TypeError("DOCUMENT_COMPONENTS_INVALID");
  }
  return {
    documentId: "",
    document: {
      version: "2.10.2",
      theme: "default",
      language: "ko-KR",
      id: createDocumentId(),
      components,
      di: structuredClone(DOCUMENT_DI),
    },
  };
}

function lookupUpload(uploadsMap, file) {
  const source = uploadsMap?.uploads ?? uploadsMap;
  if (source instanceof Map) return source.get(file);
  if (Array.isArray(source)) {
    const entry = source.find((candidate) =>
      candidate?._file === file || candidate?.file === file || candidate?.path === file
    );
    return entry?.response ?? entry;
  }
  if (source && typeof source === "object") {
    return source[file] ?? source[`/${file}`];
  }
  return undefined;
}

export function replaceImagePlaceholders(documentModel, uploadsMap) {
  if (!documentModel?.document || !Array.isArray(documentModel.document.components)) {
    throw new TypeError("DOCUMENT_MODEL_INVALID");
  }
  const components = documentModel.document.components.map((component) => {
    if (component?.["@ctype"] !== "image" || typeof component._file !== "string") {
      return component;
    }
    const upload = lookupUpload(uploadsMap, component._file);
    if (!upload) {
      const error = new Error("IMAGE_UPLOAD_MISSING");
      error.code = "IMAGE_UPLOAD_MISSING";
      throw error;
    }
    return { ...image(upload, component.represent), id: component.id };
  });
  return {
    ...documentModel,
    document: { ...documentModel.document, components },
  };
}

export function componentCounts(documentModel) {
  if (!documentModel?.document || !Array.isArray(documentModel.document.components)) {
    throw new TypeError("DOCUMENT_MODEL_INVALID");
  }
  return documentModel.document.components.reduce((counts, component) => {
    const type = component?.["@ctype"];
    if (typeof type !== "string") throw new TypeError("COMPONENT_CTYPE_INVALID");
    counts[type] = (counts[type] ?? 0) + 1;
    return counts;
  }, {});
}
