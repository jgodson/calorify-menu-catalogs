# Calorify menu catalogues

A small self-hosted server that turns restaurant nutrition information into food catalogues for Calorify, a private nutrition app,, so you can search and log menu items instead of typing them in.

It serves each catalogue at a stable URL that Calorify subscribes to (**Settings → Food Catalogues → Add from URL**), refreshes what it can on a schedule, and gives you an upload page for restaurants whose sites have to be saved from a browser.

> **Personal use.** This repository contains code only. Menu data stays on your own server, is never committed here, and shouldn't be redistributed. Run it privately (for example on your home network or behind a VPN) rather than as a public mirror.

## Restaurants

| Restaurant | How it updates |
| --- | --- |
| Tim Hortons (Canada) | Automatically, weekly by default, from the public data timhortons.ca loads its menu from (about 30 requests per refresh, 0.5 s apart). Includes every size, French names, and Regular / Double Double / Double Cream coffee orders. |
| McDonald's (Canada) | Upload a HAR captured from the nutrition calculator (steps below). Only the nutrition responses are kept; cookies and the rest of the HAR are discarded. |
| Dairy Queen (Canada) | Upload the nutrition page saved from your browser (steps below). |
| Subway (Canada) | Upload Subway's Canada nutrition information PDF (steps below). Each 6-inch sub also gets a footlong with doubled values, as the PDF instructs. Calcium and iron are only published as % DV, so they're left out. |

McDonald's and Dairy Queen block automated requests, and this project never tries to get past bot protection, so those two (and Subway's PDF) are refreshed by hand when their menus change.

## Running it

Requires Python 3.11+. Reading Subway's PDF also needs `pdftotext` from poppler (`brew install poppler`, `apk add poppler-utils`, or `apt install poppler-utils`); the container image includes it.

```sh
UPLOAD_TOKEN=$(openssl rand -hex 32) python -m menu_catalogs serve
```

Open `http://localhost:8080/` for the status and upload page. Catalogues are served at `/catalogs/<id>.catalog.json`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `UPLOAD_TOKEN` | unset | Required to upload files or trigger refreshes. Uploads are disabled when unset. |
| `READ_TOKEN` | unset | Optional. When set, catalogue URLs need `?token=<READ_TOKEN>`. |
| `DATA_DIR` | `./data` | Where catalogues, uploaded inputs, and status are stored. Keep it on persistent storage. |
| `PORT` | `8080` | Listen port. |
| `REFRESH_INTERVAL_HOURS` | `168` | How often automatic sources are re-downloaded. Failed refreshes retry after 6 hours. |
| `PUBLIC_BASE_URL` | from request | Base URL shown on the status page. |

Keep tokens in your secret store; never put them in this repository.

Catalogues are served with strong ETags, so Calorify's automatic updates cost a tiny `304 Not Modified` when nothing changed.

### Container

```sh
podman build -t calorify-menu-catalogs .
podman run -p 8080:8080 -v catalog-data:/data -e UPLOAD_TOKEN=... calorify-menu-catalogs
```

The image runs as a non-root user and works with a read-only root filesystem (mount `/data` and a writable `/tmp`).

### Kubernetes

`k8s/` contains a Deployment, Service, Ingress, and PersistentVolumeClaim with `{{PLACEHOLDER}}` values in the style used by kube-manager "manifests" applications (`APP_NAME`, `NAMESPACE`, `IMAGE`, `HOSTNAME`, `SECRET_NAME`, `REGISTRY_SECRET_NAME`). Replace them by hand for other tooling, set the storage class and ingress class for your cluster, and provide `UPLOAD_TOKEN` (and optionally `READ_TOKEN`) through a Secret.

## Updating McDonald's, Dairy Queen, and Subway

Menus change a few times a year. Item ids stay the same between updates, so meals already logged in Calorify stay linked, and logged meals always keep the nutrition they were logged with.

### McDonald's

1. In Chrome, open the [McDonald's Canada nutrition calculator](https://www.mcdonalds.com/ca/en-ca/about-our-food/nutrition-calculator.html).
2. Open developer tools (⌥⌘I on Mac), select the **Network** tab, and type `itemList` in the filter box.
3. Click each menu category once (Breakfast, Beef, Chicken, Sandwiches & Wraps, Snacks & Sides, Desserts & Shakes, Beverages, McCafé, McValue Snacks). One `itemList` request appears per category.
4. Click the download arrow (**Export HAR**).
5. On the server's status page, enter your upload token and choose the HAR under McDonald's. The page shows the food count (about 186 in October 2026; a much smaller number usually means a category was missed).
6. Delete the HAR from your computer.

Drinks appear once at the calculator's default size with their volume, so log other sizes by mL.

### Dairy Queen

1. Open [dairyqueen.com/en-ca/nutrition/food-treats](https://www.dairyqueen.com/en-ca/nutrition/food-treats/) and wait for the tables to load.
2. Use **File → Save Page As** (HTML).
3. On the server's status page, enter your upload token and choose the saved page under Dairy Queen. Rows DQ publishes with missing values are skipped and listed (about 324 foods in October 2026).

### Subway

1. On [Subway Canada](https://www.subway.com/en-CA), open **Menu → Nutrition** and download the Canada nutrition information PDF.
2. On the server's status page, enter your upload token and choose the PDF under Subway. Only the text of its tables is stored (about 190 foods in October 2026, including footlongs). Rows that don't have every column are skipped and listed.

You can also upload from a terminal:

```sh
curl -X POST -H "Authorization: Bearer $UPLOAD_TOKEN" --data-binary @mcdonalds.har \
  https://your-server/api/sources/mcdonalds/upload
```

Uploaded inputs are stored so that converter improvements are applied automatically the next time the server starts.

## Building a catalogue without the server

```sh
python -m menu_catalogs build tim-hortons
python -m menu_catalogs build mcdonalds --input mcdonalds.har
python -m menu_catalogs build dairy-queen --input "Food & Treats Nutrition.html"
python -m menu_catalogs build subway --input CAN_Nutrition_ENG.pdf
```

Files are written to `out/` (gitignored) and can be imported in Calorify with **Settings → Food Catalogues → Import file**.

## Development

```sh
python -m unittest discover -s tests -t .
```

Tests use small made-up fixtures. Each restaurant lives in its own module in `menu_catalogs/` and registers in `sources.py`; the output format is documented in [docs/food-catalog-format.md](docs/food-catalog-format.md).

## License

MIT
