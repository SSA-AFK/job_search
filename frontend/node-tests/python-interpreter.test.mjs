import assert from "node:assert/strict";
import test from "node:test";

import { resolvePythonInterpreter } from "../tests/python-interpreter.mjs";

const backendDirectory = "/workspace/backend";
const windowsBackendDirectory = "C:\\workspace\\backend";

test("uses a validated PLAYWRIGHT_PYTHON override", () => {
  const interpreter = resolvePythonInterpreter({
    backendDirectory,
    environment: { PLAYWRIGHT_PYTHON: "/tools/python" },
    platform: "linux",
    isFile: (path) => path === "/tools/python",
    findOnPath: () => "/fallback/python",
  });

  assert.deepEqual(interpreter, {
    executable: "/tools/python",
    source: "PLAYWRIGHT_PYTHON",
  });
});

test("prefers the Windows repository virtual environment over PATH", () => {
  const interpreter = resolvePythonInterpreter({
    backendDirectory: windowsBackendDirectory,
    environment: {},
    platform: "win32",
    isFile: (path) => path === "C:\\workspace\\backend\\.venv\\Scripts\\python.exe",
    findOnPath: () => "C:\\fallback\\python.exe",
  });

  assert.equal(interpreter.executable, "C:\\workspace\\backend\\.venv\\Scripts\\python.exe");
  assert.equal(interpreter.source, "backend/.venv");
});

test("prefers the POSIX repository virtual environment over PATH", () => {
  const interpreter = resolvePythonInterpreter({
    backendDirectory,
    environment: {},
    platform: "linux",
    isFile: (path) => path === "/workspace/backend/.venv/bin/python",
    findOnPath: () => "/fallback/python",
  });

  assert.equal(interpreter.executable, "/workspace/backend/.venv/bin/python");
  assert.equal(interpreter.source, "backend/.venv");
});

test("rejects an invalid override with setup guidance", () => {
  assert.throws(
    () => resolvePythonInterpreter({
      backendDirectory,
      environment: { PLAYWRIGHT_PYTHON: "/missing/python" },
      platform: "linux",
      isFile: () => false,
      findOnPath: () => "/fallback/python",
    }),
    /PLAYWRIGHT_PYTHON.*regular Python executable.*backend[\\/]\.venv/i,
  );
});

test("uses a resolved PATH fallback only when no repository virtual environment exists", () => {
  const interpreter = resolvePythonInterpreter({
    backendDirectory,
    environment: {},
    platform: "linux",
    isFile: (path) => path === "/fallback/python",
    findOnPath: () => "/fallback/python",
  });

  assert.deepEqual(interpreter, {
    executable: "/fallback/python",
    source: "PATH",
  });
});

test("fails with setup guidance when no validated interpreter can be resolved", () => {
  assert.throws(
    () => resolvePythonInterpreter({
      backendDirectory,
      environment: {},
      platform: "linux",
      isFile: () => false,
      findOnPath: () => undefined,
    }),
    /backend[\\/]\.venv.*PLAYWRIGHT_PYTHON/i,
  );
});
