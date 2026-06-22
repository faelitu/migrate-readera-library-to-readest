# Migrate ReadEra's library data to Readest

Python script to migrate your books' progress and groups from ReadEra's library to Readest's.

It also migrates highlights (ReadEra *citations* → Readest `annotation` booknotes)
for both EPUB and PDF books.

## PDF highlights (one-time setup)

PDF highlights are anchored against the text layer that Readest renders with
pdf.js, which differs from ReadEra's own text extraction. To generate matching
CFIs, the migration uses a small Node helper that runs the same pdf.js engine.

Before migrating PDFs, install its dependencies once:

```bash
cd tools/pdf_cfi
npm install
```

This requires Node.js. If Node or the dependencies are missing, EPUB migration
still works and PDF highlights are skipped (and reported), leaving the rest of
the migration intact.
