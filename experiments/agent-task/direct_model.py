"""Provide a text-only HTTP model adapter for the pinned mini-swe-agent loop.

Request construction uses the standard library. The harness supplies its existing
text-action parser and observation formatter through lazy imports. Transport is
one JSON request with no retries, redirects or environment proxy discovery.
This first adapter accepts only an explicit loopback OpenAI-compatible endpoint.
"""

import json
import math
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ACTION_REGEX = r"```mswea_bash_command\s*\n(.*?)\n```"
FORMAT_ERROR = "Please always provide EXACTLY ONE action in triple backticks, found {{actions|length}} actions."
OBSERVATION_TEMPLATE = (
    "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
    "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
)
RESPONSE_LIMIT = 1 << 20


class NoRedirect(HTTPRedirectHandler):
    """Reject redirects instead of forwarding authorization to another endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def text_messages(messages):
    """Copy supported text messages, removing only the harness's local `extra`.

    Unknown fields fail explicitly. Response metadata lives in `extra` from its
    creation, so the next request does not inherit an SDK's message dump.
    """
    if not isinstance(messages, list) or not messages:
        raise ValueError("nonempty text message history required")
    result = []
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content", "extra"}:
            raise ValueError("unsupported request message fields")
        if message.get("role") not in ("system", "user", "assistant") or not isinstance(message.get("content"), str):
            raise ValueError("only system/user/assistant string messages are supported")
        result.append({"role": message["role"], "content": message["content"]})
    if result[-1]["role"] != "user":
        raise ValueError("a model query must end with a user message")
    return result


def assistant_text(response, model_name):
    """Validate one complete text reply before the harness parses an action.

    Refusals, tool calls, null content, truncated replies and another model's
    response fail explicitly. The caller retains the entire raw parsed reply.
    """
    if not isinstance(response, dict) or response.get("model") != model_name:
        raise ValueError("response model differs from the requested model")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("expected one completion choice")
    choice = choices[0]
    message = choice.get("message")
    if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
        raise ValueError("expected a complete assistant response")
    if message.get("role") != "assistant" or not isinstance(message.get("content"), str):
        raise ValueError("expected assistant string content")
    if message.get("refusal") is not None or message.get("tool_calls") or message.get("function_call"):
        raise ValueError("refusals and native tool calls are unsupported")
    if message.get("reasoning_content") not in (None, "") or message.get("reasoning") not in (None, ""):
        raise ValueError("separate reasoning content is unsupported by the text-only adapter")
    return message["content"]


class DirectTextModel:
    """Implement the harness model protocol with one bounded HTTP POST per query.

    Only the constructor receives the key. It is excluded from template variables
    and trajectory configuration. Native response fields stay under local extra;
    no cost is invented when price/usage have not been established.
    """

    def __init__(self, *, model_name, api_base, api_key, max_tokens=64, temperature=0, timeout=10,
                 observation_template=None):
        url = urlsplit(api_base)
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.port is None
                or url.username is not None or url.password is not None
                or url.path != "/v1" or url.query or url.fragment):
            raise ValueError("explicit http://127.0.0.1:PORT/v1 endpoint required")
        if not isinstance(model_name, str) or not model_name or not isinstance(api_key, str) or not api_key:
            raise ValueError("explicit model and key required")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("positive integer max_tokens required")
        for name, value in (("temperature", temperature), ("timeout", timeout)):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (name == "timeout" and value == 0):
                raise ValueError("invalid " + name)
        # The default template is the minimal wire-check form. Live sessions pass
        # the pinned config's template, which truncates long outputs.
        self._observation_template = observation_template or OBSERVATION_TEMPLATE
        self.config = {"model_name": model_name, "api_base": api_base, "max_tokens": max_tokens,
                       "temperature": temperature, "timeout": timeout}
        self._api_key = api_key
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def query(self, messages):
        """Send once, retain response evidence and parse with upstream text rules."""
        body = {"model": self.config["model_name"], "messages": text_messages(messages),
                "max_tokens": self.config["max_tokens"], "temperature": self.config["temperature"],
                "n": 1, "stream": False}
        request = Request(self.config["api_base"] + "/chat/completions",
                          data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._api_key})
        try:
            with self._opener.open(request, timeout=self.config["timeout"]) as reply:
                data = reply.read(RESPONSE_LIMIT + 1)
        except HTTPError as error:
            # HTTPError owns a response stream too. Close it before propagating;
            # an HTTP failure must not trigger a hidden transport retry.
            error.close()
            raise
        if len(data) > RESPONSE_LIMIT:
            raise ValueError("response exceeds byte limit")
        response = json.loads(data)
        content = assistant_text(response, self.config["model_name"])
        from minisweagent.exceptions import FormatError
        from minisweagent.models.utils.actions_text import parse_regex_actions

        try:
            actions = parse_regex_actions(content, action_regex=ACTION_REGEX,
                                          format_error_template=FORMAT_ERROR,
                                          template_kwargs={"finish_reason": "stop"})
        except FormatError as error:
            error.messages[0].setdefault("extra", {})["response"] = response
            raise
        return {"role": "assistant", "content": content, "extra": {"actions": actions, "response": response}}

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        """Use the pinned upstream observation formatter, with multimodal off."""
        from minisweagent.models.utils.actions_text import format_observation_messages
        return format_observation_messages(outputs, observation_template=self._observation_template,
                                           template_vars=template_vars, multimodal_regex="")

    def get_template_vars(self):
        return dict(self.config)

    def serialize(self):
        return {"info": {"config": {"model": dict(self.config),
                                    "model_type": "direct_model.DirectTextModel"},
                         "cost_tracking": "unavailable"}}
