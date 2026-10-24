# AGENTS.md

This is a StartOS service package: it builds a `.s9pk` for StartOS.

Develop it inside a StartOS packaging workspace created by `start-cli s9pk init-workspace`,
which provides the packaging guide and agent context one level up. The workspace marker
(`.startos/`) belongs in a parent of the repository, never inside it. If you are reading this
in a bare clone with no workspace, the full guide is at <https://docs.start9.com/packaging>.

**Start every task at the recipe index**, `../start-technologies/projects/start-sdk/docs/src/recipes.md`
(or <https://docs.start9.com/packaging/recipes.html>). It maps an intent to the constructs, the
reference pages and a named production package to copy. The recipe outranks any neighbouring package.

Keep `README.md` (technical reference for an AI support or administering agent) and
`instructions.md` (end-user docs, packed into the s9pk) in sync with your changes.

## This repo

- **The package lives inside the Quill repository** (`deploy/startos/`), which is a fork of
  <https://github.com/humanitas-labs/quill>. The image is built from `../../server` with
  `../../server/Dockerfile`. Do not edit `server/`, `skills/`, `macos/`, `windows/` or `linux/`
  from a packaging change.
- **The contract is `docs/server-spec.md`.** Section 2 lists the env vars `main.ts` composes,
  section 3 the models, section 10 this package. Change the spec first when a contract changes.
- **The version is `startos/versions/current.ts`.** Bump it on every change that should reach
  users: the release workflow refuses to replace an existing release.
- **Secrets live in `store.json`** on the `main` volume (`adminPassword`, `apiKey`,
  `sessionSecret`, `internalToken`). `main.ts` reads the whole store with `.const()`, so every
  action that writes it restarts Quill.
- **The `models` volume is not backed up** on purpose. Models are re-downloaded and checked by SHA-256.
- **Every user-facing string goes through `i18n()`** with all five locales. The dictionaries are
  indexed by number and `tsc` fails on a string that is missing from `default.ts`.
- CI is the build path: `.github/workflows/startos.yml` at the repository root.
