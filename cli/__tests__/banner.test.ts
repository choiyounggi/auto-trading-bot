import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Writable } from "node:stream";

import { ART, renderBanner, printBanner, readPackageVersion } from "../banner.js";

/** Collect everything written to a stream so assertions can inspect it. */
function capture(): { stream: Writable; text: () => string } {
  const chunks: string[] = [];
  const stream = new Writable({
    write(chunk, _enc, cb) {
      chunks.push(String(chunk));
      cb();
    },
  });
  return { stream, text: () => chunks.join("") };
}

function tempPkgDir(contents: string | null): string {
  const dir = mkdtempSync(join(tmpdir(), "kis-banner-"));
  if (contents !== null) writeFileSync(join(dir, "package.json"), contents);
  return dir;
}

// ── normal ────────────────────────────────────────────────────────────

test("renderBanner includes the version and every ART row", () => {
  const out = renderBanner("1.2.3");
  assert.match(out, /v1\.2\.3/);
  for (const row of ART) {
    assert.ok(out.includes(row), `banner is missing ART row: ${row}`);
  }
  assert.equal(ART.length, 6, "ART must be exactly 6 rows");
});

test("printBanner writes to the injected stream only", () => {
  const { stream, text } = capture();
  printBanner("1.2.3", stream);
  assert.ok(text().length > 0);
  assert.ok(text().includes("v1.2.3"));
  // The art rows must be present in what the injected stream received,
  // proving nothing was routed to process.stdout instead.
  assert.ok(text().includes(ART[0]));
});

test("readPackageVersion reads the version field", () => {
  const dir = tempPkgDir(JSON.stringify({ name: "x", version: "9.9.9" }));
  try {
    assert.equal(readPackageVersion(dir), "9.9.9");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ── error ─────────────────────────────────────────────────────────────

test("readPackageVersion returns '?' for a missing directory and does not throw", () => {
  let result: string | undefined;
  assert.doesNotThrow(() => {
    result = readPackageVersion("/nonexistent/path/kis-trader-does-not-exist");
  });
  assert.equal(result, "?");
});

test("readPackageVersion returns '?' for unparseable JSON", () => {
  const dir = tempPkgDir("{ this is not json");
  try {
    assert.equal(readPackageVersion(dir), "?");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ── boundary ──────────────────────────────────────────────────────────

test("readPackageVersion returns '?' when the version key is absent", () => {
  const dir = tempPkgDir(JSON.stringify({ name: "x" }));
  try {
    assert.equal(readPackageVersion(dir), "?");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readPackageVersion returns '?' when version is not a string", () => {
  const dir = tempPkgDir(JSON.stringify({ name: "x", version: 3 }));
  try {
    assert.equal(readPackageVersion(dir), "?");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("renderBanner with an empty version still renders all ART rows and the footer", () => {
  const out = renderBanner("");
  for (const row of ART) {
    assert.ok(out.includes(row), `banner is missing ART row: ${row}`);
  }
  assert.ok(
    out.includes("github.com/choiyounggi/auto-trading-bot"),
    "footer URL must survive an empty version",
  );
});

test("every ART row has the same display width", () => {
  const widths = new Set(ART.map((r) => [...r].length));
  assert.equal(widths.size, 1, `ART rows differ in width: ${[...widths].join(", ")}`);
});
