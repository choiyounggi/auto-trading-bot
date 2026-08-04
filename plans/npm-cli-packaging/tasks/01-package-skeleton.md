# Task 01: npm package skeleton with build and test scripts

## Objective
`npm run build` compiles `cli/**/*.ts` to `dist/`, `npm test` compiles and runs
`node --test` over the built test files and exits 0 with "0 tests" (no test
files exist yet). No runtime dependencies are declared.

## Wiki pages (read these first, only these)
- wiki/security/dependencies/supply-chain.md — use for: the add-vs-write rule
  (D2: zero runtime deps) and the commit-the-lockfile rule.

## Inputs
- Existing `/Users/choeyeong-gi/Desktop/workspace/auto-trading-bot/.gitignore`
- Decisions that bind you: D1 (Node ESM, bin → `dist/index.js`), D2 (zero
  runtime deps), D3 (`node:test`), D15 (published file set).

## Steps
1. Create `package.json` at the repo root with exactly:
   - `"name": "@younggichoi/kis-trader"`, `"version": "0.1.0"`,
     `"type": "module"`, `"license": "MIT"`, `"private": false`
   - `"publishConfig": { "access": "public" }`
   - `"description": "macOS CLI for a local LLM-driven Korea Investment (KIS) auto-trading engine — onboarding, Telegram, launchd jobs, doctor."`
   - `"repository": { "type": "git", "url": "git+https://github.com/choiyounggi/auto-trading-bot.git" }`
   - `"homepage": "https://github.com/choiyounggi/auto-trading-bot#readme"`
   - `"bugs": "https://github.com/choiyounggi/auto-trading-bot/issues"`
   - `"keywords": ["kis","한국투자증권","trading","auto-trading","stock","telegram","claude-code","codex","llm","launchd","cli"]`
   - `"bin": { "kis-trader": "dist/index.js" }`
   - `"engines": { "node": ">=20" }`, `"os": ["darwin"]`
   - `"files": ["dist/","src/","config/","schemas/","data/migrations/","pyproject.toml","README.md","LICENSE"]`
   - `"scripts"`: `"build": "tsc -p tsconfig.json"`,
     `"test": "tsc -p tsconfig.test.json && node --test \"dist-test/**/*.test.js\""`,
     `"prepublishOnly": "npm run build"`
     The **glob is required, not stylistic**: `node --test dist-test/` treats
     every file in the directory as a test file, so it executes non-test
     modules and counts each as a passing test — a test that cannot fail.
     (Plan repair, recorded during execution: verified on Node v25.8.1, the
     directory form reported `pass 1` for a placeholder module with no tests.)
   - `"devDependencies": { "typescript": "^5.6.0", "@types/node": "^22.0.0" }`
   - **No `dependencies` key at all.**
2. Create `tsconfig.json`: `target` `ES2022`, `module` `NodeNext`,
   `moduleResolution` `NodeNext`, `strict` true, `outDir` `dist`,
   `rootDir` `cli`, `declaration` false, `sourceMap` false,
   `include: ["cli/**/*.ts"]`, `exclude: ["cli/**/__tests__/**"]`.
3. Create `tsconfig.test.json`: `extends: "./tsconfig.json"`, `outDir`
   `dist-test`, `rootDir` `cli`, `include: ["cli/**/*.ts"]`, no `exclude`.
4. Create a **placeholder** `cli/index.ts` — task 12 replaces it wholesale.
   `tsc` fails with `TS18003: No inputs were found` when `cli/` holds no `.ts`
   file, so an empty directory cannot satisfy this task's own Verify. The
   placeholder is the smallest thing that compiles:
   ```ts
   #!/usr/bin/env node
   // Placeholder entry point — replaced by task 12 (cli-entry).
   console.log("kis-trader: CLI not yet wired up. See plans/npm-cli-packaging/.");
   ```
   (Plan repair, recorded during execution: the original step said
   `cli/.gitkeep`, which is incompatible with the Verify below.)
5. Append to `.gitignore`: `node_modules/`, `dist/`, `dist-test/`,
   `*.tgz`. Keep the existing content untouched.
6. Run `npm install` so `package-lock.json` is generated and committed
   (supply-chain rule 1).

## Deliverables
- `package.json`
- `tsconfig.json` and `tsconfig.test.json`
- `.gitignore` (modified), `cli/index.ts` (placeholder), `package-lock.json`

## Verify
- `npm run build` → exits 0 (no inputs yet is fine; `dist/` may be empty).
- `npm test` → exits 0 and prints a node:test summary with `# tests 0`.
- `node -e "const p=require('./package.json'); if(p.dependencies) { console.error('FAIL: runtime deps present'); process.exit(1) } console.log('OK zero deps')"` → prints `OK zero deps`.

## Out of scope
- Any `cli/*.ts` source file — later tasks create those.
- README, `.npmignore`, `npm pack` verification — task 16.
