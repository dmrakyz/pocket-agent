---
name: etsy-shop
description: How to run the user's Etsy shop with the etsy_* tools: research, write a digital-download listing, draft it, review results, iterate. Load it before any Etsy work.
---

# Running the Etsy shop

You have `etsy_read` (shop, listings, orders, reviews, taxonomy), `etsy_draft` (creates an invisible draft) and `etsy_update` (visible changes; the user approves every call). If the tools are missing, Etsy is not set up: tell the user to open Settings → Etsy.

## Ground rules
- Be honest in listings. No fake reviews, fake scarcity or "only 2 left", no claims the product cannot back up, no copying a competitor's text or images, no brand or trademark names you do not own.
- Etsy has rules about disclosing AI-generated content and about who made what. When images or text are AI-generated, say so where Etsy asks and never claim hand-made work you did not do. If unsure, ask the user.
- Draft freely, but publishing (`state: active`), price changes and edits go through the user's approval. Never try to route around that.
- Do not send anything to buyers yourself; the API cannot message them. Draft replies for the user instead.
- Fees and rules change. For anything about fees, payouts or policy, check the user's Etsy dashboard or Etsy's own help pages with web_search instead of quoting from memory.

## Workflow for a new product
1. **Brief.** Ask for what is missing: product, who it is for, price floor, tone. Save the stable parts with `memory_save` so later chats start with them.
2. **Research** (web_search and the public pages it finds; do not script-scrape Etsy itself): what the top 10 listings for the main keyword look like: price range, title pattern, how many photos, what the first photo says, review counts. Write down the gap this product fills. If the niche is crowded (it usually is), pick a narrower angle ("for freelance designers", "for Turkish sole proprietors") instead of the generic keyword.
3. **Taxonomy.** `etsy_read what=taxonomy query=<words>` and pick the most specific category id.
4. **Write the listing** (keep the text in a workspace file so the user can read it):
   - Title: max 140 characters, the main buyer phrase first, then 2 or 3 specific phrases. No keyword stuffing with repeated words.
   - Tags: exactly 13, each max 20 characters, multi-word phrases a buyer would type, no repeats of the title's exact wording only.
   - Description: first 2 lines are what shows in search, so lead with what it is and who it is for. Then what is included, how it is delivered, how to use it, what is NOT included, refund/support note. Plain, specific, no hype.
   - Price: from the research range, not the cheapest. Say the reasoning.
5. **Images** (first one is the thumbnail): a clear main image with a short headline, then screenshots of each tab or feature, a "what you get" image, and a how-it-works image. Make them with generate_image or by screenshotting the real product in the browser tool, and look at each with view_image before using it.
6. **Delivery file.** A Google Sheets template is delivered as a PDF that holds the "make a copy" link plus short setup steps. Check the link opens as "Make a copy" and is view-only for everyone else. Do not put edit links in the file.
7. **Draft** with `etsy_draft` (file + images). Report the listing id and show the user the draft text. Wait for their OK, then `etsy_update` with `state: active`.

## Running it afterwards
- Weekly: `etsy_read what=shop`, `listings`, `orders`, `reviews`. Report sales, views and favorites per listing, new reviews, and ONE change worth trying (title wording, first image, price). Change one thing at a time so the result means something.
- A listing with views but no favorites needs a better first image or price. Favorites but no sales: price or description. No views: title, tags, category.
- Low numbers in the first weeks are normal for a new shop with no reviews. Do not panic-change everything, and do not promise the user income.
- Pinterest/Instagram traffic is usually what moves a new Etsy shop. Draft pin text and images for each listing and give them to the user to post unless a posting tool is connected.
- Keep a short log (a file or memory note) of what was changed and the result, so lessons are not relearned.
