# Product Development Helper

Product Development Helper 是一个面向广告优化与产品开发的 Flask 后台，覆盖社媒热门标签发现、产品扩展、平台采集和独立站竞品监控。

## 功能概览

- `中控台`：模块入口、任务、采集记录和用户概览。
- `热门标签发现`：按 TikTok、Instagram、Facebook、Pinterest、语种和广告类目生成趋势 Hashtags 与热门话题，支持复制和 CSV 导出。
- `产品扩展`：生成产品使用场景、卖点和广告概念；可调用 OpenAI Images API 或 Gemini Imagen 生成图片。
- `平台采集`：配置小红书、抖音、TikTok、Instagram、Facebook、Pinterest 的采集任务入口。
- `竞品监控`：管理竞品站点，支持站点采集和直接产品链接采集，展示产品详情、图片、变体、评论数、广告量与 CSV 导出。
- `用户管理`：三级权限模型：超级管理员、管理员、员工。超级管理员可分配页面权限和功能权限。

## 技术栈

- Flask 3.x、SQLAlchemy、Flask-Login、Flask-APScheduler
- SQLite
- Jinja2、Bootstrap 5、Bootstrap Icons
- Scrapling、Playwright/Patchright
- Gemini API、OpenAI Images API

## 项目结构

```text
product-development-helper/
├── run.py
├── requirements.txt
├── .env.example
├── app/
│   ├── blueprints/
│   ├── data/competitors.json
│   ├── models/
│   ├── services/
│   ├── static/
│   └── templates/
├── instance/
│   └── app.db                 # 本地或服务器运行数据，不通过 Git 同步
└── tests/
```

## 本地开发（Flask，5000 端口）

本地环境不使用 NSSM，直接运行 Flask：

```powershell
cd D:\workspace\claude_workspace\product-development-helper

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
.\.venv\Scripts\python.exe run.py
```

访问地址：<http://127.0.0.1:5000>

`run.py` 使用开发配置并固定监听 `127.0.0.1:5000`。停止服务时，在运行窗口按 `Ctrl+C`。

## 环境变量

将 `.env.example` 复制为 `.env` 后，填写真实密钥。`.env` 已被 Git 忽略，不应提交。

```text
SECRET_KEY=replace_with_a_secure_secret_key
DATABASE_URL=sqlite:///instance/app.db

GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

OPENAI_API_KEY=your_openai_api_key_here
IMAGE_PROVIDER=openai
OPENAI_IMAGE_MODEL=gpt-image-1
GEMINI_IMAGE_MODEL=imagen-4.0-generate-001
```

说明：

- 未配置 `GEMINI_API_KEY` 时，热门标签发现和广告概念生成无法请求 Gemini。
- 未配置 `OPENAI_API_KEY` 时，OpenAI 图片生成功能不可用。
- `DATABASE_URL` 留空或未设置时，默认使用项目下的 `instance/app.db`。
- 现有数据库部署时，不要用本地 `.env` 或本地 `app.db` 覆盖服务器文件。

## 数据库与自动迁移

项目在启动时自动执行 `db.create_all()`，并为已有 SQLite 数据库补充兼容字段，不需要执行 `flask db upgrade`。

当前自动补充的字段包括：

- `users.permissions`、`users.parent_id`
- `collection_tasks.collection_platforms`
- `competitor_tasks.collection_mode`、`competitor_tasks.product_urls`
- `competitor_tasks.product_keywords`、`competitor_tasks.sort_mode`
- `competitor_tasks.last_error`、`competitor_tasks.last_run_summary`
- `competitor_products.product_created_at`、`competitor_products.product_tags`

应用启动账户必须对 `instance` 目录和 `instance/app.db` 有修改权限。升级前先备份数据库：

```powershell
Copy-Item .\instance\app.db ".\instance\app.db.backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

## Windows Server 部署（Waitress + NSSM + Apache）

以下内容仅适用于服务器。服务器运行 Flask 应用时使用 Waitress，由 NSSM 托管；Apache 只负责反向代理到本机 `127.0.0.1:5000`。

### 首次部署

```powershell
cd D:\workspace\product-development-helper

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
```

在服务器 `.env` 中填写独立的 `SECRET_KEY`、Gemini/OpenAI 密钥。保留服务器自己的 `instance\app.db`。

NSSM 的服务程序应为虚拟环境中的 Python，参数示例：

```text
-m waitress --host=127.0.0.1 --port=5000 run:app
```

工作目录为：

```text
D:\workspace\product-development-helper
```

### 日常代码更新

先停止服务、备份数据库、拉取代码，再启动服务。启动时会自动执行上述兼容字段迁移。

```powershell
cd D:\workspace\product-development-helper

