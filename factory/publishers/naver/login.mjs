// Opens (or inspects) the Naver session for the playwright backend.
//
// usage: node login.mjs --user-data-dir <path> [--status] [--wait-ms 600000]
//
// The window is the user's: they log in by hand, including any 2FA, and the session
// stays in the persistent context directory. Nothing is typed for them and no cookie
// value is printed — the result is a boolean.
import { runPlaywrightLogin } from "./driver_playwright.mjs";

const RESULT_MARKER = "__FACTORY_NAVER_RESULT__";

export function parseArgs(argv) {
  const options = { userDataDir: "", status: false, waitMs: 0 };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--status") {
      options.status = true;
    } else if (argument === "--user-data-dir" || argument === "--wait-ms") {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error(argument === "--wait-ms" ? "WAIT_MS_REQUIRED"
          : "USER_DATA_DIR_REQUIRED");
      }
      if (argument === "--user-data-dir") options.userDataDir = value;
      if (argument === "--wait-ms") {
        const parsed = Number(value);
        if (!Number.isFinite(parsed) || parsed < 0) throw new Error("WAIT_MS_INVALID");
        options.waitMs = parsed;
      }
      index += 1;
    } else {
      throw new Error("UNKNOWN_OPTION");
    }
  }
  if (!options.userDataDir) throw new Error("USER_DATA_DIR_REQUIRED");
  return options;
}

export async function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    return { ok: false, code: String(error?.message || "UNKNOWN_OPTION") };
  }
  try {
    const result = await runPlaywrightLogin(options);
    return { ok: Boolean(result.logged_in), code: result.logged_in ? "OK"
      : "NAVER_SESSION_MISSING", ...result };
  } catch (error) {
    const code = typeof error?.code === "string" ? error.code : "LOGIN_FAILED";
    return { ok: false, code };
  }
}

const invokedDirectly = process.argv[1] && process.argv[1].endsWith("login.mjs");
if (invokedDirectly) {
  main(process.argv.slice(2)).then((result) => {
    process.stdout.write(RESULT_MARKER + JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 1);
  }).catch(() => {
    process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false,
      code: "LOGIN_FAILED" }) + "\n");
    process.exit(1);
  });
}

export { RESULT_MARKER };
