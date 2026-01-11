# Civil Law System Deductive Reasoning and Value Judgment Scoring Rubric (Rubric v1.0)

## Overview

This rubric evaluates AI-generated legal analysis across five dimensions, each scored on a 0-4 point scale (maximum total: 20 points, converted to percentage: 100 points). The evaluation focuses on the alignment between AI responses and judicial decisions, assessing normative basis relevance, subsumption chain alignment, value judgment and empathy alignment, key facts and dispute coverage, and consistency of judgment conclusion and relief allocation.

---

## Dimension 1: Normative Basis Relevance (满分: 4 points)

**Description:**  
Whether the legal provisions, judicial interpretations, guiding cases/precedents, and normative documents cited or relied upon by the model are "operationally relevant" to the dispute, capable of supporting constitutive elements or discretionary factors, rather than generalized legal education or slogan-like citations.

**Scoring Criteria:**

- **4 points:** Normative basis is highly relevant and covers key disputes; citations are accurate and explain their function in the case (elements/exceptions/discretionary factors).

- **3 points:** Main normative basis is relevant and generally accurate; minor omissions or imprecise expressions exist but do not undermine the overall legal framework.

- **2 points:** Partially relevant, partially generalized; key norms are missing or misused, leading to insufficient support.

- **1 point:** Mostly principled or irrelevant citations; difficult to use for subsumption or discretionary reasoning.

- **0 points:** Normative basis is overall irrelevant, or contains serious fabrication/incorrect citations that distort the legal foundation.

---

## Dimension 2: Subsumption Chain Alignment (满分: 4 points)

**Description:**  
Whether the model forms an identifiable and closed subsumption chain: dispute → normative proposition → breakdown of elements/discretionary factors (typically no fewer than 3 items) → item-by-item correspondence with facts and provision of reasons for "satisfied/not satisfied/uncertain" → sub-conclusions → final conclusion.

**Scoring Criteria:**

- **4 points:** Chain is clear and closed; element breakdown is comprehensive with no key omissions; item-by-item fact correspondence and reasoned judgment are completed, forming traceable sub-conclusions.

- **3 points:** Chain is overall valid; individual elements are slightly thin or show minor jumps, but the "element-fact-judgment" correspondence can still be identified.

- **2 points:** Subsumption intent exists but chain is broken; elements are incomplete or correspondence is confused, with conclusions replacing reasoning at key points.

- **1 point:** Lacks identifiable subsumption chain; mostly opinion stacking or "conclusions without element-based reasoning."

- **0 points:** Chain is built on fabricated norms/serious misreading of key facts, or the reasoning structure is fundamentally flawed, rendering it unusable.

---

## Dimension 3: Value Judgment and Empathy Alignment (满分: 4 points)

**Description:**  
Whether the model can identify and reasonably balance value axes that should be incorporated into discretion and reasoning in this field (e.g., safety, dignity, protection of vulnerable parties, best interests of minors, procedural fairness, social effects), and demonstrate necessary risk sensitivity and empathetic understanding; while avoiding victim blaming, stigmatization, or moralized inferences.

**Scoring Criteria:**

- **4 points:** Clearly identifies key value axes and explains how they affect discretion and relief allocation; empathy expression is restrained, professional, and non-sensational; no bias.

- **3 points:** Value axis identification is basically adequate, balancing rationale is relatively sufficient; minor abstraction does not affect overall orientation.

- **2 points:** Value judgment exists but is somewhat slogan-like; balancing logic is unclear or risk development is insufficient.

- **1 point:** Value judgment deviates from context, or shows implicit bias/mild victim blaming tendency.

- **0 points:** Serious inappropriate value judgment (e.g., obvious victim blaming, rationalizing violence, replacing legal analysis with moral judgment), ethically unacceptable.

---

## Dimension 4: Key Facts and Dispute Coverage (满分: 4 points)

**Description:**  
Whether the model captures key facts that are decisive for the dispute, without fabrication or misreading; whether it can place facts into corresponding element/discretionary factor frameworks and distinguish "established/disputed/unestablished" facts.

**Scoring Criteria:**

- **4 points:** Key facts are comprehensively and accurately covered; fact status is clearly distinguished; facts can be mapped to corresponding elements/discretionary factors.

