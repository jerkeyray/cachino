"""Send two bounded synthetic DeepSeek requests through a local Cachino proxy.

Default execution prints the plan without reading credentials or opening a
connection. Raw evidence belongs to Cachino capture; stdout contains only status
and normalized usage. This is a protocol smoke test, not a cache experiment.
"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from report import response_summary


class NoRedirect(HTTPRedirectHandler):
    """Prevent redirect forwarding of API credentials or additional requests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(value):
    """Accept only an explicit local IPv4 HTTP proxy origin, without secrets."""
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("proxy must be an http://127.0.0.1 origin")
    # Accessing port validates its numeric range before any request is sent.
    if parsed.port is None:
        raise ValueError("proxy must include an explicit port")
    return value.rstrip("/") + "/v1/chat/completions"


def requests():
    """Return fixed JSON/SSE fixtures with a combined 32-token output limit."""
    for stream in (False, True):
        payload = {"model": "deepseek-flash", "messages": [
            {"role": "user", "content": "Reply with only OK."}],
            "max_tokens": 16, "thinking": {"type": "disabled"}, "stream": stream}
        if stream:
            payload["stream_options"] = {"include_usage": True}
        yield payload


def send(url, key, opener):
    """Send at most two requests, stopping on the first failure without retries.

    A 30-second socket timeout and 1 MiB response cap bound client reads, not
    provider billing or total wall time. Credentials and raw responses are never
    printed, including when the upstream returns an error body.
    """
    summaries = []
    for turn, payload in enumerate(requests()):
        req = Request(url, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key,
            "X-Cachino-Session": "hosted-smoke", "X-Cachino-Turn": str(turn),
            "X-Cachino-Attempt": "0"}, method="POST")
        with opener.open(req, timeout=30) as response:
            body = response.read((1 << 20) + 1)
            if len(body) > 1 << 20:
                raise ValueError("response exceeded 1 MiB; stopped")
            summary = response_summary(body, response.headers.get("Content-Type", ""))
            if (response.status != 200 or summary["parse_error"] or summary["response_error"]
                    or summary["stream"] != payload["stream"]
                    or (payload["stream"] and not summary["sse_done"])):
                raise ValueError("response failed JSON/SSE protocol checks; inspect private capture")
            summaries.append({"turn": turn, "http_status": response.status, **summary})
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", default="http://127.0.0.1:8080")
    parser.add_argument("--send", action="store_true", help="send up to two billable requests")
    args = parser.parse_args()
    try:
        url = endpoint(args.proxy)
        if not args.send:
            print(json.dumps({"mode": "dry_run", "endpoint": url,
                              "requests": list(requests()), "maximum_requests": 2}, indent=2))
            return
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key.strip():
            raise ValueError("set DEEPSEEK_API_KEY locally before --send")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        print(json.dumps(send(url, key, opener), indent=2))
    except HTTPError as error:
        parser.exit(1, "smoke test stopped: HTTP %s; inspect private capture\n" % error.code)
    except URLError:
        parser.exit(1, "smoke test stopped: connection failed; inspect proxy setup\n")
    except (OSError, ValueError):
        # Avoid echoing exception text that can contain response or header data.
        parser.exit(1, "smoke test stopped: invalid setup or response; inspect private capture\n")


if __name__ == "__main__":
    main()
