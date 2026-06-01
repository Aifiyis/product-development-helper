# Extension Plan

This file tracks current limitations, integration placeholders, and future extension points. It is intentionally pragmatic: if a feature is not complete, it should be visible here instead of hidden in code comments.

## 1. Platform Collection

Current state:

- `/collection/platform` supports task creation and platform selection.
- Supported platform options: 小红书、抖音、TikTok、Instagram、Facebook、Pinterest.
- Real scraping is not fully implemented for authenticated social platforms.
- Xiaohongshu and Douyin scraper classes exist as structured integration points.

Future work:

- Add authenticated browser profiles or cookie management per platform.
- Add selectors and parsing logic for each platform.
- Store raw HTML / screenshots for debugging failed parses.
- Add proxy and rate-limit controls.
- Add per-platform compliance review before production crawling.
- Add platform-specific status fields and retry history.

Configuration likely needed:

```text
XHS_COOKIE_PROFILE=
DOUYIN_COOKIE_PROFILE=
TIKTOK_COOKIE_PROFILE=
INSTAGRAM_SESSION=
FACEBOOK_SESSION=
PINTEREST_SESSION=
SCRAPER_PROXY_URL=
```

## 2. Competitor Monitoring

Current state:

- `app/data/competitors.json` contains 110 seed competitors.
- Shopify `/products.json` collection is implemented and verified for at least one site.
- Non-Shopify DOM scraping is a safe stub.
- Review count extraction is not fully implemented.
- Customily/YMQ/plugin-rendered variant extraction is not fully implemented.
- FB Ads Library integration is a stub.
- SEMrush/Ahrefs/Funnel.io/Supermetrics/Meta Business Suite integrations are stubs.
- Trend tracking uses Gemini or local fallback to generate candidate domains.

Future work:

- Add robust domain platform detection.
- Add DOM fallback selectors for non-Shopify stores.
- Add configurable wait selectors for Customily/YMQ variant widgets.
- Add product review parsing per app/provider.
- Add product-to-ad matching logic using URL, title similarity, image similarity, and landing page matching.
- Add deduplication rules for repeated products across task runs.
- Add historical trend charts by site/category/product.
- Add manual approve/reject workflow for discovered competitors.

Configuration already reserved:

```text
SEMRUSH_API_KEY=
AHREFS_API_KEY=
FUNNEL_API_KEY=
SUPERMETRICS_API_KEY=
META_BUSINESS_TOKEN=
FB_ADS_LIBRARY_TOKEN=
```

## 3. Product Extension

Current state:

- Gemini generates 10 product extension concepts when `Generate Ad Concepts` is submitted.
- Local fallback concepts are used when Gemini fails or is not configured.
- OpenAI Images API and Gemini Imagen are wired for `Generate Image`.
- Generated image files are saved to `app/static/generated/`.
- Uploaded reference product image is currently preview-only and is not sent to image generation APIs.

Future work:

- Send uploaded product reference image to OpenAI image edit / variation flows.
- Add Gemini image input support if using an image-capable endpoint.
- Add image generation job table to persist prompt, provider, status, cost, output URL, and errors.
- Add async queue for image generation instead of long HTTP requests.
- Add image gallery, regeneration history, and manual rating.
- Add provider-specific controls: quality, style, background, count, safety filters, negative prompt equivalents.
- Add CDN/object storage for generated images.

Current config:

```text
OPENAI_API_KEY=
OPENAI_IMAGE_MODEL=gpt-image-1
GEMINI_API_KEY=
GEMINI_IMAGE_MODEL=imagen-4.0-generate-001
IMAGE_PROVIDER=openai
```

Potential future config:

```text
GENERATED_IMAGE_STORAGE=s3
S3_BUCKET=
S3_REGION=
IMAGE_GENERATION_QUEUE=redis
REDIS_URL=
```

## 4. Hashtag Discovery

Current state:

- First page load uses local sample data for speed.
- `Catch` uses Gemini when configured.
- Results include 10 hashtags and 5 topics.
- CSV export and tag copying work.
- Data is not persisted.

Future work:

- Persist daily hashtag discovery runs.
- Add trend delta compared with previous days.
- Add per-platform/language/category history.
- Add scheduled daily collection.
- Add source attribution once real platform APIs or third-party social listening tools are integrated.
- Add campaign notes and manual tagging.

Potential future config:

```text
HASHTAG_DAILY_COLLECTION_TIME=
SOCIAL_LISTENING_API_KEY=
```

## 5. Image Generation Providers

Current state:

- OpenAI and Gemini providers are implemented via HTTP API.
- Generated files are local static files.
- Errors are surfaced in the card UI.

Future provider options:

- Stability AI
- Replicate
- fal.ai
- Ideogram
- Midjourney-compatible third-party gateways

Potential config:

```text
STABILITY_API_KEY=
REPLICATE_API_TOKEN=
FAL_KEY=
IDEOGRAM_API_KEY=
```

## 6. Database and Migrations

Current state:

- SQLite is used locally.
- Tables are created via `db.create_all()`.
- A small manual SQLite column patch exists for `collection_platforms`.

Future work:

- Add Flask-Migrate/Alembic.
- Create real migration scripts.
- Replace manual schema patching.
- Add indexes for frequently filtered fields.
- Add data retention policy for generated products/images.

Potential config:

```text
DATABASE_URL=postgresql+psycopg://...
```

## 7. Authentication and Roles

Current state:

- Flask-Login is implemented.
- Default admin account is created on startup.
- Roles: `admin`, `viewer`.

Future work:

- Force default admin password reset.
- Add CSRF protection.
- Add audit logs for task creation, export, and image generation.
- Add per-module permissions.
- Add OAuth or SSO if this becomes team-facing.

## 8. Scheduling and Background Jobs

Current state:

- APScheduler runs inside the Flask process.
- Platform collection and competitor tasks can be restored on startup.
- Long-running scraping/image work still runs synchronously in several places.

Future work:

- Move scraping and image generation to a queue worker.
- Add job status pages.
- Add retries, backoff, and timeout settings.
- Store job logs in DB.
- Add notifications for failed scheduled runs.

Potential config:

```text
REDIS_URL=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=
```

## 9. Deployment

Current state:

- Designed for local development with Flask dev server.

Future work:

- Add production WSGI server config.
- Add Dockerfile and docker-compose.
- Add environment-specific config classes.
- Add GitHub Actions for lint/test.
- Add health check endpoint.
- Add backup strategy for SQLite or move to Postgres.

## 10. Security Notes

- `.env` is ignored by Git.
- Generated images and local database files are ignored by Git.
- API keys that were ever pasted into chat, screenshots, logs, or commits should be rotated.
- Before production, add CSRF protection and stricter secret management.
