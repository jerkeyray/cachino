"""Derive local prompt tokens and adjacent-request prefixes from verified traces.

This is a separate measurement domain: local token IDs are not upstream cache
observations or proof of equivalence to any engine's actual prompt rendering.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

from report import TraceError, body_bytes, read_events
from tokenizer_assets import DEFAULT_PIN, load_pin, verify_assets


class UnsupportedRequest(ValueError):
    """A request needs prompt normalization this renderer has not validated."""


# Calibration requests ask vLLM to return its prompt/generated token IDs. At the
# inspected pin (experiments/cache-ground-truth/source-pin.json) this flag only
# adds response fields and does not enter chat rendering, so it is accepted as
# observational. Other vLLM extras (cache_salt, chat_template, echo, documents,
# continue_final_message, ...) can change rendering or cache identity and stay
# rejected until each has its own validation.
OBSERVATION_FIELDS = {"return_token_ids"}

# Explicit server-side template controls. The server does not see the local pin,
# so a calibration request must state the thinking setting itself.
TEMPLATE_CONTROL_FIELDS = {"chat_template_kwargs", "add_generation_prompt"}

# tool_choice "auto" and "required" render the same tool list into the prompt
# at the inspected pin (vllm/renderers/online_renderer.py L292-297); "required"
# only constrains decoding. "none" can drop the tools when the server sets
# exclude_tools_when_tool_choice_none, and named choices are unvalidated, so
# both stay rejected.
RENDER_NEUTRAL_TOOL_CHOICES = {"auto", "required"}


def template_controls(request, pin):
    """Check request template controls against the pin; return what was explicit.

    At the inspected vLLM pin the server merges template kwargs as launch
    defaults, then request chat_template_kwargs, then the add_generation_prompt
    field, so the field overrides a same-named kwarg. To keep one unambiguous
    meaning, chat_template_kwargs may contain only enable_thinking, and both
    values must equal the pin. A conflicting value raises UnsupportedRequest
    instead of being rendered under the local setting.

    The returned flags record whether the request itself carried each setting.
    A request without them renders locally under the pin, but the server would
    use its own defaults; parity then also depends on recorded launch defaults.
    """
    kwargs = request.get("chat_template_kwargs")
    if kwargs is not None and (not isinstance(kwargs, dict) or set(kwargs) - {"enable_thinking"}):
        raise UnsupportedRequest("chat_template_kwargs may contain only enable_thinking")
    if kwargs and kwargs.get("enable_thinking") is not pin["enable_thinking"]:
        raise UnsupportedRequest("enable_thinking differs from the pinned template setting")
    if "add_generation_prompt" in request and request["add_generation_prompt"] is not pin["add_generation_prompt"]:
        raise UnsupportedRequest("add_generation_prompt differs from the pinned template setting")
    return {"enable_thinking_explicit": bool(kwargs) and "enable_thinking" in kwargs,
            "add_generation_prompt_explicit": "add_generation_prompt" in request}


def prompt_inputs(request, model_id, remap=False):
    """Accept an explicit text/chat/tool subset; reject unknown prompt controls.

    Generation-only settings do not change the rendered prompt. Unknown fields,
    multimodal content and assistant-prefill requests require separate support
    rather than silently being dropped. Source request bytes remain untouched.
    """
    generation_fields = {"stream", "stream_options", "temperature", "top_p", "top_k", "min_p", "max_tokens", "max_completion_tokens", "seed", "stop", "presence_penalty", "frequency_penalty", "repetition_penalty", "n", "logprobs", "top_logprobs"}
    allowed = generation_fields | OBSERVATION_FIELDS | TEMPLATE_CONTROL_FIELDS | {"model", "messages", "tools", "tool_choice"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise UnsupportedRequest("unknown request fields or non-object request")
    choice = request.get("tool_choice")
    if "tool_choice" in request and (not isinstance(choice, str) or choice not in RENDER_NEUTRAL_TOOL_CHOICES
                                     or not request.get("tools")):
        raise UnsupportedRequest("tool_choice must be auto or required, with tools")
    if "return_token_ids" in request and not isinstance(request["return_token_ids"], bool):
        raise UnsupportedRequest("return_token_ids must be a boolean")
    if not isinstance(request.get("model"), str) or not request["model"]:
        raise UnsupportedRequest("an explicit source model is required")
    if request.get("model") != model_id and not remap:
        raise UnsupportedRequest("model differs from the pinned model; explicit remapping required")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise UnsupportedRequest("nonempty messages required")
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content", "tool_calls", "tool_call_id", "reasoning_content"}:
            raise UnsupportedRequest("unknown message fields")
        role = message.get("role")
        if not isinstance(role, str) or role not in {"system", "user", "assistant", "tool"} or not isinstance(message.get("content"), str):
            raise UnsupportedRequest("only string content and supported chat roles are accepted")
        if "reasoning_content" in message and (role != "assistant" or not isinstance(message["reasoning_content"], str)):
            raise UnsupportedRequest("unsupported reasoning content")
        if "tool_call_id" in message and (role != "tool" or not isinstance(message["tool_call_id"], str)):
            raise UnsupportedRequest("unsupported tool call ID")
        if "tool_calls" in message:
            calls = message["tool_calls"]
            if role != "assistant" or not isinstance(calls, list):
                raise UnsupportedRequest("assistant tool calls must be a list")
            for call in calls:
                if not isinstance(call, dict) or set(call) - {"id", "type", "function"} or call.get("type") != "function":
                    raise UnsupportedRequest("only function tool calls are accepted")
                function = call.get("function")
                if not isinstance(function, dict) or set(function) != {"name", "arguments"} or not isinstance(function["name"], str) or not isinstance(function["arguments"], (dict, str)):
                    raise UnsupportedRequest("unsupported function arguments")
                if isinstance(function["arguments"], str):
                    try:
                        if not isinstance(json.loads(function["arguments"]), dict):
                            raise ValueError("arguments must encode an object")
                    except ValueError as error:
                        raise UnsupportedRequest("invalid function argument JSON") from error
    if messages[-1]["role"] not in {"user", "tool"}:
        raise UnsupportedRequest("assistant prefills and non-query final messages are unsupported")
    tools = request.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise UnsupportedRequest("tools must be a list")
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool.get("type") != "function" or not isinstance(tool["function"], dict) or not isinstance(tool["function"].get("name"), str):
                raise UnsupportedRequest("only function tool schemas are accepted")
    return messages, tools


def lcp(previous, current):
    """Count equal leading token IDs, independent of tokenizer or cache residency."""
    for index, (left, right) in enumerate(zip(previous, current)):
        if left != right:
            return index
    return min(len(previous), len(current))


def render(tokenizer, request, pin, remap=False):
    """Render one supported request and cross-check both tokenization paths.

    The template supplies special tokens. Adding special tokens again during
    encoding would change the sequence, so the text path disables that step.
    Agreement here checks the local library paths, not engine token identity.
    """
    messages, tools = prompt_inputs(request, pin["model_id"], remap)
    template_controls(request, pin)
    options = {"add_generation_prompt": pin["add_generation_prompt"], "enable_thinking": pin["enable_thinking"]}
    if tools is not None:
        options["tools"] = tools
    text = tokenizer.apply_chat_template(messages, tokenize=False, **options)
    ids = tokenizer.encode(text, add_special_tokens=False)
    direct = tokenizer.apply_chat_template(messages, tokenize=True, **options)
    if ids != direct:
        raise ValueError("template+encode token IDs differ from direct template tokenization")
    if tokenizer.decode(ids, skip_special_tokens=False, clean_up_tokenization_spaces=False) != text:
        raise ValueError("rendered prompt does not round-trip through the tokenizer")
    digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    return text, ids, digest


def _load_tokenizer(assets, pin):
    """Verify snapshot and runtime provenance before constructing a local tokenizer.

    Keep this boundary separate from trace orchestration so its failure cannot
    create a partial token dataset, and capture-only analysis needs no ML imports.
    """
    manifest = verify_assets(assets, pin)
    libraries = {name: version(name) for name in ("transformers", "tokenizers", "jinja2", "huggingface-hub")}
    if libraries["transformers"] != "4.57.6" or libraries["jinja2"] != "3.1.6":
        raise ValueError("use the pinned analysis/tokenizer environment")
    # Import only for reconstruction: capture reporting and validation helpers
    # still work in the dependency-free base analysis environment.
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(assets), local_files_only=True, trust_remote_code=False)
    if not isinstance(tokenizer.chat_template, str):
        raise ValueError("one explicit chat template is required")
    return tokenizer, manifest, libraries


def reconstruct(root, assets, out, pin, remap=False):
    """Create a new derived token dataset while preserving the raw trace run.

    Unsupported captures remain rows and break the adjacent-request comparison
    chain. Missing session labels never create an inferred shared session.
    """
    tokenizer, manifest, libraries = _load_tokenizer(assets, pin)
    root, out = Path(root).resolve(), Path(out)
    records, warnings = read_events(root)
    out.mkdir(mode=0o700, parents=True)
    rows, previous = [], {}
    for record in records:
        row = {"request_id": record["request_id"], "sequence": record["sequence"], "session_id": record.get("session_id"), "turn": record.get("turn"), "status": "unsupported", "adjacent_lcp_tokens": None, "previous_request_id": None}
        session = row["session_id"]
        predecessor = previous.get(session) if session is not None else None
        if session is not None:
            previous[session] = None
        data, integrity = body_bytes(root, record, "request")
        try:
            if record.get("path") != "/v1/chat/completions":
                raise UnsupportedRequest("only OpenAI chat-completion captures support local reconstruction")
            if data is None:
                raise UnsupportedRequest("request capture is " + integrity)
            request = json.loads(data)
            text, ids, digest = render(tokenizer, request, pin, remap)
        except (UnsupportedRequest, json.JSONDecodeError, UnicodeError) as error:
            row["reason"] = str(error)
            rows.append(row)
            continue
        prefix = record["request_id"]
        (out / (prefix + ".prompt.txt")).write_text(text, encoding="utf-8")
        (out / (prefix + ".tokens.json")).write_text(json.dumps(ids) + "\n")
        row.update(status="rendered", prompt_tokens=len(ids), token_ids_sha256=digest,
                   request_sha256=record["request"]["sha256_observed"],
                   source_model=request.get("model"), model_remapped=request.get("model") != pin["model_id"],
                   return_token_ids_requested=request.get("return_token_ids") is True,
                   **template_controls(request, pin))
        if predecessor is not None:
            row["previous_request_id"] = predecessor[0]
            row["adjacent_lcp_tokens"] = lcp(predecessor[1], ids)
        if session is not None:
            previous[session] = (record["request_id"], ids)
        rows.append(row)
    result = {"schema_version": 1, "source_trace": str(root), "pin": pin, "assets": manifest,
              "template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
              "libraries": libraries,
              "warnings": warnings, "engine_validated": False, "allow_model_remap": remap,
              "comparison_order": "adjacent accepted requests within explicitly labeled sessions",
              "requests": rows}
    (out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path, nargs="?")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    parser.add_argument("--allow-model-remap", action="store_true", help="explicitly render other model requests with the local pinned tokenizer")
    parser.add_argument("--check-setup", action="store_true", help="validate assets and load the pinned tokenizer without creating a dataset")
    args = parser.parse_args()
    if args.check_setup:
        if args.trace_dir is not None or args.out is not None or args.allow_model_remap:
            parser.error("--check-setup does not accept a trace, output path or model remapping")
    elif args.trace_dir is None or args.out is None:
        parser.error("trace_dir and --out are required for reconstruction")
    try:
        pin = load_pin(args.pin)
        if args.check_setup:
            _load_tokenizer(args.assets, pin)
            print("Tokenizer setup verified: %s@%s. No inference performed." % (pin["model_id"], pin["revision"]))
            return
        result = reconstruct(args.trace_dir, args.assets, args.out, pin, args.allow_model_remap)
        print("Rendered %d/%d requests. Engine validation: not performed." % (sum(row["status"] == "rendered" for row in result["requests"]), len(result["requests"])))
    except (OSError, ValueError, KeyError, ImportError, TraceError) as error:
        parser.exit(1, "reconstruction failed: %s\n" % error)


if __name__ == "__main__":
    main()
