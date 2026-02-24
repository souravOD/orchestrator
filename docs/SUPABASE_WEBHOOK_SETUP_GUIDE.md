# Supabase Webhook Setup Guide — Step-by-Step

> **Last updated:** 2026-02-24
>
> Exact field values for creating all 13 Gold→Neo4j webhooks in the Supabase Dashboard.

---

## Before You Start

You need **2 things** ready:

1. **Your orchestrator's public URL** — e.g., `https://orchestrator.yourdomain.com`
2. **A webhook secret** — generate one and put it in your orchestrator's `.env`:

   ```
   WEBHOOK_SECRET=your-secret-value-here
   ```

> [!IMPORTANT]
> Replace `https://orchestrator.yourdomain.com` below with your actual deployed URL.
> The secret you set here must **exactly match** the `WEBHOOK_SECRET` in your `.env` file.

---

## Common Values (Same for All 13 Webhooks)

These fields are **identical** for every webhook:

| Field | Value |
|---|---|
| **Type of webhook** | `HTTP Request` |
| **Method** | `POST` |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` ms |
| **HTTP Header 1** | Name: `Content-type` → Value: `application/json` |
| **HTTP Header 2** *(+ Add a new header)* | Name: `x-webhook-secret` → Value: `your-secret-value-here` |
| **HTTP Parameters** | *(leave empty — no parameters needed)* |

---

## Webhook 1 of 13: `gold_recipes_sync`

| Field | Value |
|---|---|
| **Name** | `gold_recipes_sync` |
| **Table** | `gold.recipes` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 2 of 13: `gold_products_sync`

| Field | Value |
|---|---|
| **Name** | `gold_products_sync` |
| **Table** | `gold.products` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 3 of 13: `gold_ingredients_sync`

| Field | Value |
|---|---|
| **Name** | `gold_ingredients_sync` |
| **Table** | `gold.ingredients` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 4 of 13: `gold_b2c_customers_sync`

| Field | Value |
|---|---|
| **Name** | `gold_b2c_customers_sync` |
| **Table** | `gold.b2c_customers` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 5 of 13: `gold_b2b_customers_sync`

| Field | Value |
|---|---|
| **Name** | `gold_b2b_customers_sync` |
| **Table** | `gold.b2b_customers` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 6 of 13: `gold_households_sync`

| Field | Value |
|---|---|
| **Name** | `gold_households_sync` |
| **Table** | `gold.households` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 7 of 13: `gold_vendors_sync`

| Field | Value |
|---|---|
| **Name** | `gold_vendors_sync` |
| **Table** | `gold.vendors` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 8 of 13: `gold_allergens_sync`

| Field | Value |
|---|---|
| **Name** | `gold_allergens_sync` |
| **Table** | `gold.allergens` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 9 of 13: `gold_dietary_preferences_sync`

| Field | Value |
|---|---|
| **Name** | `gold_dietary_preferences_sync` |
| **Table** | `gold.dietary_preferences` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 10 of 13: `gold_health_conditions_sync`

| Field | Value |
|---|---|
| **Name** | `gold_health_conditions_sync` |
| **Table** | `gold.health_conditions` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 11 of 13: `gold_cuisines_sync`

| Field | Value |
|---|---|
| **Name** | `gold_cuisines_sync` |
| **Table** | `gold.cuisines` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 12 of 13: `gold_product_categories_sync`

| Field | Value |
|---|---|
| **Name** | `gold_product_categories_sync` |
| **Table** | `gold.product_categories` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Webhook 13 of 13: `gold_certifications_sync`

| Field | Value |
|---|---|
| **Name** | `gold_certifications_sync` |
| **Table** | `gold.certifications` |
| **Events** | ☑️ Insert ☑️ Update ☑️ Delete |
| **Type of webhook** | HTTP Request |
| **Method** | POST |
| **URL** | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| **Timeout** | `5000` |
| **HTTP Headers** | `Content-type`: `application/json` |
| | `x-webhook-secret`: `your-secret-value-here` |
| **HTTP Parameters** | *(empty)* |

---

## Quick Reference Checklist

Use this to track your progress:

- [ ] Webhook 1: `gold_recipes_sync` → `gold.recipes`
- [ ] Webhook 2: `gold_products_sync` → `gold.products`
- [ ] Webhook 3: `gold_ingredients_sync` → `gold.ingredients`
- [ ] Webhook 4: `gold_b2c_customers_sync` → `gold.b2c_customers`
- [ ] Webhook 5: `gold_b2b_customers_sync` → `gold.b2b_customers`
- [ ] Webhook 6: `gold_households_sync` → `gold.households`
- [ ] Webhook 7: `gold_vendors_sync` → `gold.vendors`
- [ ] Webhook 8: `gold_allergens_sync` → `gold.allergens`
- [ ] Webhook 9: `gold_dietary_preferences_sync` → `gold.dietary_preferences`
- [ ] Webhook 10: `gold_health_conditions_sync` → `gold.health_conditions`
- [ ] Webhook 11: `gold_cuisines_sync` → `gold.cuisines`
- [ ] Webhook 12: `gold_product_categories_sync` → `gold.product_categories`
- [ ] Webhook 13: `gold_certifications_sync` → `gold.certifications`

---

## After Creating All Webhooks

### Step 1: Verify in Supabase

Go to **Database → Webhooks** — you should see all 13 listed as active.

### Step 2: Test with a curl command

```bash
# Simulate a webhook payload to test your orchestrator receives it
curl -X POST https://orchestrator.yourdomain.com/webhooks/supabase \
  -H "Content-type: application/json" \
  -H "x-webhook-secret: your-secret-value-here" \
  -d '{
    "type": "INSERT",
    "table": "recipes",
    "schema": "gold",
    "record": {"id": "test-uuid", "title": "Test Recipe"},
    "old_record": null
  }'
```

Expected response: `200 OK` with a JSON body containing the flow result.

### Step 3: Test with actual data

Insert a test row into any Gold table and verify:

1. The webhook fires (check Supabase webhook logs)
2. The orchestrator receives it (check FastAPI logs)
3. `neo4j_batch_sync_flow` runs (check `orchestration_runs` table)
