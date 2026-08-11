# Gift Shop Catalog Service

A messy CSV export turned into five operations an AI agent can call — over **REST**,
with an OpenAPI 3.0 specification the indigo.ai platform imports directly, or over
**MCP** at `/mcp`. Both are thin adapters over one implementation, so the transport is
the client's choice rather than a fork in the code.

**Live service** — `https://catalog-service-566410667338.europe-west1.run.app`
**Specification** — [`/openapi.json`](https://catalog-service-566410667338.europe-west1.run.app/openapi.json) · browsable at [`/docs`](https://catalog-service-566410667338.europe-west1.run.app/docs)
**Agent test page** — `https://clair.platform.indigo.ai/chatbot/5f1a19fb-2334-4907-b3a6-f87b6f58d205`

> <!-- TODO(Isaac): fill in once the video is recorded -->
> **Video walkthrough** — _link to follow_

---

## Part 1 — How I work with AI

### My workflow

They way I work with AI tools is pretty standardized regardless of the end goal; what does change is the depth I go into or the time put into
the planning stage of my workflow, but it remains the same. Whenever I am going to start working on something, the first thing I do is collect information
on what I will be doing (from conversations, problems I identified, a Spec, or simply an idea left in backlog) so that I can have a clear idea of what the
final deliverable will be. 

Once this is done and I have the necessary information to start, I then enter plan mode with AI. I do this by setting off a brainstorming session in which
I explain in lengthy detail what I want to do, what inputs and parameters I have, what outputs I expect, architectural decisions and requirements, things to 
check along the development path, and anything else I feel is important, and then let the agent (usually Opus from Claude) read everything and come up with
blindspots in my plan; alternatives to my proposed points; summary of which decisions make sense and which don't, and why; and I go over its list to be able
to answer back with my own personal comments, doubts, clarification points, technical decisions that don't match the business needs, an anything else that 
is relevant to the design. Once I'm happy with the state of the plan I then have Claude write a PLAN.md file that serves as the base for the development 
thereinafter. I go over the plan to re-check any drift from what I had in mind and when all is in place, I then switch to build mode.

Build mode is basically Claude running in auto mode (Sonnet for simple tasks, Opus if some part of the build requires deeper care) defined steps of the plan,
which I then check to make sure things are working as intended and that it looks good, as it's easier to review 4 files and a new feature rather than 25 files
and all features at the same time. In this stage is where problems always arise and the plan needs to adapt and evolve, but keeping the end goal as the North
star at project's end. Also in this stage is where I usually come up with new ideas and ways to improve what I first design, so it's both a build + plan iterative stage.

Once the build completes I use Claude once more to stress-test the product to find any cracks or edge cases we may come up with, and once it's behaving as intended and everything is in place I ship the final product.

### When it went wrong

The most recent example was at Duckbill, with one of the hardest projects I tackled: Personal Information Extraction from unstructured chats. A profile extraction pipeline I built had Opus doing the extraction and Sonnet verifying it, so no human in the loop here. The changes I made were supposed to drop the slop rate, and the evals numbers said they did, but I kept manually running into bad records that had passed verification. That mismatch is what told me something was off, and when I checked the drift between the evals result and what I was seeing in Prod I notices that Sonnet wasn't actually catching Opus's mistakes, it was just missing the same things, since they're close enough in training and lab to share blind spots.

What I do differently now: verification needs to come from an actually independent model, not just a different size of the same one, so I moved that step to GPT-5.5. And more broadly, I stopped assuming a decision was fine just because it seemed to be running and working. I check any decision point I didn't personally make, so I know where the pipeline can actually break instead of hoping it doesn't. I get the AI Agent to give me a list of decision points it took to evaluate possible drift points.

### In the room

Dealing with stakeholders wanting something done differently is an inherent part of the job and life itself. The way I've always dealt with this is by opening a good communication channel with the person in the room. Once I do this, I go over their perspective and their points and understand their complaints and why they think my proposal was incorrect, always making sure I'm getting it from the client's own perspective. Once this is settled I review their arguments and identify which ones are genuinely good and worth changing, which ones I think we could compromise on, and which ones I feel are wrong and why I feel that way, and start the second part of the communcation channel in which I present this to them.

The idea here is to be able to have a professional discussion on point in which I will cede, points in which I feel I'm right, and points in which I want to talk about more in depth to see how we could reach common ground. Sometimes a client will be impossible to convince otherwise and will just want everything as they say, in which case I don't lose temper or time arguing and just evaluate if what they want is feasible and change it. This is however my last resort, as I've found that an honest conversation tends to be productive from and for both sides.

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
uv run pytest                       # 170 tests
uv run uvicorn app.main:app --reload --port 8080
```

The same five operations over MCP:

```bash
curl -s -X POST "$BASE/mcp" -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Architecture

```
data/gift-shop-catalog.csv   152 rows, one export, assumed hostile
   │
   ├─ app/ingest.py          validating pipeline: coerce, quarantine, report
   │                         → in-memory Catalog (no database)
   ├─ app/search.py          pure functions: filter, rank, similar, budget
   │                         ↑ the single implementation both transports call
   ├─ app/main.py            FastAPI · 5 REST endpoints (bearer auth) · /p/ page (public)
   ├─ app/mcp_server.py      the same 5 operations as MCP tools, mounted at /mcp
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

**Queries match across word endings.** An agent picks whichever surface form the
shopper's phrasing suggests, and exact token matching made that a coin flip: `baking`
found the Bread Baking Set, `bake` returned `no_match` and the assistant apologised
for a €72 product we stock. A small suffix stemmer folds the everyday variants. Its
rules were chosen by measuring rather than guessing — over the real 1,299-token
vocabulary, every group it merges is a genuine word family, and a test asserts that.

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

**`/openapi.json`, `/docs`, `/health` and the `/p/` pages are deliberately
unauthenticated.** A platform must be able to read the specification before it has
anywhere to put a credential, and `/p/` is loaded by a shopper's browser.

Out of scope, and stated as such: rotation, per-client keys, request signing, rate
limiting.

### The MCP transport

The same five operations, exposed as MCP tools at `/mcp` on the same service. Each
tool is a thin adapter — take arguments, call `app/search.py`, return its dict — so
ranking, filtering and response shaping have exactly one implementation, and the tool
descriptions are copied verbatim from the REST operations rather than reworded, since
they are tuned prompt surface.

Three things were not obvious:

**A mounted sub-app bypasses the router's auth.** The REST tools are protected by
`APIRouter(dependencies=[Depends(verify_bearer)])`. FastAPI hands a `Mount` the raw
ASGI triple and never resolves dependencies, so a plain `app.mount("/mcp", …)` would
have left the whole catalogue readable without a token while REST stayed locked —
with nothing visibly broken. `BearerAuthASGIMiddleware` closes it, scoped to the
mounted app so the public `/p/` pages stay public. Verified by mutation: removing the
middleware fails exactly the two auth tests and nothing else.

**`stateless_http=True`, because Cloud Run has no session affinity.** MCP is
session-oriented by default — it was designed for a long-lived stdio connection to a
local subprocess, with server-initiated messages and subscriptions. Here `initialize`
could create a session on one instance and the next call land on another, and
scale-to-zero can discard the instance between conversation turns. None of the
stateful features are used by five read-only tools, so the session goes.

**The transport is stricter about `Accept` than real clients are.** streamable-HTTP
answers 406 to anything that is not exactly `application/json, text/event-stream` —
including `*/*` and a missing header. Every request reaching this app is an MCP call,
so the requirement is supplied for the client rather than demanded of it.

Mounted at `/` rather than `/mcp`, because FastMCP's own route already sits at `/mcp`
and nesting a second prefix answers a bare `POST /mcp` with a 307 to `/mcp/`, which
not every client follows. The cost is that an unmatched path reaches the MCP app and
is judged by its middleware first — 401 without a token, a normal 404 with one. A test
pins that, so moving the mount is a decision rather than an accident.

### Deployment

Google Cloud Run, `europe-west1` — chosen because indigo.ai is Italian and tool-call
latency is part of the user experience. Warm responses are around 5 ms; scale-to-zero
cold starts are 1–2 seconds.

```bash
gcloud run deploy catalog-service --source . --region europe-west1 \
  --allow-unauthenticated \
  --update-env-vars CATALOG_API_TOKEN="$(cat .token)",\
PUBLIC_BASE_URL="https://catalog-service-566410667338.europe-west1.run.app" \
  --memory 512Mi --cpu 1 --max-instances 3
```

**`--update-env-vars`, never `--set-env-vars`.** `--set-env-vars` *replaces* the whole
environment rather than merging into it, so deploying with only the token silently drops
`PUBLIC_BASE_URL`. That variable defaults to `http://localhost:8080` (`app/config.py`), and
nothing fails loudly when it is wrong — the service starts, every endpoint returns 200, and
`image_url` / `product_url` come back pointing at localhost. In the widget that surfaces as
a broken image rendering its `alt` text next to the link, so the product name appears twice,
plus a Chrome "wants to access other apps and services on this device" prompt, which is the
Local Network Access warning fired by a public page requesting `localhost`. Cost an hour on
10 Aug 2026. If those two symptoms ever reappear, check this variable first.

`--allow-unauthenticated` is required: it governs Google's IAM layer, which would
otherwise return 403 before this service ever runs. The bearer token is the actual
gate, one layer further in.

### Tests

```bash
uv run pytest        # 170 tests
```

| File | Covers |
|---|---|
| `test_ingest.py` | The hostile fixture: quarantine reasons, coercion, caps, ragged rows, encoding |
| `test_search.py` | The six brief scenarios at the tool layer: budget too low, out of stock, nothing suitable, fuzzy category, deduplication, limits, stemming |
| `test_endpoints.py` | Auth, the public spec, **OpenAPI validity**, and spec quality — every operation has a description, every parameter is described, no response contains `null` |
| `test_mcp.py` | The MCP surface: auth on the mounted app, flat tool schemas, and **every tool byte-identical to its REST twin** |

The specification is validated with `openapi-spec-validator` in the test suite rather
than by eye. That is a direct consequence of a mistake — see below.

---

## Conversation design

### How the tools are bound to the agent

No workflow. indigo.ai makes agents mandatory and workflows optional, and a shop
assistant is one agent with five tools — a drag-and-drop conversational graph would
have added a maintenance surface without adding a behaviour.

**REST binding.** Agent settings → Tools settings → *Create Custom Tool Collection*.
The `SCHEMA` box takes the OpenAPI document pasted as text — it is a JSON editor, not
a URL fetcher, so every spec change means re-pasting. The endpoints, methods and
parameters are derived from the spec rather than typed in, which is why the spec is
treated as prompt surface throughout this build: **what goes in that box becomes the
tool definitions the model reads.** Auth is one HEADERS row, `Authorization`, with the
value built from an inline secret so the token never sits in the workspace as
plaintext. The assembled header must come out as exactly `Bearer <token>`; getting the
`Bearer ` prefix on the wrong side of the secret boundary produces a uniform 401 that
looks like a broken service rather than a config error. Then in the Agent Block's
Tools section, each of the five operations is added individually.

**MCP binding** is the alternative path to the same five operations: Integrations →
Add MCP Server, with the **bare service URL** — the platform appends the transport
path itself, so `https://…run.app`, never `…/mcp`. Both bindings were built and both
work; the transport is the client's choice, not a fork in the code.

Multi-tool orchestration is the platform's job, not ours. It calls tools in parallel
when they are independent and sequentially when one depends on another, which is what
makes `get_product_details` → `find_similar_products` chain without a workflow — and
what justifies five focused tools instead of one fat endpoint.

### The prompt

Four sections in the Agent Block. Instructions are in English; **the conversation
examples are in Spanish**, because the examples are what actually teach the output
format and the model keys off them.

**Tone of Voice** sets the register and the column width — *"a good shop assistant,
not a brochure"*, short sentences, at most one emoji per conversation. It bans
headings, tables and nested bullets outright, because the widget is a narrow column
usually on a phone. It also carries two rules that exist only because the widget
misbehaved without them: each product is one block with no blank lines inside it and
a `---` between products (a blank line alone renders as no separation), and product
names are spoken in the shopper's language every time — *"cuchillo puntilla de 9cm"*,
not `Paring Knife 9cm` — with the single exception of names that are themselves
English titles, such as books.

**Brand Rules** is the honesty section. Never invent a product, price, stock level,
delivery time or policy; everything factual must come from a tool response *in this
conversation*. It names what we do not have — returns policy, delivery guarantees,
discount codes, opening hours, a physical shop — so the model declines from a list
rather than improvising. A hard budget is hard: *"under 70"* never quietly becomes 82.
When a tool returns a non-`ok` status, read `message`, `suggestions` and `notes` and
act on them before saying "we have nothing". And **never claim the catalogue lacks
something without having called `search_products` for it** — that rule was added after
the agent answered a yoga question by reasoning over the eleven category names,
concluded there was no yoga shelf, and told a shopper we had nothing, while the
service was returning a cork yoga mat for that exact query. The category list
describes how the shop is organised, not what it contains.

**Conversation Examples** carry the six scenarios from the brief in Spanish, with real
prices and availability. Examples are the lever for anything the model will not do
unprompted: it defaulted to offering a plain link to a product page until an example
showed `![name](url)`, after which it embedded images reliably.

**Company Description** is the shop's own framing, kept short.

### How many questions before recommending, and why

**At most two before the first recommendation, and none at all if the shopper has
already given a constraint.** "A chef's knife under a hundred euros" gets knives, not
a questionnaire. "I need a gift" gets one question that does the most work — who it is
for and what the occasion is, in a single sentence — then recommendations, then
refinement.

The reasoning is asymmetric cost. Refining after a recommendation is cheap: the
shopper has something concrete to react to, and "cheaper" or "she is not really a
cook" is easier to say than to volunteer cold. Interrogating before one is expensive:
every question is a turn where the shopper has received nothing, and it is where
people leave. A wrong first recommendation is recoverable in one turn; a
questionnaire is not recoverable at all.

The budget also has to survive a real tension. A reason tied to the shopper — *"in
your budget, ships in two days, suits someone who has just moved"* — is what separates
a recommendation from a search result, and on turn one there may be nothing to tie it
to. The resolution is not to ask more questions but to **say what is being assumed**,
and to lead with the trade-off when the honest answer is a spread rather than a pick.

### Message format

Designed against the platform's own Card Block limits (title 55 characters,
description 85) so it fits a narrow column whether rendered as text or as cards: two
products per message, three only when comparing; name and price, one line of why tied
to what the shopper said, one line of logistics; under about 80 words; no tables, no
nested bullets; exactly one next step.

**Two products, not one.** With a single product there is nothing the choice is
*against*, and the "why" line degrades into a product blurb — a sentence that would be
identical for every shopper who asked. The contrast is what makes a reason possible.

### The prompt is not the only place behaviour lives

Every list-returning tool ships a `notes` field, and non-`ok` responses ship
`suggestions` as well. That is deliberate: **per-state instructions belong in the
response that created the state**, not in a prompt that has to anticipate all of them
in advance. `get_product_details` on an out-of-stock product returns the alternatives
*and* a note to state the unavailability before pivoting, in one round trip.

This is sharp enough to cut. `find_similar_products` used to tell the model *"say what
makes each one a comparable choice"* — good advice attached to a candidate list that
had not been verified as comparable, which read as *justify whatever I hand you*. Fed
a bath towel set as an alternative to a yoga mat, the agent duly informed a shopper
the towels were "ideales para llevar a clase". The ranking was fixed so the list is
honest, and the note now asks for the trade-off *or* a plain statement that something
is a different kind of thing.

---

## What this build found about the platform

Genuine integration findings, since they cost real time and would cost the next
person the same:

**The importer inlines every `$ref`**, so schema reuse multiplies rather than saves —
one schema referenced five times became twenty inlined copies and 72 KB. Collapsing
the deep recovery-payload schemas halved it.

**Agents are invisible without a trigger.** Without one, messages fall through to the
platform's toolless General Agent, which answers fluently and entirely from
imagination — in our case inventing fourteen categories and eighty subcategories that
do not exist. That failure mode looks like success, which makes it the dangerous one.

**Secrets do not interpolate when an MCP server connects.** The header
`Authorization: [[CATALOG_API_TOKEN]]` arrived at the service as that literal string —
no substitution, no token. The same secret works perfectly for the REST tools, and the
reason is *when* each transport needs it. REST gets its tool list from the uploaded
specification, which needs no credential to read; the token is used only when the
agent invokes a tool, inside a conversation, where an environment is bound and the
placeholder resolves. MCP has no specification: the only way to populate the tool list
is to connect and call `tools/list`, which happens at configuration time with no
conversation and nothing to resolve against. **MCP's runtime discovery — its main
advantage over OpenAPI — is exactly what breaks, because it needs authentication
before an execution context exists.** Fix: put the literal value in the header field.

**The chat widget renders markdown as CommonMark, including setext headings.** A `---`
divider between two products, written on the line directly after a paragraph, is not a
horizontal rule — it is a heading underline. It silently promoted the entire first
product to an `<h2>` (every line bold and oversized) and was consumed, so no rule ever
appeared. The missing divider and the giant bold text were one bug. Use `***`, which
is a thematic break in every position, or no divider at all.

**Three false negatives cost more than the bugs did.** "Repeat this verbatim" tests
kept passing because they omit the divider and the second product — the last product
in a message is the one position that never breaks. Copying a reply out of the chat or
the Details panel strips the newlines the bug depends on; a doubled product name in a
paste is the tell that you are reading rendered HTML rather than markdown. And a
platform reporting "Not Connected" covers at least four distinct failures. In all three
cases the answer came from instrumenting this side — Cloud Run request logs, and one
`WARNING` that logs the *shape* of a rejected credential (scheme, length, never the
token) — not from reasoning about the symptom.

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
per-user personalization or memory beyond the conversation · LLM-generated product
summaries — designed and documented, deliberately not built, because the export's
descriptions are already short enough and the truncation guard covers the case ·
real product photography, which the export does not contain and which will not be
invented — `image_url` is a generated placeholder, not a substitute for it.

The decision to leave these out stems from the fact that the current state of the inventory is simple enough to not need building them, a matter of keeping things simple unless needed. If volume of SKUs rocketed above 10.000 then it makes sense to enable vector databases, RDBs and the rest of the architecture around it, but for 152 I went with KISS

## Time spent

I spent roughly two and a half days building it, partitioned in half a Sunday building the tools and connecting to the Indigo agent, then Monday creating the webpage and improving UX, outputs, standardizing information, translating everything to Spanish at presentation time, and making sure the agent's output was nice and gave a good UX sensation. Tuesday was spent linking up the tools as an MCP server to have both options available (REST and MCP), getting final touches ready and creating the video demo.

## Repository layout

```
app/
  config.py            environment, fails fast on a missing token
  ingest.py            the validating pipeline, tokenising and stemming
  models.py            Pydantic response models, every field described
  search.py            filtering, ranking, similarity, response budget
  errors.py            recoverable-response builders
  auth.py              bearer dependency, and the ASGI guard for the MCP mount
  openapi_compat.py    OpenAPI 3.0 conformance and tool-runtime compatibility
  mcp_server.py        the five operations as MCP tools
  product_page.py      the public /p/ page, chat widget embedded
  placeholder_image.py generated per-product SVG behind image_url
  vocab_es.py          fixed colour/material glossary for the Spanish page
  main.py              five routes plus the MCP mount, thin
data/                  the catalogue export
web/index.html         the landing page (GitHub Pages), widget embedded
scripts/               export_openapi.py — regenerates the committed spec
tests/                 170 tests, including a fixture of deliberately broken CSV
openapi.json           generated snapshot; a test fails if it drifts from the service
```
