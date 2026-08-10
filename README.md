# Gift Shop Catalog Service

A messy CSV export turned into five REST operations an AI agent can call, with an
OpenAPI 3.0 specification the indigo.ai platform imports directly to create its tools.

**Live service** — `https://catalog-service-566410667338.europe-west1.run.app`
**Specification** — [`/openapi.json`](https://catalog-service-566410667338.europe-west1.run.app/openapi.json) · browsable at [`/docs`](https://catalog-service-566410667338.europe-west1.run.app/docs)
**Agent test page** — `https://clair.platform.indigo.ai/chatbot/5f1a19fb-2334-4907-b3a6-f87b6f58d205`

> <!-- TODO(Isaac): fill in once the video is recorded -->
> **Video walkthrough** — _link to follow_

---

## Part 1 — How I work with AI

> <!-- TODO(Isaac): these three are yours to write. Keep the whole section to one page. -->
> <!-- Brief and honest beats polished — that is what they asked for. -->
> <!-- STUDY-GUIDE.md §3.2 has a genuine, usable answer for question 2 if you want it: -->
> <!-- the info.title bug, where a generic error message got re-interpreted three times -->
> <!-- instead of running a validator. It is a real story about building something a -->
> <!-- model consumes, which is exactly what the question asks for. -->

### My workflow

_TODO_

### When it went wrong

_TODO_

### In the room

_TODO_

---

## Part 2 — The build

### Try it

```bash
TOKEN=<your token>
BASE=https://catalog-service-566410667338.europe-west1.run.app

curl -s "$BASE/health"                                          # public, no auth
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/categories"
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/products/search?query=chef%20knife&max_price_eur=100"
```

Run it locally:

```bash
uv sync --dev
cp .env.example .env                # then set CATALOG_API_TOKEN
uv run pytest                       # 113 tests
uv run uvicorn app.main:app --reload --port 8080
```

### Architecture

```
data/gift-shop-catalog.csv   152 rows, one export, assumed hostile
   │
   ├─ app/ingest.py          validating pipeline: coerce, quarantine, report
   │                         → in-memory Catalog (no database)
   ├─ app/search.py          pure functions: filter, rank, similar, budget
   ├─ app/main.py            FastAPI · 5 tool endpoints (bearer auth) · /p/ product page (public)
   ├─ app/product_page.py    renders the /p/ page from the same get_product_details data
   ├─ app/placeholder_image.py  generates the /p/.../image.svg placeholder art
   └─ app/openapi_compat.py  post-processes the spec into genuine OpenAPI 3.0
```

**FastAPI + Pydantic, stdlib `csv`, no database, no pandas, no embeddings.**

FastAPI because the OpenAPI document *is* the deliverable: `Field(description=...)`
and `operation_id` are how the tool definitions get written, and they cannot drift
from the implementation. 152 read-only rows load in milliseconds — pandas would add
~50 MB to the image and a slower cold start for a groupby that is five lines of
stdlib. A vector index would add a dependency, a cold-start cost, and a ranking that
cannot be explained line by line to a client. At roughly 50,000 products that trade
flips; at 152 it does not.

**The platform owns the agentic loop; this service owns the tools.** The reasoning
could have been hidden behind a single `recommend_gift` endpoint. It is not, because
the client's team edits the prompt on the platform rather than in this repo — and
because the thing being evaluated is whether a model can act correctly on the
specification alone.

### The data, and why the loader is paranoid

The export is one snapshot from a system nobody documented. What is actually wrong
with it:

| Issue | Detail | Handling |
|---|---|---|
| Category variants | 17 raw spellings → **11 canonical**: `home & living`, `Home and Living`, `' Home & Living'` (leading space), `Tech and Gadgets`… | Fold on strip → casefold → `" and "` → `" & "`, then pick the best-cased variant the data itself uses |
| Cross-listed duplicates | `Herb Garden Kit` is both HL-021 and KD-024; `Amber Glass Tumbler Set` is both HL-024 and KD-023 | Both ids stay resolvable; results collapse to one per group, and the survivor carries `also_in_categories` |
| Out of stock | 11 products | Excluded from search by default |
| Missing ratings | 5 products | **Key omitted entirely** — never `null`, never `0` |
| Missing occasion / colour / material | 3 / 2 / 2 | Keys omitted |
| Thin descriptions | `"A card."` (7 characters) | Nothing to justify a recommendation with. Not padded, not invented |
| Price spread | €6.50 → €899 | "Budget too low" is a live conversation path, not a hypothetical |
| Gift cards | stock 999, 0 shipping days, no rating, no material | Break every assumption. Also the honest fallback when nothing else fits |

**This particular file is clean** — no ragged rows, no unparseable numbers, no
encoding damage. That is a property of one export, not of the system that produced it.
So the loader treats every field as hostile regardless:

- no row can crash startup — bad rows are **quarantined with a reason and counted**
- every scalar is coerced defensively: `parse_money` handles `€1.234,56` and
  `1,234.56`; unreadable stock becomes `"unknown"`, which is **not** the same as
  `"out_of_stock"`
- every string is capped at ingest, whether or not today's data needs it
- **the filter vocabularies are derived from the data**, so a new `occasion` value in
  the next export becomes a legal filter instead of silently breaking search
- startup emits a data-quality report; `/health` exposes the counts

`tests/fixtures/nasty-catalog.csv` is a hand-written file of horrors — ragged rows,
`stock = "N/A"`, `rating = 9.5`, duplicate ids, HTML, a 2,000-character description,
BOM, CRLF, comma-separated tags. It caught two real bugs the clean export never
would have.

**The export contains no images or product URLs.** URLs turned out cheap to add for
real: `/p/{product_id}` is a small, unauthenticated HTML route on this same service
that renders `get_product_details` for a human instead of a model, so `product_url` is
computed at request time, not stored, and can never disagree with what the agent already
said.

Images stayed cut in the sense that matters: nothing pretends to be a photo of a
product we've never seen. Once testing showed the widget renders markdown images
inline, a single placeholder repeated across every product would have made that
obvious — two different recommendations in one reply showing the identical picture
reads as broken, not MVP. So `image_url` points at a generated SVG
(`app/placeholder_image.py`, served from `/p/{product_id}/image.svg`): a colour per
category, a monogram from the product's name. It's honestly a placeholder, not
invented photography — the distinction that matters, same as never inventing a price
or a policy. Real photography is still the first thing I would ask the client for.

### The tools

Three were specified. There are five, because three do not cover the conversations
the brief describes.

| Tool | Purpose |
|---|---|
| `get_categories` | Categories with counts, price ranges, subcategories — **and `filter_vocabulary`**, the exact strings the other tools accept |
| `search_products` | The workhorse: free text, budget, occasion, recipient, category; filtered and ranked server-side |
| `get_products_by_category` | Browsing, paginated. Its description explicitly says to prefer `search_products` |
| `get_product_details` | Full record. **Automatically includes two in-stock alternatives when the product is out of stock** |
| `find_similar_products` | Alternatives, optionally under a price cap |

**`get_categories` returns the vocabulary because the model cannot guess it.** This
catalogue says `her`, not `sister`; `housewarming`, not `new home`. Without one cheap
bootstrap call, every filter silently returns nothing and the agent concludes the shop
is empty. The response also carries price ranges, so the agent can tell someone with
€30 that Jewellery starts at €54 without making a second call.

**`find_similar_products` exists because two of the six scenarios are conversation
states, not searches** — "the thing they liked is over budget" and "the obvious
recommendation is out of stock". Giving the model one call that resolves each is more
reliable than hoping it composes a clever search.

**Ranking is a transparent weighted sum**, not a black box:

```
+3.0  per query token matching the product NAME
+2.0  per query token matching a TAG or SUBCATEGORY
+1.0  per query token matching anything else
+2.0  occasion matches
+1.5  recipient matches exactly (never via the catch-all "anyone")
+1.0  price sits at 70–100% of the stated budget
-1.0  stock is low
+0.5 × normalised popularity     (rating × log10(reviews + 10))
```

`anyone` is excluded from the recipient bonus deliberately: 90 of 152 products carry
it, so rewarding that match would be noise wearing a signal's clothes.

**Category is a query parameter, not a path segment.** `Home & Living` contains an
ampersand, and path-encoding it in a URL assembled by a model is a footgun for no
benefit.

### The response shape

One envelope, identical across every operation that returns products:

```json
{
  "status": "ok",
  "total_matches": 23,
  "returned": 3,
  "filters_applied": {"category": "Home & Living", "max_price_eur": 50.0,
                      "include_out_of_stock": false},
  "products": [ /* ~55 tokens each */ ],
  "notes": ["12 more products match. Narrow with occasion, recipient or a tighter budget.",
            "2 matching product(s) hidden because they are out of stock."]
}
```

**`filters_applied` echoes resolved values**, including defaults and any corrected
category spelling, so the agent can tell the shopper what it actually searched — and a
wrong guess becomes visible instead of silent.

**`notes` is a model-facing instruction channel inside the payload.** This is the
answer to "a category with forty products is a problem you have to solve": return
eight, say how many more exist, and say how to narrow. Language models follow these
reliably.

**A key whose value is unknown is never emitted.** A model handed `"rating": null`
will tell a shopper the product is rated zero out of five. Absence is unambiguous in a
way that `null` and `0` are not.

Two tiers keep list responses small: a **summary** (11 fields, ~65 tokens) for lists,
and a **detail** (adds brand, colour, material, tags, occasions, exact stock) only
when the agent asks about one product. `category` and `subcategory` were cut from the
summary: once a search has already resolved to a category, repeating it on every
product buys nothing and only hands the model more raw English catalogue vocabulary
it might echo instead of translate. `product_url` and `image_url` (see below) are
computed rather than read straight off the product.

### Errors the agent can recover from

> **401 is the only non-2xx this service returns.** Every other state comes back
> **200** with a `status` field and a `message` written to be read aloud.

The reasoning: we do not control how the consuming platform serialises a non-2xx
response into the model's context. Several importers surface only "tool call failed"
and discard the body — which throws away exactly the information the agent needs to
recover. A 404 the model never sees is worth less than a 200 it can act on.
Authentication is the exception, because a missing token is a deployment problem, not
a conversation the agent can rescue.

| Condition | `status` | The response carries |
|---|---|---|
| Unknown category | `unknown_category` | `did_you_mean` plus every real category |
| Unknown product id | `not_found` | `did_you_mean` from a name search |
| Nothing matched | `no_match` | a `reason`, the cheapest in-scope product, and concrete `relax` suggestions |
| Bad enum or range | `invalid_parameter` | the parameter, and the allowed values |

Worked example — this beats an empty array every time:

```json
{"status": "no_match", "reason": "budget_too_low",
 "message": "Nothing in Jewellery fits that budget. The cheapest match is Leather Watch Strap 20mm at 68 EUR.",
 "suggestions": {
   "cheapest_in_scope": { "...": "a real, in-stock product" },
   "relax": ["raise max_price_eur to 68",
             "try category 'Books & Stationery', which starts at 6.5 EUR"]}}
```

Anything offered as a fallback is filtered to what can actually be bought. An early
version suggested the cheapest match without checking stock, and cheerfully offered a
sold-out bracelet — there is a regression test named after it.

### Response size budget

Sizing is done first by the `limit` defaults, the summary/detail split, and a
140-character cap on the one-line product `pitch`. A normal search response is around
4 KB.

`enforce_budget` is the backstop for input we have not seen: if a response exceeds
12 KB it degrades in a fixed order — shorten every pitch to 90 characters, drop the
lowest-signal fields, then drop products from the tail, never below one — and always
announces the trim in `notes`. `total_matches` is never modified, so the agent always
knows how much it did not see. Bytes rather than tokens, because a real token count
needs a tokeniser matched to a model we do not control.

### Authentication

`Authorization: Bearer <token>`, declared as an `HTTPBearer` security scheme and
compared with `secrets.compare_digest` (a normal `==` exits at the first wrong
character, which leaks the token to anyone who can measure response times).

The token comes from `CATALOG_API_TOKEN`; the service refuses to start without one of
at least 24 characters. Generate one with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**`/openapi.json`, `/docs` and `/health` are deliberately unauthenticated.** A
platform must be able to read the specification before it has anywhere to put a
credential.

Out of scope, and stated as such: rotation, per-client keys, request signing, rate
limiting.

### Deployment

Google Cloud Run, `europe-west1` — chosen because indigo.ai is Italian and tool-call
latency is part of the user experience. Warm responses are around 5 ms; scale-to-zero
cold starts are 1–2 seconds.

```bash
gcloud run deploy catalog-service --source . --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars CATALOG_API_TOKEN="$(cat .token)" \
  --memory 512Mi --cpu 1 --max-instances 3
```

`--allow-unauthenticated` is required: it governs Google's IAM layer, which would
otherwise return 403 before this service ever runs. The bearer token is the actual
gate, one layer further in.

### Tests

```bash
uv run pytest        # 113 tests
```

| File | Covers |
|---|---|
| `test_ingest.py` | The hostile fixture: quarantine reasons, coercion, caps, ragged rows, encoding |
| `test_search.py` | The six brief scenarios at the tool layer: budget too low, out of stock, nothing suitable, fuzzy category, deduplication, limits |
| `test_endpoints.py` | Auth, the public spec, **OpenAPI validity**, and spec quality — every operation has a description, every parameter is described, no response contains `null` |

The specification is validated with `openapi-spec-validator` in the test suite rather
than by eye. That is a direct consequence of a mistake — see below.

---

## Conversation design

> <!-- TODO(Sonnet): fill in once the Agent Block sections are final. -->
> <!-- Required by the brief: the prompt as written, how the tools were bound, and -->
> <!-- especially how many questions it asks before recommending, and why. -->
> <!-- The prompt sections are in HANDOFF.md §3. -->

**Question budget: at most two before the first recommendation, and none at all if the
shopper has already given a constraint.** "A chef's knife under a hundred euros" gets
knives, not a questionnaire. "I need a gift" gets one question that does the most work
— who it is for and what the occasion is, in a single sentence. Refining after a
recommendation is cheap and welcome; interrogating before one is why people leave.

**Message format**, designed against the platform's own Card Block limits (title 55
characters, description 85) so it fits a narrow column whether rendered as text or as
cards: two products per message, three only when comparing; name and price, one line
of why tied to what the shopper said, one line of logistics; under about 80 words;
no tables, no nested bullets; exactly one next step.

