# Archive path admission R0.1 (issue #141)

This opt-in, **read-only** stdlib utility validates untrusted member names
before a sealed archive manifest is used. It does not read archive bytes,
modify canonical Noosphere journals, authorize a restore, or attest contents.

## API

`validate_member_path(path)` accepts one exact built-in string containing a
relative POSIX member name. Ill-formed Unicode (including unpaired surrogates)
is rejected as `ArchivePathError`; UTF-8 encoding errors never escape the API.
`admit_member_paths(iterable, max_members=4096)` returns a tuple, preserving
manifest order. The input is consumed once. Each UTF-8 name is limited to
1024 bytes, each component to 255 bytes. The manifest is limited to 4096
entries. Unsafe names cause `ArchivePathError`, never silent rewriting.

Rejected: absolute/drive/UNC paths, backslashes, dot/dotdot components,
empty components, controls/NUL, trailing dots/spaces, colons (including NTFS
alternate data streams), Windows DOS device basenames (`CON`, `PRN`, `AUX`,
`NUL`, `CONIN# Archive path admission R0.1 (issue #141)

This opt-in, **read-only** stdlib utility validates untrusted member names
before a sealed archive manifest is used. It does not read archive bytes,
modify canonical Noosphere journals, authorize a restore, or attest contents.

## API

`validate_member_path(path)` accepts one exact built-in string containing a
relative POSIX member name. Ill-formed Unicode (including unpaired surrogates)
is rejected as `ArchivePathError`; UTF-8 encoding errors never escape the API.
`admit_member_paths(iterable, max_members=4096)` returns a tuple, preserving
manifest order. The input is consumed once. Each UTF-8 name is limited to
1024 bytes, each component to 255 bytes. The manifest is limited to 4096
entries. Unsafe names cause `ArchivePathError`, never silent rewriting.

Rejected: absolute/drive/UNC paths, backslashes, dot/dotdot components,
empty components, controls/NUL, trailing dots/spaces, colons (including NTFS
alternate data streams), Windows DOS device basenames (`CON`, `PRN`, `AUX`,
, `CONOUT# Archive path admission R0.1 (issue #141)

This opt-in, **read-only** stdlib utility validates untrusted member names
before a sealed archive manifest is used. It does not read archive bytes,
modify canonical Noosphere journals, authorize a restore, or attest contents.

## API

`validate_member_path(path)` accepts one exact built-in string containing a
relative POSIX member name. Ill-formed Unicode (including unpaired surrogates)
is rejected as `ArchivePathError`; UTF-8 encoding errors never escape the API.
`admit_member_paths(iterable, max_members=4096)` returns a tuple, preserving
manifest order. The input is consumed once. Each UTF-8 name is limited to
1024 bytes, each component to 255 bytes. The manifest is limited to 4096
entries. Unsafe names cause `ArchivePathError`, never silent rewriting.

Rejected: absolute/drive/UNC paths, backslashes, dot/dotdot components,
empty components, controls/NUL, trailing dots/spaces, colons (including NTFS
alternate data streams), Windows DOS device basenames (`CON`, `PRN`, `AUX`,
, `COM1`–`COM9`, `LPT1`–`LPT9`, including extensions and superscript
COM/LPT digits), duplicate names, casefold-equivalent names, and file/directory
prefix collisions. Device-name checks apply to every path component.

## Security boundary

**Not a safe extractor.** String validation cannot prevent symlink races,
hard-link aliases, mount traversal, filesystem Unicode-normalization
collisions or TOCTOU attacks. The eventual archive reader must use a
private extraction root and platform-appropriate descriptor-relative
no-follow operations; it must verify content digests and authenticated
roots independently. No real PRIVATE data or network services are used.

No mandatory dependency or runtime integration. This deliberately does
not change existing archive serialization, schema or restore policy.

## Test

`PYTHONPATH=src python -m unittest discover -s tests -p 'test_archive_path_admission.py' -v`

Expected: ten hermetic tests; CI and independent QA must verify exact PR
head before merge. This submission does not claim a complete #141 fix.
