---
name: digital-store
description: Run a small digital-product business end to end with Lemon Squeezy and a static storefront: niche research, build the product, listing kit, site, traffic drafts, weekly review. Load it for any "make money / sell a product" work.
---

# Digital product business

Tools: `ls_read`, `ls_checkout`, `ls_refund` (Lemon Squeezy, the payment and delivery side), `web_search`/`web_fetch` (research), `publish_pages` (the storefront, the user approves), `routine_create` (the user approves), `send_email` (the user approves), `memory_save`. Lemon Squeezy's API is read-only for products, so **the user creates each product once in the Lemon Squeezy dashboard**; you prepare everything they paste.

## Ground rules
- The user's goal is money with as little of their own work as possible. Say plainly what only they can do: open the payout account (identity check, bank), create each product in the dashboard, approve publishing and refunds. Do everything else yourself.
- Be honest to buyers: no fake reviews, testimonials, sales counters or scarcity; no claims the product cannot back up; say when content or images are AI-generated if the platform asks; do not copy competitors' text or images; no trademarks you do not own.
- Never promise income to the user. Early on, expect little. The first real sale is the milestone.
- Taxes and registration in Turkey are the user's call with a mali müşavir. Do not give tax advice beyond "ask them".
- Spend nothing. Free tools only, unless the user approves a cost.

## 1. Brief
Ask only for what is missing: what the user can and cannot build, budget (default $0), languages, and what they refuse to do. Save stable answers with `memory_save`.

## 2. Pick a niche (the step that decides everything)
Find 3 candidates and give evidence for each. Use the user's own screen: real demand, not blocked, you can build it, payable to them in Turkey, big enough audience. Kill any candidate with a mature, well-reviewed competitor and no angle of its own, and say so (the user killed a good-looking idea that way before and counted it a success).
Evidence to collect with web_search / web_fetch: people asking for it (Reddit, forums, "how do I..." questions), what already sells and at what price, how many competitors and how good they are, the specific gap. Cite the pages. Do not invent search volumes; if you cannot measure demand, say "unmeasured".
Prefer products you can fully build and test yourself: spreadsheet templates, small web tools, datasets, checklists/guides with real expertise, config or code packs, printable planners, Minecraft/mod resources the user can vouch for.
Output one short table: candidate, buyer, price, evidence (links), gap, build effort, kill risk. Recommend one and wait for the user to choose.

## 3. Build and test the product
Build it in the workspace (`out/`). Then test it like a buyer: open every file with its library, recalculate formulas, click every link, run the code on a clean copy. Fix what breaks. A template delivered as a Google Sheet goes out as a PDF containing the "make a copy" link and setup steps (never an edit link). Write a README/Quick-start inside the download.

## 4. Listing kit for the user to paste
One file (`out/listing-kit.md`) with: product name, one-line promise, full description (what it is, who it is for, what is included, how it is delivered, what it is NOT), price with reasoning, file list, FAQ, refund policy text. Include the exact dashboard steps: New product → paste name and description → set price → upload the file(s) → publish. Ask the user to tell you when done, then run `ls_read what=products` and `what=variants` to get the ids.

## 5. Storefront
One fast static page per product (and a short home page if there are several): hero with the promise, 4 to 6 preview images, what is included, FAQ, refund policy, contact, and a Buy button pointing at the `ls_checkout` link (or the product's buy URL). Mobile first, no tracking scripts the user did not approve. Publish with `publish_pages` (asks the user). Re-run it after changes.
Add a few extra pages that each answer one specific search question a buyer would type (one query per page, useful on its own, linking to the product). That is the free, durable traffic source.

## 6. Traffic
Be realistic: a new site gets almost no visitors by itself. Draft posts and pins for the user to publish, tailored per community, always disclosing it is the user's own product and following each community's self-promotion rules. Do not spam, astroturf, or run bot accounts; they get banned and can sink the whole project. If a posting tool is connected and the user approves, use it; otherwise hand over ready-to-paste drafts.

## 7. Operate
Offer to schedule (the user approves each): a daily check of `ls_read what=orders` (alert on a new sale, refund or odd pattern) and a weekly report with orders, revenue, what changed, and ONE next experiment. Draft support replies; send nothing yourself without approval. Refund only per the stated refund policy, and only with the user's approval.

## 8. Kill rule
Write down a fail condition before launch in counts, not time (for example: after 2,000 visits to the page and zero sales, change the page; after two product pages with no sale, drop the niche). Report against it each week and recommend a kill or a pivot when it is hit. Log every change and its result in a short notes file or memory so lessons are not relearned.
