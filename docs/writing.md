# Writing documents in this repository

> Purpose: say what each document is for, and how to write it so a reader who learned English as a second language understands it the first time.

## Why this exists

The unit gives no error for a wrong write, so the written record is the only
trail of what is known. A record nobody can read is no record. These rules keep
the documents short and plain, and `tests/test_writing.py` checks the parts a
test can check.

## One purpose per document

Every document opens with one line after its title:

```
> Purpose: <what this document is for>.
```

At most 40 words. It says what the document is for. It does not say what the
document is not for. A purpose that grows into "X, and not Y, and never Z" has
stopped being a purpose, so the test rejects the common negations (`not`,
`never`, `no`, `cannot`, `nor`, `neither`, `except`, `nothing`, and any `n't`)
in that line. When
something does not fit, put it where it fits (next table) and link to it.

| document | purpose |
|---|---|
| `README.md` | get a user from `pip install` to a first working script |
| `CLAUDE.md` | the rules an agent follows here, one bullet each, pointing at the document that holds the detail |
| `docs/STEERING.md` | what the system is and why it is shaped this way |
| `docs/ADR.md` | each decision, with the options that were rejected and why |
| `docs/architecture.md` | how the code is layered, and how to add an operation or a profile |
| `docs/api.md` | what a caller can do with the protocol layer, grouped by what it touches |
| `docs/protocol.md` | what is known about the wire, per profile, with an evidence tag on each fact |
| `docs/domain-model.md` | the design of the model layer: built, planned, and left out with the reason |
| `docs/manual-coverage.md` | each feature of the manual against what the library covers |
| `docs/capture.md` | how to observe the unit's own traffic and read what arrives |
| `docs/roadmap.md` | where the library is going, and the dead ends already found |
| `docs/migration.md` | what breaks between versions and what to change |
| `docs/releasing.md` | the release checklist |
| `docs/troubleshooting.md` | symptoms and fixes |
| `docs/writing.md` | this file |
| `changelog.md` | what changed for someone who installs the package, per version |
| `contributing.md` | how to contribute, including the pull request rules |
| `tests/hardware/readme.md` | how to run the hardware suite and what it promises |
| `examples/readme.md` | what each example does |

## What goes where

| you are writing | put it in |
|---|---|
| a rule an agent or contributor must follow | `CLAUDE.md`, one bullet, linking the detail |
| what the system is, and why it has this shape | `docs/STEERING.md` |
| a decision, with the options rejected | `docs/ADR.md` |
| a fact about the wire, with its evidence tag | `docs/protocol.md` |
| the model layer's design, or a catalog attribute we cannot explain | `docs/domain-model.md` |
| a method for observing the unit | `docs/capture.md` |
| how the code is laid out, or how to add to it | `docs/architecture.md` |
| what a caller can do | `docs/api.md` |
| what changed for a user, per version | `changelog.md` |
| what breaks between versions | `docs/migration.md` |
| how a finding was reached, the failed attempts, the story | the commit message, the pull request, or the lab repository (`../quad-cortex/doc/`) |

## Style

Write for a reader who knows the domain and reads English as a second language.

- Short sentences. One idea each. About 20 words.
- Plain words. "Use", not "leverage". "Because", not "on the strength of".
- One word for one thing. The glossary below fixes the words.
- No capital letters for emphasis. A label the unit shows goes in backticks:
  `VOLUME`, `MODE`. A protobuf action goes in backticks: `READ`, `UPDATE`.
- Numbers that matter go in a table, not in a sentence.
- Bold marks a term being defined, the first words of a list item, or the lead
  sentence of a paragraph. Never a paragraph.
- Link to the one place a thing is explained. Do not restate it.

## Evidence, not narrative

A document records what is known about the unit. How we came to know it goes in
the commit message or the lab repository.

- A fact carries a short evidence tag: "Measured 2026-09-11 on CorOS 4.0.1",
  "Inferred from the schema", "Read on the screen". One clause, not a story.
- A correction replaces the wrong sentence. Do not leave the old sentence beside
  the new one.
- A document never talks about itself, its history, its reviewers or its
  drafts. Such sentences have no reader. The changelog is the one exception: an
  entry that withdraws a released claim names the entry it withdraws.
- A negative result says which question the instrument answered. "The unit does
  not announce X" and "X is not on the wire" are different claims.

## Phrases that mark a problem

The test rejects these phrases outside code blocks. Each one marks a sentence
written for a reviewer instead of a reader. Rewrite the sentence. This file is
the one place they may appear, because it lists them.

- `an earlier version of this`, `an earlier draft`, `used to say`,
  `this paragraph`, `this section used to`
- `worth stating`, `worth noting`, `worth keeping`, `worth recording`,
  `worth the sentence`
- `load-bearing`, `cuts the other way`, `stated rather than`,
  `rather than buried`, `the honest`, `honestly`

## Sizes the test enforces

| what | limit |
|---|---|
| the purpose line | 40 words, no negation |
| a bullet in `CLAUDE.md` | 60 words |
| a paragraph or list item in any governed document | 150 words |
| an entry in `docs/STEERING.md`'s change log | 80 words |

Governed documents are every `.md` file in the repository except
`code_of_conduct.md`, the issue and pull request forms under `.github/`, and the
released entries of `changelog.md`, which are a record of what was said at
release time. `docs/ADR.md` is exempt from the
paragraph limit only: a decided record is append-only and is not edited for
length. New records follow the limit.

## Glossary

The glossary governs prose. A code identifier keeps its own name:
`await_broadcast`, `set_scene_color`, `Device`.

| word | meaning | avoid |
|---|---|---|
| unit | the physical Quad Cortex | hardware, QC, the box |
| device | fine in names the code or the manual uses: `Device`, device profile, Device Settings | using it for the unit in ordinary prose |
| screen | the unit's touchscreen and what it draws | display, glass |
| wire | the bytes that cross the USB link | the protocol, when you mean the bytes |
| catalog | the `ModelRepo` XML the unit publishes | model repository |
| profile | one measured pair of `device_type` and CorOS version, as a client class | baseline |
| protocol layer | `pyquadcortex.protocol` | client layer, low level |
| model layer, the model | `pyquadcortex` and the code in `pyquadcortex/device/`; "the domain model" is the design's name in `docs/domain-model.md` and the ADRs | object model |
| block | a placed amp, pedal or other virtual device on the grid | model, in prose (`model` is the wire's word and the code's) |
| slot, column | the screen's word and the wire's word for a cell in a row | mixing them when writing about one layer |
| push | a message the unit sends without being asked; as a verb, the unit announces or pushes | broadcast, notification |
| read | a `READ` request and its answer | fetch, query |

## The change log in `docs/STEERING.md`

An entry has a dated heading and three lines: what changed, why, and scope
(updated, and deliberately not updated). Under 80 words. The story behind it
goes in the pull request.
