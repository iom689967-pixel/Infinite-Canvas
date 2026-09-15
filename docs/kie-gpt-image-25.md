# Kie GPT Image 2.5 integration

This extends the local Infinite-Canvas Kie adapter. It does not introduce a new
provider, credential store, upload service, queue or polling implementation.

## Model contract

| UI model | Without references | With references |
| --- | --- | --- |
| `gpt-image-2.5-flare` | `gpt-image-2-5-flare-text-to-image` | `gpt-image-2-5-flare-image-to-image` |
| `gpt-image-2.5-sunburst` | `gpt-image-2-5-sunburst-text-to-image` | `gpt-image-2-5-sunburst-image-to-image` |

Only the two UI identities are accepted from the browser. The existing backend
selects a Market route from the normalized reference list. Image routes use
`input.input_urls`; text routes omit that field. `background` defaults to `auto`;
the adapter also accepts `opaque` and `transparent` for future UI use.

Both variants allow 16 ordered reference images and 1K/2K/4K. Their ratio list is
`auto, 1:1, 3:2, 2:3, 16:9, 9:16, 4:3, 3:4, 21:9, 27:16, 16:27, 9:8, 8:9`.
The last four ratios require 1K. All three entrypoints consume the existing
`/api/image-params` capability schema and disable invalid combinations. Backend
validation runs before reference preparation, so invalid requests do not upload.

Official API examples:
- https://docs.kie.ai/market/gpt/gpt-image-2-5-flare-image-to-image
- https://docs.kie.ai/market/gpt/gpt-image-2-5-sunburst-text-to-image

## Reused application paths

- `providers/kie/models.py`: route map and capability schema, alongside GPT Image 2
  and Nano Banana Pro; no change to their model identities or request contracts.
- `providers/kie/uploads.py`: original normalization, source fingerprint,
  in-flight upload lock, cache and temporary-URL expiry checks. Reference order
  and cache keys are unchanged, including reuse when switching between variants.
- `providers/kie/client.py` and `tasks.py`: one createTask, then polling that task
  through waiting/queuing/generating/success/fail, including cancellation.
- `main.py`: existing Canvas queue, result parser, image downloader, dimension
  extraction and history writer. Kie downloads must now save a verified image;
  a temporary remote URL or an HTTP 200 non-image body cannot count as completion.
  Download failure retains the upstream task reference for the existing query
  path; it does not automatically create another task.
- Smart Canvas keeps its existing runSettings, node serialization, cloning,
  reference sources, edit/regenerate semantics and generation history. Its
  capability request is now awaited when a saved node runs immediately on load.
- Normal Canvas and online image generation use the same small schema helper,
  with proper labels, explicit ratio/resolution and model-aware reference limits.
  Online history reuse retains the model, parameters and up to 16 references.

## Validation

Local baseline: `88f21f8a6bc7ebcd28d7063811d191a629e6680e`, 382 tests.
With this integration: 399 tests passed using `./scripts/check.sh`;
`python -m pip check` reported no broken requirements.

The 17 additional tests cover four route payloads, every ratio/resolution
combination, reference ordering/limits, background, local rejection before upload,
gzip upload responses, reuse across variants, polling, queue completion,
cancellation, original local dimensions and history, no create retry, download
failures, invalid image bodies, frontend controls and persisted node models.
HTTP tests use `httpx.MockTransport`, with no real provider calls or credentials.

Browser acceptance used the real Smart Canvas, normal Canvas and online HTML/JS
against a disposable loopback mock API. It verified both model labels, 1K-only
controls, generation and preview, save/reload, Smart Canvas duplication and
continue-edit routing, and online history reuse of 16 ordered references.
These are mock acceptance results, not a claim of a successful paid Kie task.
