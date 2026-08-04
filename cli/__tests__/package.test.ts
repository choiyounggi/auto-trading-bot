/**
 * Guards on package.json itself.
 *
 * The zero-runtime-dependency rule (plan decision D2) was previously only
 * checked by hand at task-01 time, so nothing would have caught a later
 * `npm install --save`. These tests run on every `npm test`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// dist-test/__tests__/package.test.js → repo root is two levels up.
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");

function pkg(): Record<string, unknown> {
  return JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
}

// ── normal ────────────────────────────────────────────────────────────

test("package.json declares no runtime dependencies", () => {
  const p = pkg();
  const deps = p.dependencies as Record<string, string> | undefined;
  assert.equal(
    deps === undefined || Object.keys(deps).length === 0,
    true,
    `expected zero runtime dependencies, found: ${JSON.stringify(deps)}`,
  );
});

test("package.json declares no optional or peer dependencies either", () => {
  const p = pkg();
  for (const field of ["optionalDependencies", "peerDependencies"] as const) {
    const deps = p[field] as Record<string, string> | undefined;
    assert.equal(
      deps === undefined || Object.keys(deps).length === 0,
      true,
      `expected no ${field}, found: ${JSON.stringify(deps)}`,
    );
  }
});

test("the bin entry points at the built CLI", () => {
  const bin = pkg().bin as Record<string, string>;
  assert.deepEqual(bin, { "kis-trader": "dist/index.js" });
});

// ── error ─────────────────────────────────────────────────────────────

test("reading a nonexistent package.json throws ENOENT", () => {
  assert.throws(
    () => JSON.parse(readFileSync(join(ROOT, "no-such-package.json"), "utf8")),
    (err: NodeJS.ErrnoException) => {
      assert.equal(err.code, "ENOENT");
      return true;
    },
  );
});

// ── boundary ──────────────────────────────────────────────────────────

test("every path in files[] is a prefix-style entry, never an absolute path", () => {
  const files = pkg().files as string[];
  assert.ok(files.length > 0, "files[] must not be empty");
  for (const f of files) {
    assert.equal(f.startsWith("/"), false, `files[] entry must be relative: ${f}`);
    assert.equal(f.includes(".."), false, `files[] entry must not escape root: ${f}`);
  }
});

test("files[] excludes test and planning directories", () => {
  const files = pkg().files as string[];
  for (const forbidden of ["tests/", "plans/", "dist-test/", "node_modules/"]) {
    assert.equal(
      files.includes(forbidden),
      false,
      `${forbidden} must not be published`,
    );
  }
});
