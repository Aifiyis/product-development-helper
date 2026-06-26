from app import create_app
from app.config import DevelopmentConfig


if __name__ == "__main__":
    app = create_app(DevelopmentConfig)
    app.run(host="127.0.0.1", port=5000, debug=True)
else:
    app = create_app()
