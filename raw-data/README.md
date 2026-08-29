# Local raw health-data workspace

This directory is for local health-data exports and their extracted files. Its
contents are sensitive and ignored by Git; only this handling guide is tracked.

A lot of the data here might not actually be used in the application yet (2026-08-29). - Lucas

Keep raw imports here instead of under `db/`. The `db/` directory is the live
MongoDB bind mount and should contain only files managed by MongoDB.

Suggested layout:

```text
raw-data/
  <source>/
    <export-date>/
      original-export.zip
      extracted files...
```

Rules:

- Never force-add raw health exports to Git.
- Do not copy credentials, tokens, or service-account files here.
- Treat extracted files as equally sensitive as their archive.
- Prefer aggregate findings in documentation and logs.
- Back up or delete this directory separately from the MongoDB `db/` backup.
