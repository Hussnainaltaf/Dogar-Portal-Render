# Dogar Portal.Com — Deployment Handoff

**Author:** Manus AI  
**Verification date:** 2026-08-21  
**Production URL:** [https://dogar-portal-com.onrender.com](https://dogar-portal-com.onrender.com)

## Delivered implementation

The uploaded release was imported into a new GitHub deployment mirror and extended with tenant-aware branding. The Settings screen now exposes a dedicated Branding tab backed by current-tenant GET/PUT and logo-upload endpoints. Branding updates persist against the control tenant row while authorization remains tenant-scoped. The shared Logo and authenticated shell surfaces consume the same tenant logo, colors, font, office name, and live update event, so branding remains consistent across route transitions and shared navigation surfaces.

The authorization regression coverage now verifies that a recruitment office cannot read, update, or print another office’s candidates or demand files. The tests cover candidate detail, candidate update, candidate print, demand detail, demand update, and demand print paths, plus branding upsert and logo upload isolation.

## Verification results

| Check | Result |
|---|---|
| Ruff | Pass |
| OpenAI demo syntax compilation | Pass |
| Frontend production build | Pass; Vite build completed successfully |
| Tenant branding contract tests | Pass |
| Tenant isolation regression tests | Pass |
| Render `/api/health` | HTTP 200; `status=ok`, `db=ok` |
| Render `/api/ping` | HTTP 200; `{"ok":true}` |
| Render `/` | HTTP 200; SPA served with CSP and CSRF cookie |
| OpenAI live smoke test | Reached the Responses API but returned HTTP 429 `insufficient_quota` / `credit_balance_exhausted`; the demo reports this safely as JSON without printing the API key |

## GitHub repositories

The original fresh source repository is private: [Hussnainaltaf/Dogar-Portal.Com](https://github.com/Hussnainaltaf/Dogar-Portal.Com). Because Render’s connected GitHub App was attached to a different GitHub identity, a public Render-accessible mirror was created for deployment: [Hussnainaltaf/Dogar-Portal-Render](https://github.com/Hussnainaltaf/Dogar-Portal-Render). The deployed commit is `ca04850` (`Configure production vendor admin seeding`). No SQL backup, database file, API key, or generated credential was committed.

## Render deployment

Render Blueprint `dogar-portal-free` created the free Docker web service `dogar-portal-com`. The service is live at the production URL above. Free Render instances can spin down during inactivity and therefore may add a cold-start delay; this is shown in the Render dashboard and documented by Render [1]. The free service has no persistent disk, so it is suitable for a code/demo deployment but not a durable production database or uploaded-file store.

The production environment includes generated bootstrap credentials and the required vendor-admin variables. The generated passwords were entered directly into Render and are not stored in GitHub or this document. The service reports password reset as unavailable because SMTP is not configured; configure SMTP in Render before relying on self-service password reset.

## Uploaded real-data backup

The repository now includes [`scripts/migrate_staged_database.py`](../scripts/migrate_staged_database.py) and [`docs/MIGRATE_STAGED_DATABASE.md`](MIGRATE_STAGED_DATABASE.md). The command is dry-run by default, requires an explicit `--apply` flag, performs conflict-safe inserts, and verifies source/target row counts. It must be run against durable storage rather than the free Render filesystem.

The uploaded file `/home/ubuntu/upload/backup-29-07-2026.sql.gz` is plain UTF-8 SQL despite its `.gz` suffix. It was not decompressed or executed blindly. The repository’s existing idempotent legacy importer staged it in an isolated local database at `/home/ubuntu/work/data-staging/dogar_trading.db`.

| Staged legacy data | Imported count |
|---|---:|
| Candidates | 2,705 |
| Clients | 1,249 |
| Demands | 2,298 |
| Job categories | 2,412 |
| Candidate assignments | 2,579 |
| Cities | 38 |
| Visa categories | 191 |
| Medical centers | 37 |
| Embassies | 4 |
| Contacts | 10 |

Legacy user password hashes were intentionally not imported; portal bootstrap authentication remains separate. The staged database was **not copied to Render**, because the chosen free service has no persistent disk and any local SQLite data could be lost on service replacement or restart. A durable migration should use managed PostgreSQL or a paid persistent disk, run the existing migration/import pipeline, verify row-count parity, rotate bootstrap credentials, and configure backups before real users are directed to the service.

## OpenAI demo

The repository includes [`scripts/openai_demo.py`](../scripts/openai_demo.py) and [`docs/OPENAI_DEMO.md`](OPENAI_DEMO.md). The demo covers a normal Responses API request, strict Structured Outputs using JSON Schema, and permission-aware function calling. Run it locally with:

```bash
export OPENAI_API_KEY='your-api-key'
export OPENAI_MODEL='gpt-5'
.venv/bin/python scripts/openai_demo.py --mode text
.venv/bin/python scripts/openai_demo.py --mode structured
.venv/bin/python scripts/openai_demo.py --mode tools
```

The main capabilities relevant to this portal are text and image inputs, text outputs, stateful interactions, built-in tools, application function calling, Structured Outputs, vision, and audio/speech workflows [2] [3] [4] [5] [6]. The model must never be treated as the authorization layer: candidate, demand, document, and tenant access must continue to be enforced by the portal API before any model tool is executed.

## References

[1]: https://render.com/docs/free "Render free instance documentation"

[2]: https://developers.openai.com/api/reference/responses/overview/ "OpenAI Responses API overview"

[3]: https://developers.openai.com/api/docs/guides/structured-outputs "OpenAI Structured Outputs guide"

[4]: https://developers.openai.com/api/docs/guides/function-calling "OpenAI Function Calling guide"

[5]: https://developers.openai.com/api/docs/guides/images-vision "OpenAI Images and Vision guide"

[6]: https://developers.openai.com/api/docs/guides/audio "OpenAI Audio and Speech guide"
