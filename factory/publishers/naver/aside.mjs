// Vendored from pilot/scripts/naver/lib/aside.mjs (2026-10-14). Drives the aside CLI
// REPL once per step and returns the JSON value the browser-side body returned.
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

export const ASIDE_MAX_CODE_BYTES = 100 * 1024;
export const ASIDE_MAX_OUTPUT_BYTES = 32 * 1024 * 1024;
export const ASIDE_REPL_TIMEOUT_MS = 120_000;
export const ASIDE_HOST_TIMEOUT_MS = 130_000;

const RESULT_PREFIX = "__FACTORY_ASIDE_JSON__";

function fail(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function wrapCode(code, marker) {
  // 최상위 블록 `{ … }` 으로 감싸면 aside REPL 이 await 를 식별자로 파싱한다("await is not defined", 2026-09-08 실측) — 블록 없이 최상위 await.
  return `const __factoryAsideResult = await (async () => {
${code}
})();
console.log(${JSON.stringify(marker)} + JSON.stringify(__factoryAsideResult ?? null));`;
}

function parseResult(stdout, marker, stderr = "") {
  const markerIndex = stdout.lastIndexOf(marker);
  if (markerIndex < 0) {
    // REPL 안에서 throw 되면 aside 는 종료코드 0 으로 에러 텍스트만 찍는다 — 원인이 코드에 묻히지 않게 꼬리를 싣는다.
    const tail = `${stdout}\n${stderr}`.replace(/\u001b\[[0-9;]*m/g, "").trim().split("\n").filter((line) => line.trim() && !/^\s+at /.test(line)).slice(-4).join(" | ").slice(0, 600);
    const error = fail("ASIDE_RESULT_MISSING");
    error.detail = tail;
    throw error;
  }

  const start = markerIndex + marker.length;
  const newline = stdout.indexOf("\n", start);
  const raw = stdout.slice(start, newline < 0 ? undefined : newline).trim();
  try {
    return JSON.parse(raw);
  } catch {
    throw fail("ASIDE_RESULT_INVALID");
  }
}

function runProcess(binary, args, { cwd, timeoutMs }) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(binary, args, {
        cwd,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch {
      reject(fail("ASIDE_UNAVAILABLE"));
      return;
    }
    const stdoutChunks = [];
    const stderrChunks = [];
    let outputBytes = 0;
    let failureCode = null;
    let settled = false;
    let forceKillTimer = null;

    const finish = (callback) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (forceKillTimer) clearTimeout(forceKillTimer);
      callback();
    };

    const stop = (code) => {
      if (failureCode) return;
      failureCode = code;
      child.kill("SIGTERM");
      forceKillTimer = setTimeout(() => child.kill("SIGKILL"), 2_000);
      forceKillTimer.unref();
    };

    const collect = (target, chunk) => {
      outputBytes += chunk.length;
      if (outputBytes > ASIDE_MAX_OUTPUT_BYTES) {
        stop("ASIDE_OUTPUT_TOO_LARGE");
        return;
      }
      target.push(chunk);
    };

    child.stdout.on("data", (chunk) => collect(stdoutChunks, chunk));
    child.stderr.on("data", (chunk) => collect(stderrChunks, chunk));

    child.once("error", () => {
      finish(() => reject(fail("ASIDE_UNAVAILABLE")));
    });

    child.once("close", (exitCode) => {
      finish(() => {
        if (failureCode) {
          reject(fail(failureCode));
          return;
        }
        if (exitCode !== 0) {
          reject(fail(`ASIDE_FAILED_${exitCode ?? "UNKNOWN"}`));
          return;
        }
        resolve({
          stdout: Buffer.concat(stdoutChunks).toString("utf8"),
          stderr: Buffer.concat(stderrChunks).toString("utf8"),
        });
      });
    });

    const timeout = setTimeout(() => stop("ASIDE_TIMEOUT"), timeoutMs);
    timeout.unref();
  });
}

/**
 * Runs one Aside REPL step and returns the JSON-serializable value returned by
 * the supplied function body. The browser-side code may use top-level await
 * and should finish with `return value`.
 */
export async function runAsideJson(code, options = {}) {
  if (typeof code !== "string") throw fail("ASIDE_CODE_REQUIRED");

  const timeoutMs = options.timeoutMs ?? ASIDE_HOST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw fail("ASIDE_TIMEOUT_INVALID");

  const marker = `${RESULT_PREFIX}${randomUUID()}__`;
  const wrappedCode = wrapCode(code, marker);
  if (Buffer.byteLength(wrappedCode, "utf8") > ASIDE_MAX_CODE_BYTES) {
    throw fail("ASIDE_CODE_TOO_LARGE");
  }

  let tempRoot;
  try {
    tempRoot = await mkdtemp(path.join(tmpdir(), "contents-factory-aside-"));
  } catch {
    throw fail("ASIDE_TEMPFILE_FAILED");
  }
  const codePath = path.join(tempRoot, "repl.js");
  try {
    let argvCode;
    try {
      await writeFile(codePath, wrappedCode, { encoding: "utf8", flag: "wx", mode: 0o600 });
      argvCode = await readFile(codePath, "utf8");
    } catch {
      throw fail("ASIDE_TEMPFILE_FAILED");
    }
    const args = ["repl"];
    if (options.account) args.push("--account", String(options.account));
    if (options.host) args.push("--host", String(options.host));
    args.push(argvCode);

    const binary = String(options.binary ?? options.asideBin ?? process.env.FACTORY_ASIDE_BIN ?? "aside").trim();
    if (!binary) throw fail("ASIDE_UNAVAILABLE");
    const { stdout, stderr } = await runProcess(binary, args, {
      cwd: options.cwd,
      timeoutMs,
    });
    return parseResult(stdout, marker, stderr);
  } finally {
    await rm(tempRoot, { recursive: true, force: true }).catch(() => {});
  }
}
