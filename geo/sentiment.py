"""
Sentiment: how positively an AI answer portrays the client, 0–100, via one
cheap Claude Haiku call per answer that names the client. Scored once and
stored on answers.sentiment; the nightly metrics job averages it.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import select

from geo.models import Answer, Client, Mention

PROMPT = """\
Below is an AI assistant's answer to the question: "{question}"

How does this answer portray {brand} ({domain})? Judge only what it says about {brand}, on a 0-100 scale:
  0   = strongly negative (warns against it, highlights serious problems)
  25  = mostly negative
  50  = neutral (merely listed or mentioned without judgement)
  75  = positive (recommended among others, favourable points)
  100 = strongly positive (the top or only recommendation, clearly praised)

<answer>
{answer}
</answer>"""

_SCHEMA = {
    "type": "object",
    "properties": {"sentiment": {"type": "integer"}},
    "required": ["sentiment"],
    "additionalProperties": False,
}


def score_one(answer_text, question, brand, domain, api_key, model):
    """Returns (score 0-100, None) or (None, error string). Never raises."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                question=question, brand=brand or domain, domain=domain, answer=answer_text[:60000])}],
        )
        if resp.stop_reason in ("refusal", "max_tokens"):
            return None, f"stopped: {resp.stop_reason}"
        text = next((b.text for b in resp.content if b.type == "text"), "")
        value = int(json.loads(text)["sentiment"])
        return max(0, min(100, value)), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def answers_needing_sentiment(session, since: datetime, limit: int):
    """Successful answers since `since` that name the client (own domain or
    brand mentioned in the text) and have answer text but no score yet."""
    named = (select(Mention.answer_id)
             .where(Mention.entity_type.in_(("own", "own brand")), Mention.mentioned.is_(True)))
    return session.execute(
        select(Answer, Client).join(Client, Answer.client_id == Client.id)
        .where(Answer.created_at >= since, Answer.error.is_(None), Answer.answer_text.is_not(None),
               Answer.sentiment.is_(None), Answer.id.in_(named))
        .order_by(Answer.created_at).limit(limit)).all()


def score_pending(session, settings, since: datetime, limit=300):
    """Score every pending answer (in parallel), write results. Returns
    {"scored": n, "failed": n}. A no-op without an Anthropic key."""
    if not (settings.sentiment_enabled and settings.anthropic_api_key):
        return {"scored": 0, "failed": 0, "skipped": "no ANTHROPIC_API_KEY or SENTIMENT_ENABLED=false"}
    rows = answers_needing_sentiment(session, since, limit)
    if not rows:
        return {"scored": 0, "failed": 0}

    def work(row):
        answer, client = row
        return answer, score_one(answer.answer_text, answer.question_text, client.brand, client.domain,
                                 settings.anthropic_api_key, settings.sentiment_model)

    scored = failed = 0
    with ThreadPoolExecutor(max_workers=min(settings.max_workers, len(rows))) as pool:
        for answer, (value, err) in pool.map(work, rows):
            if value is None:
                failed += 1
                answer.usage = {**(answer.usage or {}), "sentiment_error": (err or "")[:300]}
            else:
                scored += 1
                answer.sentiment = value
                answer.usage = {**(answer.usage or {}), "sentiment_model": settings.sentiment_model}
    session.flush()
    return {"scored": scored, "failed": failed}
