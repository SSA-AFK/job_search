import { spawn, spawnSync } from "node:child_process";
import { existsSync, unlinkSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { resolvePythonInterpreter } from "./python-interpreter.mjs";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(testDirectory, "..");
const backendDirectory = resolve(frontendDirectory, "..", "backend");
const databaseFilename = ".playwright-seeded.sqlite3";
const databasePath = resolve(backendDirectory, databaseFilename);
const python = resolvePythonInterpreter({ backendDirectory });

if (dirname(databasePath) !== backendDirectory || basename(databasePath) !== databaseFilename) {
  throw new Error(`Refusing to replace unexpected database path: ${databasePath}`);
}
const environment = {
  ...process.env,
  COLLECTION_ENABLED: "false",
  DATABASE_URL: `sqlite:///${databasePath.replaceAll("\\", "/")}`,
  PYTHONUNBUFFERED: "1",
};

function runPython(arguments_, { capture = false } = {}) {
  const result = spawnSync(python.executable, arguments_, {
    cwd: backendDirectory,
    env: environment,
    encoding: "utf8",
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
  });
  if (result.error) {
    throw new Error(`Unable to run Python interpreter ${python.executable}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    if (capture && result.stderr) process.stderr.write(result.stderr);
    throw new Error(`Python command failed (${result.status}): ${arguments_.join(" ")}`);
  }
  return capture ? result.stdout.trim() : "";
}

function preflightPython() {
  try {
    runPython(["-c", "import alembic, sqlalchemy, uvicorn; import app.main; import app.seed.cli"]);
  } catch (error) {
    throw new Error(
      `Python preflight failed for ${python.executable} (${python.source}). ` +
      "Create backend/.venv as documented, or set PLAYWRIGHT_PYTHON to an executable with the backend dependencies installed. " +
      error.message,
    );
  }
}

const countScript = [
  "import json",
  "from sqlalchemy import func, select",
  "from app.core.database import SessionLocal",
  "from app.models import Company, CompanyAlias, JobPosting, JobSource, RegulatoryFiling",
  "with SessionLocal() as session:",
  "    counts = {",
  '        "aliases": session.scalar(select(func.count(CompanyAlias.id))),',
  '        "companies": session.scalar(select(func.count(Company.id))),',
  '        "filings": session.scalar(select(func.count(RegulatoryFiling.id))),',
  '        "job_sources": session.scalar(select(func.count(JobSource.id))),',
  '        "jobs": session.scalar(select(func.count(JobPosting.id))),',
  "    }",
  "print(json.dumps(counts, sort_keys=True))",
].join("\n");

function readCounts() {
  return JSON.parse(runPython(["-c", countScript], { capture: true }));
}

preflightPython();
if (existsSync(databasePath)) {
  unlinkSync(databasePath);
}
console.log(`PLAYWRIGHT_PYTHON ${python.executable} (${python.source})`);
console.log(`PLAYWRIGHT_DATABASE ${databasePath}`);
runPython(["-m", "alembic", "upgrade", "head"]);
runPython(["-m", "app.seed.cli", "data/companies.seed.json"]);
const firstCounts = readCounts();
console.log(`PLAYWRIGHT_SEED_FIRST ${JSON.stringify(firstCounts)}`);
runPython(["-m", "app.seed.cli", "data/companies.seed.json"]);
const secondCounts = readCounts();
console.log(`PLAYWRIGHT_SEED_SECOND ${JSON.stringify(secondCounts)}`);

const expectedCounts = {
  aliases: 10,
  companies: 5,
  filings: 2,
  job_sources: 7,
  jobs: 6,
};
if (JSON.stringify(firstCounts) !== JSON.stringify(expectedCounts)) {
  throw new Error(`Unexpected first seed counts: ${JSON.stringify(firstCounts)}`);
}
if (JSON.stringify(secondCounts) !== JSON.stringify(firstCounts)) {
  throw new Error(`Repeated seed changed row counts: ${JSON.stringify(secondCounts)}`);
}
console.log("PLAYWRIGHT_SEED_STABLE true");

const server = spawn(
  python.executable,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8011"],
  { cwd: backendDirectory, env: environment, stdio: "inherit" },
);

server.on("error", (error) => {
  console.error(error);
  process.exit(1);
});
server.on("exit", (code) => process.exit(code ?? 1));
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}