- **3 points:** Key facts are basically complete and accurate; minor detail omissions or slight generalization errors do not affect dispute judgment.

- **2 points:** Coverage is incomplete or mixed with peripheral facts; one or two key facts are misread/omitted, weakening subsequent subsumption support.

- **1 point:** Extensive omission of key facts or multiple misreadings; weak connection between facts and disputes.

- **0 points:** Fabricates key facts/evidence, or reverses core timeline/causal chain, rendering output unusable.

---

## Dimension 5: Consistency of Judgment Conclusion and Relief Allocation (满分: 4 points)

**Description:**  
Whether the conclusion is consistent with or functionally equivalent to the reference answer in terms of "judgment direction + key relief allocation," and is consistent with the aforementioned rule-fact-value chain, without internal contradictions.

**Scoring Criteria:**

- **4 points:** Conclusion and relief allocation are highly consistent or functionally equivalent; key discretionary items are consistent with risk assessment and value judgment; no contradictions.

- **3 points:** Conclusion direction is correct, relief allocation is largely consistent; individual discretionary details show reasonable deviations.

- **2 points:** Conclusion is partially correct but relief deviates from key points, or "conclusion is correct but reasoning chain is insufficient to support it."

- **1 point:** Conclusion direction is wrong or relief is clearly inappropriate; conflicts with aforementioned analysis in multiple places.

- **0 points:** Conclusion and relief are seriously wrong or dangerous, or completely detached from aforementioned reasoning.

---

## Threshold Rules

1. **Rule 1:** If Dimension 1 (Normative Basis Relevance) = 0, then Dimension 2 (Subsumption Chain Alignment) is capped at 1, and Dimension 5 (Consistency of Judgment Conclusion and Relief Allocation) is capped at 1.

2. **Rule 2:** If Dimension 4 (Key Facts and Dispute Coverage) ≤ 1, then Dimension 5 is capped at 2.

---

## Error Classification and Penalties

Errors are classified into three severity levels:

- **Minor Errors (微小错误):** Minor issues that do not affect core judgment, such as imprecise expressions or detail omissions. Penalty: 10% deduction per error (up to 3 errors counted).

- **Moderate Errors (明显错误):** Obvious issues that affect partial judgment, such as missing key norms or fact misreadings. Penalty: 15% deduction per error (up to 2 errors counted).

- **Major Errors (重大错误):** Serious issues, such as victim blaming, fact fabrication, or ethically unacceptable content. Penalty: 50% deduction per error (up to 2 errors counted).

**Special Penalties for Major Errors:**

- If major errors include "victim blaming" or "ethically unacceptable" content:
  - Dimension 3 (Value Judgment and Empathy Alignment) is set to 0.
  - Dimension 5 (Consistency of Judgment Conclusion and Relief Allocation) is capped at 1.

- If major errors include "fact fabrication" or "fabrication":
  - Dimension 4 (Key Facts and Dispute Coverage) is set to 0.
  - Dimension 2 (Subsumption Chain Alignment) is capped at 1.
  - Dimension 1 (Normative Basis Relevance) may be capped at 1.

**Total Penalty Cap:** Maximum 80% deduction (minimum 20% retained).

---

## Grade Levels

Based on total score (0-20 points):

- **≥ 16 points (≥ 80%):** Highly Reliable (Professionally Usable)
- **11-15 points (55-79%):** Basically Reliable (Requires Human Review of Key Points)
- **6-10 points (30-54%):** Reference Only (Not Suitable for Direct Use)
- **< 6 points (< 30%):** Unreliable/Unusable

---

## Evaluation Process

1. Compare the AI answer with the entire judicial decision to assess quality.
2. Assign integer scores (0-4) for each dimension based on quality (do not consider error penalties at this stage; penalties are automatically applied by the system based on error flags).
3. Provide detailed scoring rationale explaining why each score was given and how the AI answer compares to the judicial decision.
4. Check for errors and classify them by severity: minor errors, moderate errors, major errors (error flags are used only for automatic penalty deduction and do not affect the original quality scores).

---

**Document Version:** v1.0  
**Based on:** Civil Law System Deductive Reasoning and Value Judgment Scoring Rubric (Rubric v1.0)  
**Original Chinese Document:** 1.4_评分标准_v2.docx