---

## What this build found about the platform

Genuine integration findings, since they cost real time and would cost the next
person the same:

**The importer accepts specifications its own model runtime rejects.** The tool
collection validated a document containing `allOf`; the configured Gemini model then
failed to build function declarations from it and the agent errored before making any
HTTP call. OpenAPI 3.0 forbids sibling keys next to `$ref`, so nullable references
*must* be wrapped in `allOf` — and the function-calling runtime rejects exactly that.
The two consumers have contradictory requirements. **Resolution: importer conformance
wins, and the model gets swapped.** A specification that will not import is worth
nothing; a model that dislikes `allOf` can be changed in one click.

**The importer inlines every `$ref`**, so schema reuse multiplies rather than saves —
one schema referenced five times became twenty inlined copies and 72 KB. Collapsing
the deep recovery-payload schemas halved it.

**Agents are invisible without a trigger.** Without one, messages fall through to the
platform's toolless General Agent, which answers fluently and entirely from
imagination — in our case inventing fourteen categories and eighty subcategories that
do not exist. That failure mode looks like success, which makes it the dangerous one.

**And a mistake of my own**, since it is the most instructive thing here: the
post-processing stripped `title` from every object in the document to satisfy the
model runtime — including `info.title`, which OpenAPI requires. The result was an
invalid document, and the importer reports every validation failure with the same
sentence. I re-interpreted that one message three times before running a validator,
which named the cause in ninety seconds. The fix is not a resolution to be more
careful; it is `test_spec_is_valid_openapi`.