nssm stop ProductHelper
Copy-Item .\instance\app.db ".\instance\app.db.backup-$(Get-Date -Format yyyyMMdd-HHmmss)"

git pull origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

nssm start ProductHelper
nssm status ProductHelper
```

正常状态应为：

```text
SERVICE_RUNNING
```

若服务无法启动，查看 NSSM 配置及其标准输出/错误日志：

```powershell
nssm get ProductHelper Application
nssm get ProductHelper AppDirectory
nssm get ProductHelper AppParameters
nssm get ProductHelper AppStdout
nssm get ProductHelper AppStderr
```

Apache 使用反向代理时，不需要为 Flask 应用设置 `DocumentRoot`。Apache 虚拟主机示例：

```apache
<VirtualHost *:80>
    ServerName your-domain.example

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:5000/
    ProxyPassReverse / http://127.0.0.1:5000/
    RequestHeader set X-Forwarded-Proto "http"

    ErrorLog "logs/product-helper-error.log"
    CustomLog "logs/product-helper-access.log" common
</VirtualHost>
```

启用 Apache 的 `proxy`、`proxy_http`、`headers` 模块后，重启 Apache 使配置生效。

## 竞品监控

### 竞品站采集

在“创建采集”中选择“竞品站采集”，可按网站类别筛选、搜索并多选 `app/data/competitors.json` 中的网站。可设置每站采集数量、按销量排名或最新上架时间采集、产品关键词和周期。

站点列表页面支持：

- AI 跟踪趋势后加入候选站点。
- 手动添加站点：填写网址、网站类别、平台、简介和采集原因。
- 平台标识：Shopify、Shopline、Shoplazza、自建站或未知。

### 链接采集

在“创建采集”中选择“链接采集”，在产品网址输入框中每行填写一个完整 URL，例如：

```text
https://example.com/products/example-one
https://example.com/products/example-two?variant=123
```

链接采集会逐条访问产品详情页，解析标题、价格、描述、产品图片、变体、Product Tags 和评论数量。它不会按竞品站列表、产品关键词或列表排序过滤。

### 抓取依赖

部分站点依赖动态页面解析。运行机器需安装可供 Playwright 启动的 Chrome 或 Edge 浏览器；首次配置 Playwright 浏览器时可执行：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

实际抓取成功率受站点反爬策略、页面结构、登录状态和第三方插件加载时间影响。任务失败信息会显示在竞品任务管理列表中。

## 页面与操作路由

主要页面：

- `/dashboard`
- `/hashtag-discovery`
- `/product-extension`
- `/collection/platform`
- `/competitor`
- `/competitor/sites`
- `/auth/users`

竞品监控接口：

- `POST /competitor/tasks`
- `POST /competitor/tasks/<id>/run`
- `POST /competitor/tasks/<id>/pause`
- `POST /competitor/tasks/<id>/delete`
- `GET /competitor/tasks/<id>/status`
- `GET /competitor/products/<id>`
- `POST /competitor/export`
- `POST /competitor/sites/add`
- `POST /competitor/sites/track`

## 验证

自动测试使用内存 SQLite，不会修改 `instance/app.db`：

```powershell
cd D:\workspace\claude_workspace\product-development-helper
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

手动检查：

1. 登录后打开“竞品监控”。
2. 点击“创建采集”，确认可切换“竞品站采集”和“链接采集”。
3. 选择链接采集，输入多条完整产品 URL，保存后确认任务状态变为“采集中”。
4. 打开“竞品站列表”，确认可使用“手动添加站点”。
5. 采集完成后刷新页面，检查产品详情、图片、变体和 Product Tags。

## 默认账户

只有在全新空数据库且不存在超级管理员时，应用才会创建初始账户：`admin / admin123`。生产环境首次登录后应立即修改密码，并创建正式的超级管理员账户。

## 相关说明

- `Scrapling` 从 PyPI 安装；其上游项目为 [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling)。
- `white0dew/XiaohongshuSkills` 可作为 Codex 技能辅助小红书工作流，但 Flask 应用运行时不依赖该技能。
- 后续扩展规划见 [extension.md](extension.md)。
