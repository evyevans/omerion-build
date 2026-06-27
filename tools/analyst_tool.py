"""
Analyst tool — standalone entry point for reply sentiment classification.
The reply_webhook.py uses this indirectly; this module can also be imported
directly for testing or manual re-analysis of a stored reply.
"""
import os
import anthropic

from tools.deployment_logger import log_deployment

_VALID_SENTIMENTS = {"POSITIVE", "WARM", "NEUTRAL", "NEGATIVE", "REFERRAL"}
_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


@log_deployment(skill_name="analyst_classify", triggered_by="manual", model="claude-sonnet-4-6")
def classify(reply_text: str) -> dict:
    """
    Returns {"sentiment": str, "valid": bool}.
    sentiment is always one of the 5 valid values (defaults to NEUTRAL on bad output).
    """
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                "Classify the sentiment of this cold outreach reply.\n"
                "Return ONLY one word: POSITIVE, WARM, NEUTRAL, NEGATIVE, or REFERRAL\n\n"
                f"Reply:\n{reply_text[:2000]}"
            ),
        }],
    )
    classify._last_usage = response.usage
    label = response.content[0].text.strip().upper()
    valid = label in _VALID_SENTIMENTS
    return {"sentiment": label if valid else "NEUTRAL", "valid": valid}


if __name__ == "__main__":
    import sys
    text = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    print(classify(text))
