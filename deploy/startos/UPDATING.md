# Updating

Quill is developed in this repository, a fork of [humanitas-labs/quill](https://github.com/humanitas-labs/quill). There is no pinned upstream image: the package builds `server/` from the same checkout, so the package version follows the server code.

## Tracking upstream

```sh
git remote add upstream https://github.com/humanitas-labs/quill.git   # once
git fetch upstream
git merge upstream/main
```

Then check the three places the server contract touches the package:

1. `docs/server-spec.md` section 2 (environment variables) against `startos/main.ts`. A new variable that the server reads belongs in `main.ts`, in `store.json.ts` and in an action when users should change it.
2. Section 3 (models) against the size quoted in `instructions.md` and in the health check text (about 490 MB).
3. Section 10 (the package contract) against `startos/manifest/index.ts`, `startos/interfaces.ts` and `startos/actions/`.

## Version bump rules

The version is `X.Y.Z:N` in `startos/versions/current.ts`. `X.Y.Z` is the version of the Quill server in `server/pyproject.toml`; `N` is the StartOS revision.

| Change | New version |
| --- | --- |
| Server version changed (any merge that changes `server/`) | `X.Y.Z:0` with the new server version |
| Package-only change (actions, health checks, docs shipped in the package) | same `X.Y.Z`, `N + 1` |
| Change to the shape of `store.json` that old files cannot satisfy | edit `current.ts` and add a migration; see below |

Edit `current.ts` in place and write the release notes in all five locales. Add a historical version file and an entry in `versions/index.ts` only when a migration must run through it. Every key in `store.json.ts` has a `.catch()` default, so adding a key needs no migration.

## Release

Merge to `release`. `.github/workflows/startos.yml` builds both architectures and publishes `quill_<version>_x86_64.s9pk` and `quill_<version>_aarch64.s9pk` as a GitHub Release tagged `startos-v<version>`. A second merge without a version bump fails instead of replacing the release.
