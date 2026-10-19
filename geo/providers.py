"""
AI app providers. Each one takes a question and returns a ProviderResult:
the parsed answer plus which model actually answered and its token usage.

Request shapes, prompts and error messages are unchanged from
api/scheduled_audit.py. Phase 4 turns this into a formal plug-in interface
(Gemini, Perplexity, Google AI Overviews); for now it's a registry of two.
"""
from dataclasses import dataclass, field

from geo.parsing import ParsedAnswer, parse_claude, parse_openai

WEB_SEARCH_MAX_USES = 5  # Claude: cap searches per query; billed at $10/1,000 searches on top of tokens

RANKING_PROMPT = """\
{prompt}

Use web search to find current, real information before answering, and cite the sources you use. Answer the way you normally would for someone asking this question."""


@dataclass
class ProviderResult:
    parsed: ParsedAnswer = field(default_factory=ParsedAnswer)
    model: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""


_BILLING_HINTS = ("insufficient_quota", "credit_balance", "credit balance", "no credits", "billing")


def _is_billing_error(exc):
    """OpenAI uses HTTP 429 both for 'too many requests' and 'out of credit';
    Anthropic returns 400 'credit balance is too low'. Tell them apart so the
    user is sent to billing, not told to retry."""
    body = getattr(exc, "body", None)
    text = " ".join(str(x) for x in (getattr(exc, "code", ""), body, exc)).lower()
    return any(h in text for h in _BILLING_HINTS)


def _usage_dict(usage, *names):
    if usage is None:
        return {}
    out = {}
    for n in names:
        v = getattr(usage, n, None)
        if isinstance(v, (int, float)):
            out[n] = v
    stu = getattr(usage, "server_tool_use", None)
    searches = getattr(stu, "web_search_requests", None) if stu is not None else None
    if isinstance(searches, int):
        out["web_search_requests"] = searches
    return out


def ask_claude(prompt, api_key, models):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    last_err = ""
    for model in models:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1800,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": WEB_SEARCH_MAX_USES,
                }],
                messages=[{"role": "user", "content": RANKING_PROMPT.format(prompt=prompt)}],
            )
            return ProviderResult(
                parsed=parse_claude(resp.content),
                model=getattr(resp, "model", None) or model,
                usage=_usage_dict(resp.usage, "input_tokens", "output_tokens"),
            )
        except anthropic.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except anthropic.AuthenticationError:
            return ProviderResult(error="Invalid ANTHROPIC_API_KEY — check your environment variables.", model=model)
        except anthropic.RateLimitError:
            return ProviderResult(error="Rate limit reached — try again in a few seconds.", model=model)
        except anthropic.BadRequestError as e:
            if _is_billing_error(e):
                return ProviderResult(error="Anthropic account has no credit left — add credit at console.anthropic.com → Billing.", model=model)
            return ProviderResult(error=f"{type(e).__name__}: {e}", model=model)
        except anthropic.APITimeoutError:
            return ProviderResult(error="Claude API timed out — try again.", model=model)
        except Exception as e:
            return ProviderResult(error=f"{type(e).__name__}: {e}", model=model)
    return ProviderResult(error=f"No working model found. Last: {last_err}")


def ask_openai(prompt, api_key, models):
    import openai
    client = openai.OpenAI(api_key=api_key)
    last_err = ""
    for model in models:
        try:
            resp = client.responses.create(
                model=model,
                input=RANKING_PROMPT.format(prompt=prompt),
                tools=[{"type": "web_search", "search_context_size": "high"}],
                max_output_tokens=1800,
                # Opt-in field: without this, action.sources always comes back
                # empty and rankings silently fall back to citation-only data.
                include=["web_search_call.action.sources"],
            )
            return ProviderResult(
                parsed=parse_openai(resp.output, resp.output_text),
                model=getattr(resp, "model", None) or model,
                usage=_usage_dict(getattr(resp, "usage", None), "input_tokens", "output_tokens"),
            )
        except openai.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except openai.AuthenticationError:
            return ProviderResult(error="Invalid OPENAI_API_KEY — check your environment variables.", model=model)
        except openai.RateLimitError as e:
            if _is_billing_error(e):
                return ProviderResult(error="OpenAI account has no credit left — add credit at platform.openai.com → Settings → Billing.", model=model)
            return ProviderResult(error="Rate limit reached — try again in a few seconds.", model=model)
        except openai.APITimeoutError:
            return ProviderResult(error="OpenAI API timed out — try again.", model=model)
        except Exception as e:
            return ProviderResult(error=f"{type(e).__name__}: {e}", model=model)
    return ProviderResult(error=f"No working model found. Last: {last_err}")


# key → (Results tab name, settings attr for the API key, settings attr for models, function)
_REGISTRY = {
    "claude":  ("Results - Claude",  "anthropic_api_key", "claude_models", ask_claude),
    "chatgpt": ("Results - ChatGPT", "openai_api_key",    "openai_models", ask_openai),
}


def available_providers(settings):
    """Only providers whose API key is configured — everything downstream
    (the Platforms column, tab creation, the audit loop) skips the rest."""
    out = {}
    for key, (tab, key_attr, models_attr, fn) in _REGISTRY.items():
        api_key = getattr(settings, key_attr)
        if api_key:
            models = getattr(settings, models_attr)
            out[key] = {
                "tab": tab,
                "ask": (lambda prompt, _fn=fn, _k=api_key, _m=models: _fn(prompt, _k, _m)),
            }
    return out
