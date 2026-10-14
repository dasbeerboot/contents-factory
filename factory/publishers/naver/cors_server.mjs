#!/usr/bin/env node
// Vendored from pilot/scripts/naver/lib/cors_server.mjs (2026-10-14).
// Serves the built document bundle to the editor tab so the injected program stays
// small: the aside REPL caps program size, the document does not go through it.

import { createReadStream } from "node:fs";
import { realpath, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const CORS_HOST = "127.0.0.1";
// 0 lets the OS pick a free port, so this never collides with another
// local static server. The caller passes the bound port into the browser code.
export const CORS_PORT = 0;

const FACTORY_HOME = process.env.FACTORY_HOME
  ? path.resolve(process.env.FACTORY_HOME.replace(/^~(?=$|\/)/, homedir()))
  : path.join(homedir(), ".contents-factory");
const DEFAULT_BUILD_ROOT = path.join(FACTORY_HOME, "state", "naver", "build");
const DEFAULT_IMAGE_ROOT = path.join(FACTORY_HOME, "outputs", "naver", "img");

const CONTENT_TYPES = new Map([
  [".avif", "image/avif"],
  [".gif", "image/gif"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml; charset=utf-8"],
  [".webp", "image/webp"],
]);

const BASE_HEADERS = {
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Private-Network": "true",
  "Cache-Control": "no-store",
  "Cross-Origin-Resource-Policy": "cross-origin",
  "X-Content-Type-Options": "nosniff",
};

function fail(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function send(res, statusCode, body = "") {
  res.writeHead(statusCode, {
    ...BASE_HEADERS,
    "Content-Length": Buffer.byteLength(body),
    "Content-Type": "text/plain; charset=utf-8",
  });
  res.end(body);
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function routePath(pathname, roots) {
  for (const [mount, root] of roots) {
    if (pathname === mount || pathname.startsWith(`${mount}/`)) {
      const encodedRelative = pathname.slice(mount.length).replace(/^\/+/, "");
      let relative;
      try {
        relative = decodeURIComponent(encodedRelative);
      } catch {
        throw fail("CORS_PATH_INVALID");
      }
      if (!relative || relative.includes("\0") || relative.includes("\\")) {
        throw fail("CORS_PATH_INVALID");
      }
      const candidate = path.resolve(root, relative);
      if (!isWithin(root, candidate)) throw fail("CORS_PATH_FORBIDDEN");
      return { root, candidate };
    }
  }
  return null;
}

async function resolveSafeFile(root, candidate) {
  let actualRoot;
  let actualFile;
  try {
    [actualRoot, actualFile] = await Promise.all([realpath(root), realpath(candidate)]);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") return null;
    throw fail("CORS_FILE_UNAVAILABLE");
  }
  if (!isWithin(actualRoot, actualFile)) throw fail("CORS_PATH_FORBIDDEN");

  let info;
  try {
    info = await stat(actualFile);
  } catch {
    throw fail("CORS_FILE_UNAVAILABLE");
  }
  return info.isFile() ? { path: actualFile, size: info.size } : null;
}

async function handleRequest(req, res, roots) {
  if (req.method === "OPTIONS") {
    res.writeHead(204, BASE_HEADERS);
    res.end();
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { ...BASE_HEADERS, Allow: "GET, HEAD, OPTIONS", "Content-Length": "0" });
    res.end();
    return;
  }

  let pathname;
  try {
    pathname = new URL(req.url ?? "/", `http://${CORS_HOST}`).pathname;
  } catch {
    send(res, 400, "BAD_REQUEST");
    return;
  }

  try {
    const route = routePath(pathname, roots);
    if (!route) {
      send(res, 404, "NOT_FOUND");
      return;
    }
    const file = await resolveSafeFile(route.root, route.candidate);
    if (!file) {
      send(res, 404, "NOT_FOUND");
      return;
    }

    const headers = {
      ...BASE_HEADERS,
      "Content-Length": String(file.size),
      "Content-Type": CONTENT_TYPES.get(path.extname(file.path).toLowerCase()) ?? "application/octet-stream",
    };
    res.writeHead(200, headers);
    if (req.method === "HEAD") {
      res.end();
      return;
    }

    const stream = createReadStream(file.path);
    stream.once("error", () => {
      if (!res.headersSent) send(res, 500, "READ_FAILED");
      else res.destroy();
    });
    stream.pipe(res);
  } catch (error) {
    if (error?.code === "CORS_PATH_INVALID") send(res, 400, "BAD_REQUEST");
    else if (error?.code === "CORS_PATH_FORBIDDEN") send(res, 403, "FORBIDDEN");
    else send(res, 500, "INTERNAL_ERROR");
  }
}

export async function startCorsServer(options = {}) {
  const port = options.port ?? CORS_PORT;
  if (!Number.isInteger(port) || port < 0 || port > 65_535) throw fail("CORS_PORT_INVALID");

  const buildRoot = path.resolve(options.buildRoot ?? DEFAULT_BUILD_ROOT);
  const imageRoot = path.resolve(options.imageRoot ?? DEFAULT_IMAGE_ROOT);
  const roots = new Map([
    ["/build", buildRoot],
    ["/img", imageRoot],
  ]);
  const server = createServer((req, res) => {
    void handleRequest(req, res, roots);
  });

  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off("listening", onListening);
      reject(fail(error?.code === "EADDRINUSE" ? "CORS_PORT_IN_USE" : "CORS_LISTEN_FAILED"));
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, CORS_HOST);
  });

  const address = server.address();
  const boundPort = typeof address === "object" && address ? address.port : port;
  let closePromise = null;
  const close = () => {
    if (closePromise) return closePromise;
    closePromise = new Promise((resolve, reject) => {
      if (!server.listening) {
        resolve();
        return;
      }
      server.close((error) => {
        if (error) reject(fail("CORS_CLOSE_FAILED"));
        else resolve();
      });
      server.closeAllConnections?.();
    });
    return closePromise;
  };

  return {
    server,
    close,
    host: CORS_HOST,
    port: boundPort,
    origin: `http://${CORS_HOST}:${boundPort}`,
    buildRoot,
    imageRoot,
  };
}

const isDirect = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirect) {
  try {
    const runtime = await startCorsServer();
    console.log(`CORS_SERVER_READY ${runtime.origin}`);
    let stopping = false;
    const stop = async () => {
      if (stopping) return;
      stopping = true;
      await runtime.close();
    };
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
  } catch (error) {
    console.error(`FAIL ${error?.message ?? "CORS_START_FAILED"}`);
    process.exitCode = 1;
  }
}
