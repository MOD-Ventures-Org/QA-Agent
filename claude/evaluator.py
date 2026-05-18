import json
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI

from config import settings
from utils.logger import get_logger
from claude.prompts import EVALUATOR_SYSTEM, evaluator_user_prompt

logger = get_logger(__name__)
client = OpenAI(api_key=settings.openai_api_key)


@dataclass
class ProductEvaluation:
    quality_score: int = 0
    grade: str = "N/A"
    summary: str = ""
    strengths: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    recommendation: str = "unknown"


def _fallback_evaluation(test_result, reason: str) -> ProductEvaluation:
    score = int((test_result.passed / max(test_result.total, 1)) * 100) if test_result.total > 0 else 0
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    rec = "ship" if score >= 90 else "ship with caution" if score >= 70 else "block"
    return ProductEvaluation(
        quality_score=score,
        grade=grade,
        summary=f"{reason} Score derived from pass rate ({score}%).",
        recommendation=rec,
    )


async def evaluate_product(event, test_plan, test_result) -> ProductEvaluation:
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1024,
            temperature=0,
            messages=[
                {"role": "system", "content": EVALUATOR_SYSTEM},
                {"role": "user", "content": evaluator_user_prompt(event, test_plan, test_result)},
            ],
        )
        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)
        return ProductEvaluation(**{k: v for k, v in data.items() if k in ProductEvaluation.__dataclass_fields__})
    except Exception as e:
        logger.error(f"Product evaluator failed: {e}")
        return _fallback_evaluation(test_result, f"Evaluator error: {e}.")
