# Contributing to RIBBON

This guide describes the team's responsibilities, contribution workflow, and roadmap. The API's JSON contract connects the workstreams so each contributor can make progress without waiting for every other component.

## Current project stage

The first implementation phase provides a verification engine, a REST API, SQLite history, and a working web interface. The prototype processes batches, streams page results, highlights OCR evidence, classifies discrepancies, and exports reports. Automated tests cover engine rules, API behavior, persistence, and the browser script.

The second phase added, in a first form:

- Sign-in with per-user audits, stored in the same local SQLite database.
- Matching slips to the reference by registration code, with missing and duplicate slips reported.
- Per-page timing, Tesseract confidence, and strategy agreement; parallel page reading.
- Recorded reviewer verdicts (real discrepancy or OCR misread) and a metrics view by resolution.
- A benchmark tool that measures accuracy against a ground truth, with synthetic sample generation.
- A **temporary** multi-user simulation for the thesis presentation, to be replaced by a real load test.

Shared database infrastructure, roles and permissions beyond ownership, administrator-managed accounts, and a ground truth for real batches remain future work.

## Goal of the next phase

**Make RIBBON collaborative.** This addresses the gap between the team platform described in the thesis and the current local prototype.

This phase combines shared data storage, API authentication, and user-specific screens. The tasks below include starting points that can be pursued independently.

## Team responsibilities

### 1. Engine and API — Felipe

Own `core/`, `api/`, and the data contract consumed by the other workstreams. Coordinate changes to storage integration with the database contributor.

- Implement check-digit validation for the payment slip's numeric line.
- Build a ground truth for a real batch and run `python -m tools.benchmark` at each resolution; choose the default DPI from those results.
- Replace the temporary simulation (`api/simulation.py`) with a load test run outside the application.
- Compare automated verification with manual checking to evaluate the thesis's overall objective.

When another workstream needs a new field or behavior, coordinate the contract with the engine/API owner before modifying these components.

### 2. Database — contributor to be assigned

The current database has two tables and stores audit history only.

- Plan migration to PostgreSQL or MySQL for the proposed shared deployment, consistent with the database architecture discussed in Chapter 2.
- Extend the current model (`app_user`, `session`, and `audit.owner`) with roles and shared access: who ran each audit and who may access its results.
- Persist the reference list, which is currently extracted for each run and discarded.
- Design queries for metrics by batch, operator, and time period. Distinguish agreement with the reference from measured OCR accuracy against reviewed data.
- Define retention and backup policies for taxpayer information.

**Starting point:** design the proposed schema and compare it with [`core/storage.py`](core/storage.py).

### 3. Front end — contributor to be assigned

The current interface uses plain JavaScript, with no framework or build step. It provides a foundation for the next phase.

- Extend the metrics view with conformity over time and batches with discrepancies.
- Extend the review workflow with comments and corrected values; the interface currently records only the verdict.
- Evaluate React, Vue, or continued use of plain JavaScript. Record the rationale for the thesis rather than treating a framework migration as a requirement.

**Starting point:** sketch the new screens and list the data each needs. Agree on the required API contract before implementing dependent integrations.

### 4. Documentation — contributor to be assigned

Maintain the repository guides and coordinate the thesis's technical writing. The existing thesis work plan includes:

- Complete the seven figures marked in the working document: four diagrams (use cases, sequence, classes, and architecture) and three screenshots.
- Verify the fourteen bibliography entries by opening and checking each source.
- Update the Word table of contents, apply proper captions to Table 1 and the item labeled Quadro 1, and fix the orphaned heading in section 1.3.
- Document the other workstreams as their implementations become available.
- Keep Chapter 3 aligned with the implemented system.

These items refer to the team's separate working thesis document, not files included with the repository. Reconfirm their status against that document.

**Starting point:** verify the bibliography before final layout work, since unverifiable references may require changes to the text.

## Development workflow

1. Follow the setup instructions in [README.md](README.md) and create your own virtual environment.
2. Create a branch for a focused change and coordinate ownership where needed.
3. Preserve existing route names, JSON fields, and report labels unless a contract change has been agreed with its consumers.
4. Run `python -m pytest` before submitting the change. Tests always use a temporary database.
5. Open a pull request describing the problem, resulting behavior, and validation.
6. Review and merge through a pull request rather than pushing directly to `main`.

Tests must pass before merging. For OCR or interface changes, also describe any manual validation performed with local inputs. Passing unit tests alone does not demonstrate recognition accuracy or correct browser behavior.

## Interface principle

> The reviewer must be able to see where the system obtained each field, not only the final result.

The annotated image makes it possible to inspect a discrepancy without reopening the original document. Preserve that connection between evidence and result when designing new screens. Where reliable coordinates are unavailable, communicate that limitation instead of implying that a field was visually located.

Historical reports currently contain no annotated images. Features requiring later access to those images need an explicit storage and retention design.

## Documentation conventions

- Write repository documentation in English.
- Use English for identifiers, comments, docstrings, API fields, and report labels. Legacy database literals in the migration layer are the compatibility exception.
- Write the web interface's visible text in Portuguese. The interface translates API values and error messages for display; add a translation in `static/app.js` when the API gains a message the interface shows.
- Separate implemented features from planned work.
- Update setup instructions and API documentation when behavior changes.
- State the dataset, resolution, method, and limitations behind accuracy or timing claims. The browser timer alone is not a controlled OCR benchmark; use `tools/benchmark.py`, and say when results come from synthetic slips.

## Local execution and data handling

Each contributor clones the project and runs it locally. Working PDFs contain confidential taxpayer information and are excluded from version control. Do not commit them or send them to external services. Use synthetic data for public examples, screenshots, and tests.

The collaborative phase should preserve this data-handling constraint using infrastructure controlled by the team. Sign-in now separates users' audits, but registration is open and there are no roles or HTTPS; permissions and retention must be designed before extending access beyond a trusted local network.
