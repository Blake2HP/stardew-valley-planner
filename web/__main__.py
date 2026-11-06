"""python -m web [--host 127.0.0.1] [--port 8000]"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动网页版 Demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="代码改动后自动重启（开发用）")
    args = parser.parse_args()
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
