# Product Development Helper

Flask dashboard for managing Xiaohongshu and Douyin product collection tasks.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5000`.

Default admin account:

- Username: `admin`
- Password: `admin123`

## Environment Variables

Copy `.env.example` to `.env`, then fill in real credentials:

```powershell
GEMINI_API_KEY="your_real_api_key"
```

The local `.env` file is ignored by Git.

## Notes

- `white0dew/XiaohongshuSkills` should be installed as a Codex skill for Xiaohongshu-specific collection workflows.
- `Scrapling` is referenced from `https://github.com/D4Vinci/Scrapling` in `requirements.txt`.
- Real Xiaohongshu/Douyin collection usually requires authenticated browser context, stable selectors, and compliance review. The included scraper services are structured integration points and return no records when live parsing is not configured.
