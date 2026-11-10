"""python -m web [--host 127.0.0.1] [--port 8000] [--forwarded-allow-ips 10.0.0.5]

部署在反向代理（Nginx、平台负载均衡等）后面时，用 --forwarded-allow-ips（或环境变量
DEMO_FORWARDED_ALLOW_IPS）列出代理的 IP。uvicorn 只信任这些地址发来的 X-Forwarded-For，
并据此得到用户的真实 IP；否则所有请求都来自代理的 IP，限流会把全部用户当成同一个人。
不要在直接暴露在公网时设成 "*"：那样任何人都能伪造 X-Forwarded-For 绕过限流。
"""

import argparse
import os

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动网页版 Demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="代码改动后自动重启（开发用）")
    parser.add_argument(
        "--forwarded-allow-ips", default=os.getenv("DEMO_FORWARDED_ALLOW_IPS") or None,
        help="信任哪些反向代理传来的 X-Forwarded-For（逗号分隔的 IP）；默认只信任 127.0.0.1",
    )
    args = parser.parse_args(argv)
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload,
                proxy_headers=True, forwarded_allow_ips=args.forwarded_allow_ips)


if __name__ == "__main__":
    main()
