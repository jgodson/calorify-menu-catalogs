# Calorify food catalogue format

Calorify can import read-only food lists, such as restaurant menus, from a JSON file or subscribe to one at a URL (**Settings → Food Catalogues**). Catalogues stay on the device, work offline, are excluded from data export and catalogue sync, and are never bundled with or redistributed by the app. Each person brings their own.

This server produces files in this format; any other tool can too.

## Structure

```json
{
  "format": "calorify-catalog",
  "formatVersion": 1,
  "catalog": {
    "id": "mcdonalds-ca",
    "name": "McDonald's Canada",
    "publisher": "McDonald's",
    "region": "CA",
    "sourceUrl": "https://www.mcdonalds.com/ca/en-ca/...",
    "publishedAt": "2026-10-01",
    "version": "2026-10-01"
  },
  "foods": [
    {
      "id": "big-mac",
      "name": "Big Mac",
      "nameFr": "Big Mac",
      "brand": "McDonald's",
      "category": "Burgers",
      "aliases": ["bigmac"],
      "barcode": null,
      "serving": { "description": "1 sandwich (219 g)", "grams": 219 },
      "calories": 540,
      "nutrients": { "protein": 25, "total_fat": 28, "carbohydrate": 46, "sodium": 1010 }
    }
  ]
}
```

### `catalog`

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Letters, numbers, `.`, `-`, `_`. Importing or fetching a catalogue with the same id replaces the existing one. |
| `name` | yes | Shown as the section title in Foods and Add Food. |
| `publisher`, `region` | no | Shown on the catalogue's page. |
| `sourceUrl` | no | Where the data came from, shown as attribution. |
| `publishedAt` | no | `YYYY-MM-DD` or ISO 8601. |
| `version` | no | Free text, shown on the catalogue's page. |

### `foods[]`

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Unique within the catalogue and **stable across updates**, so logged meals and recents stay linked. |
| `name` | yes | |
| `serving.description` | yes | As printed, e.g. `1 sandwich (219 g)`. Nutrition values are per this serving. |
| `serving.grams` or `serving.millilitres` | no | Enables logging by weight or volume. |
| `calories` | yes | kcal per serving, 0–10,000. |
| `nutrients` | no | Keyed by the ids below, in each nutrient's unit. Unknown keys are ignored and reported. Omit a nutrient rather than writing 0 when it isn't published. |
| `nameFr`, `brand`, `category`, `aliases`, `barcode` | no | Used for search and stickers. |

Invalid foods (missing name, negative values, duplicate id, …) are skipped and counted; the rest still import. Files over 20 MB or 20,000 foods are rejected.

### Nutrient ids and units

| id | unit | id | unit |
| --- | --- | --- | --- |
| `total_fat` | g | `sugars` | g |
| `saturated_fat` | g | `sugar_alcohols` | g |
| `trans_fat` | g | `protein` | g |
| `cholesterol` | mg | `potassium` | mg |
| `sodium` | mg | `calcium` | mg |
| `carbohydrate` | g | `iron` | mg |
| `fibre` | g | `vitamin_a` | mcg |
| `vitamin_c` | mg | `vitamin_d` | mcg |

## Hosting a catalogue

Serve the file over **https** (plain http is allowed only for local-network hosts such as `*.local`, `localhost`, or private IP addresses). Calorify sends a plain `GET` with no personal data. If the server returns an `ETag`, Calorify sends it back as `If-None-Match`, and a `304 Not Modified` response costs almost nothing.

Subscriptions update either manually (tap **Update now**) or automatically when the app opens, at most every 12 hours. A failed check keeps the previous version and shows the error on the catalogue.