---

## What was deliberately left out

Vector search and embeddings (152 products) · a database (read-only, loads in
milliseconds) · write operations, cart and checkout · rate limiting and key rotation ·
per-user personalisation or memory beyond the conversation · LLM-generated product
summaries — designed and documented, deliberately not built, because the export's
descriptions are already short enough and the truncation guard covers the case ·
real product photography, which the export does not contain and which will not be
invented — `image_url` is a generated placeholder, not a substitute for it.

## Time spent

> <!-- TODO(Isaac): the brief asks roughly how long, and what you left out. -->
> <!-- Day 1 was roughly N hours, of which a large share went on the platform -->
> <!-- integration rather than the service. Worth saying plainly — it is the honest -->
> <!-- shape of forward-deployed work. -->

_TODO_

## Repository layout

```
app/
  config.py          environment, fails fast on a missing token
  ingest.py          the validating pipeline
  models.py          Pydantic response models, every field described
  search.py          filtering, ranking, similarity, response budget
  errors.py          recoverable-response builders
  auth.py            bearer dependency
  openapi_compat.py  OpenAPI 3.0 conformance and tool-runtime compatibility
  main.py            five routes, thin
data/                the catalogue export
scripts/             export_openapi.py — regenerates the committed spec
tests/               113 tests, including a fixture of deliberately broken CSV
openapi.json         generated snapshot; a test fails if it drifts from the service
```
