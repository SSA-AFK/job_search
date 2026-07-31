import { spawnSync } from "node:child_process";
import { statSync } from "node:fs";
import { posix, win32 } from "node:path";

function pathApiFor(platform) {
  return platform === "win32" ? win32 : posix;
}

function isRegularFile(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function findPythonOnPath(platform) {
  const command = platform === "win32" ? "where.exe" : "which";
  const arguments_ = platform === "win32" ? ["python"] : ["python3"];
  const result = spawnSync(command, arguments_, { encoding: "utf8", windowsHide: true });
  if (result.error || result.status !== 0) return undefined;

  const pathApi = pathApiFor(platform);
  return result.stdout
    .split(/\r?\n/)
    .map((candidate) => candidate.trim())
    .find((candidate) => pathApi.isAbsolute(candidate) && isRegularFile(candidate));
}

export function resolvePythonInterpreter({
  backendDirectory,
  environment = process.env,
  platform = process.platform,
  isFile = isRegularFile,
  findOnPath = findPythonOnPath,
}) {
  const pathApi = pathApiFor(platform);
  const override = environment.PLAYWRIGHT_PYTHON;
  if (override) {
    if (!pathApi.isAbsolute(override) || !isFile(override)) {
      throw new Error(
        "PLAYWRIGHT_PYTHON must be an absolute path to a regular Python executable. " +
        "Create backend/.venv as documented, or set PLAYWRIGHT_PYTHON to that executable.",
      );
    }
    return { executable: override, source: "PLAYWRIGHT_PYTHON" };
  }

  const venvExecutable = pathApi.join(
    backendDirectory,
    ".venv",
    platform === "win32" ? "Scripts" : "bin",
    platform === "win32" ? "python.exe" : "python",
  );
  if (isFile(venvExecutable)) {
    return { executable: venvExecutable, source: "backend/.venv" };
  }

  const fallback = findOnPath(platform);
  if (fallback && pathApi.isAbsolute(fallback) && isFile(fallback)) {
    return { executable: fallback, source: "PATH" };
  }

  throw new Error(
    "No usable Python interpreter was found. Create backend/.venv as documented, " +
    "or set PLAYWRIGHT_PYTHON to an absolute Python executable with the backend dependencies installed.",
  );
}
