# Product Development Helper

Product Development Helper 是一个 Flask + SQLite 的产品开发辅助后台，用于广告优化、社媒趋势发现、产品卖点扩展、平台采集和竞品监控。

当前项目包含这些功能模块：

- `中控台`：模块入口、任务数、采集结果数、用户数概览。
- `产品扩展`：根据产品名、目标用户、卖点和广告文案生成 10 个使用场景/卖点概念，并支持 OpenAI Images API / Gemini Imagen 生图。
- `热门标签发现`：按平台、语种、广告类目生成社媒上升趋势 Hashtags 和热门话题，支持复制标签和 CSV 导出。
- `平台采集`：创建多平台采集任务，支持小红书、抖音、TikTok、Instagram、Facebook、Pinterest 的任务配置入口。
- `竞品监控`：维护竞品站列表，创建竞品采集任务，优先通过 Shopify `/products.json` 采集产品信息，支持详情弹窗、立即运行、定时任务和 CSV 导出。
- `用户管理`：管理员创建、编辑、停用和删除用户。

## Tech Stack

- Backend: Flask 3.x
- Database: SQLite + SQLAlchemy
- Auth: Flask-Login
- Scheduler: Flask-APScheduler / APScheduler
- Scraping: Scrapling + Shopify `/products.json`
- Frontend: Jinja2 + Bootstrap 5 CDN + Bootstrap Icons
- AI: Gemini API, OpenAI Images API

## Setup

```powershell
cd D:\workspace\claude_workspace\product-development-helper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Open:

```text
http://127.0.0.1:5000
```

Default admin account:

- Username: `admin`
- Password: `admin123`

## Environment Variables

Copy `.env.example` to `.env`, then fill in real credentials:

```powershell
copy .env.example .env
```

Important variables:

```text
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=your_openai_api_key_here
IMAGE_PROVIDER=openai
OPENAI_IMAGE_MODEL=gpt-image-1
GEMINI_IMAGE_MODEL=imagen-4.0-generate-001
SECRET_KEY=replace_with_a_secure_secret_key
DATABASE_URL=sqlite:///instance/app.db
```

Competitor and ad intelligence integration placeholders:

```text
SEMRUSH_API_KEY=
AHREFS_API_KEY=
FUNNEL_API_KEY=
SUPERMETRICS_API_KEY=
META_BUSINESS_TOKEN=
FB_ADS_LIBRARY_TOKEN=
```

The local `.env` file is ignored by Git.

## Routes

Main pages:

- `/dashboard`
- `/product-extension`
- `/hashtag-discovery`
- `/collection/platform`
- `/competitor`
- `/competitor/sites`
- `/auth/users`

Key API/action routes:

- `POST /product-extension/generate-image`
- `POST /hashtag-discovery/export`
- `POST /collection/platform/tasks`
- `GET /collection/platform/notes/<id>`
- `POST /collection/platform/export`
- `POST /competitor/tasks`
- `POST /competitor/tasks/<id>/run`
- `GET /competitor/products/<id>`
- `POST /competitor/export`
- `POST /competitor/sites/track`

## Project Structure

```text
product-development-helper/
├── run.py
├── requirements.txt
├── README.md
├── extension.md
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── extensions.py
│   ├── data/
│   │   └── competitors.json
│   ├── models/
│   ├── blueprints/
│   ├── services/
│   ├── templates/
│   └── static/
└── instance/
```

## Current Module Notes

### 产品扩展

- `Generate Ad Concepts` uses Gemini when `GEMINI_API_KEY` is configured.
- `Generate Image` calls `POST /product-extension/generate-image`.
- Image provider can be switched between OpenAI and Gemini on the page.
- Generated images are saved under `app/static/generated/` and ignored by Git.

### 热门标签发现

- First page load uses local sample data for speed.
- Clicking `Catch` calls Gemini when `GEMINI_API_KEY` is configured.
- Results can be copied with `CopyTags` or exported with `ExportCSV`.

### 平台采集

- Task creation supports platform selection for 小红书、抖音、TikTok、Instagram、Facebook、Pinterest.
- Real social platform collection is currently an integration surface; actual authenticated platform scraping requires cookies, browser state, selectors, and compliance review.

### 竞品监控

- Seed competitors live in `app/data/competitors.json`.
- Shopify sites are collected through `https://<domain>/products.json?limit=N`.
- Product details include source, title, price, media, variants, description, URL, review count placeholder, and FB ads placeholder.
- Scheduled jobs are restored on app startup for active competitor tasks.

## Verification

Basic checks:

```powershell
.\.venv\Scripts\python -m compileall -q .
python run.py
```

Manual verification:

1. Login with `admin/admin123`.
2. Open `/dashboard` and verify all module cards render.
3. Open `/product-extension`, generate concepts, then generate an image using OpenAI or Gemini.
4. Open `/hashtag-discovery`, click `Catch`, copy tags, export CSV.
5. Open `/collection/platform`, create a platform collection task.
6. Open `/competitor/sites`, confirm the competitor list is grouped by category.
7. Open `/competitor`, create a task for a Shopify site such as `pawsionate.com`, then click `立即运行`.
8. Export competitor products as CSV.

## Notes

- `white0dew/XiaohongshuSkills` can be installed as a Codex skill for Xiaohongshu-specific collection workflows, but the Flask app does not require skills at runtime.
- `Scrapling` is referenced from `https://github.com/D4Vinci/Scrapling` in `requirements.txt`.
- SQLite tables are created with `db.create_all()` on startup. This is fine for local development; production should use migrations.
