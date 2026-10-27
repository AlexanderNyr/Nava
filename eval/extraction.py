"""Extract a Nava, C or C++ program from a model response.

Models wrap code in Markdown fences, add prose, or emit several blocks. The
rule is deliberately simple and is recorded in every attempt so a run can be
audited:

1. if the text contains fenced blocks, take the *largest* block whose language
   tag is absent or matches the target language (``nava``/``nv``, ``c``,
   ``cpp``/``c++``);
2. if no tagged block matches, take the largest untagged block;
3. if there are no fences, take the whole text.

The extractor never "fixes" code: no imports are added, no wrappers stripped.
If the answer needs repair, that is the model's job and it is recorded as a
repair round, not hidden here.
"""
from __future__ import annotations

import re

FENCE = re.compile(r"```([A-Za-z0-9_+.#-]*)\n(.*?)```", re.DOTALL)
TAGS = {
    "nava": {"nava", "nv"},
    "c": {"c"},
    "cpp": {"cpp", "c++", "cxx", "cc"},
}


def extract_code(text: str, language: str) -> tuple[str, dict]:
    """Return (code, provenance) where provenance explains the choice."""
    blocks = [(match.group(1).strip().lower(), match.group(2)) for match in FENCE.finditer(text)]
    if not blocks:
        return text, {"method": "whole-text", "blocks": 0, "tag": None}
    wanted = TAGS.get(language, set())
    matching = [(tag, body) for tag, body in blocks if tag in wanted]
    untagged = [(tag, body) for tag, body in blocks if tag == ""]
    chosen_pool, method = (matching, "tagged-fence") if matching else (untagged, "untagged-fence")
    if not chosen_pool:
        return text, {"method": "whole-text-fallback", "blocks": len(blocks), "tag": None}
    tag, body = max(chosen_pool, key=lambda item: len(item[1]))
    return body, {"method": method, "blocks": len(blocks), "tag": tag or None}
