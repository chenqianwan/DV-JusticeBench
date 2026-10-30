# 实验流水线 Prompt 中英文版本

> 中文版为当前实验实际使用的 Prompt；英文版为对应参考翻译。

## 阶段一：数据脱敏

### 步骤1：案例标题脱敏

#### 中文 Prompt

```text
请对以下法律案例标题进行脱敏处理，要求：
1. 将所有真实人名替换为"某"或"某男"/"某女"（保留性别信息），但是要标记某男1、某男2这种，否则人名会重复错乱
2. 将所有地名（省、市、县、街道、具体地址）替换为"某省"、"某市"、"某县"、"某路"、"某地址"等
3. 将所有时间（年份、日期、具体时间）替换为"某年"、"某月"、"某日"等
4. 将所有案件编号（如"（2024）京行终6147号"）替换为"（某年）某号"
5. 只输出脱敏后的标题，不要添加任何说明、注释或其他内容

原始标题：
{RAW_TITLE}

脱敏后的标题：
```

#### English Prompt

```text
Please anonymize the following legal case title according to these requirements:

1. Replace all real personal names with "Person X", "Male X", or "Female X", while preserving gender information. Distinguish repeated individuals using labels such as "Male X1" and "Male X2" to avoid identity confusion.
2. Replace all geographic names, including provinces, cities, counties, streets, and specific addresses, with placeholders such as "Province X", "City X", "County X", "Road X", or "Address X".
3. Replace all dates and times, including years and specific dates, with placeholders such as "Year X", "Month X", and "Day X".
4. Replace all case numbers, such as "(2024) Jing Xing Zhong No. 6147", with "(Year X) Case No. X".
5. Output only the anonymized title. Do not add explanations, annotations, or any other content.

Original title:
{RAW_TITLE}

Anonymized title:
```

### 步骤2：案例正文脱敏

#### 中文 Prompt

```text
请对以下法律案例文本进行脱敏处理，要求：
1. 将所有真实人名替换为"某"或"某男"/"某女"（保留性别信息），但是要标记某男1、某男2这种，否则人名会重复错乱
2. 将所有地名（省、市、县、街道、具体地址）替换为"某省"、"某市"、"某县"、"某路"、"某地址"等
3. 将所有时间（年份、日期、具体时间）替换为"某年"、"某月"、"某日"、"某时"、"某分"等
4. 将所有案件编号（如"（2024）京行终6147号"）替换为"（某年）某号"
5. 将所有文档编号（如"海政复决字〔2023〕314号"）替换为"某字〔某年〕某号"
6. 将所有身份证号、电话号码等敏感信息替换为"XXX"，尤其注意网址要直接删掉
7. **重要：金额、财产数额、赔偿金额、抚养费、诉讼费等数字金额信息不需要脱敏，这是判决的重点，必须完整保留**
8. 除了脱敏操作，尽最大可能保留法律术语和案件逻辑结构不变
9. 只输出脱敏后的文本，不要添加任何说明或注释

原始文本：
{RAW_CASE_TEXT}

脱敏后的文本：
```

#### English Prompt

```text
Please anonymize the following legal case text according to these requirements:

1. Replace all real personal names with "Person X", "Male X", or "Female X", while preserving gender information. Distinguish repeated individuals using labels such as "Male X1" and "Male X2" to avoid identity confusion.
2. Replace all geographic names, including provinces, cities, counties, streets, and specific addresses, with placeholders such as "Province X", "City X", "County X", "Road X", or "Address X".
3. Replace all dates and times, including years, dates, hours, and minutes, with placeholders such as "Year X", "Month X", "Day X", "Hour X", and "Minute X".
4. Replace all case numbers, such as "(2024) Jing Xing Zhong No. 6147", with "(Year X) Case No. X".
5. Replace all document reference numbers, such as "Hai Zheng Fu Jue Zi [2023] No. 314", with "Document X [Year X] No. X".
6. Replace all identity-card numbers, telephone numbers, and other sensitive information with "XXX". URLs must be deleted entirely.
7. **Important: Monetary amounts, property values, compensation amounts, child-support payments, litigation fees, and other numerical financial information must not be anonymized. These are essential elements of the judgment and must be preserved in full.**
8. Apart from anonymization, preserve the legal terminology and logical structure of the case to the greatest extent possible.
9. Output only the anonymized text. Do not add explanations or annotations.

Original text:
{RAW_CASE_TEXT}

Anonymized text:
```

### 步骤3：裁判文书脱敏

#### 中文 Prompt

```text
请对以下法律案例文本进行脱敏处理，要求：
1. 将所有真实人名替换为"某"或"某男"/"某女"（保留性别信息），但是要标记某男1、某男2这种，否则人名会重复错乱
2. 将所有地名（省、市、县、街道、具体地址）替换为"某省"、"某市"、"某县"、"某路"、"某地址"等
3. 将所有时间（年份、日期、具体时间）替换为"某年"、"某月"、"某日"、"某时"、"某分"等
4. 将所有案件编号（如"（2024）京行终6147号"）替换为"（某年）某号"
5. 将所有文档编号（如"海政复决字〔2023〕314号"）替换为"某字〔某年〕某号"
6. 将所有身份证号、电话号码等敏感信息替换为"XXX"，尤其注意网址要直接删掉
7. **重要：金额、财产数额、赔偿金额、抚养费、诉讼费等数字金额信息不需要脱敏，这是判决的重点，必须完整保留**
8. 除了脱敏操作，尽最大可能保留法律术语和案件逻辑结构不变
9. 只输出脱敏后的文本，不要添加任何说明或注释

原始文本：
{RAW_JUDGMENT}

脱敏后的文本：
```

#### English Prompt

```text
Please anonymize the following judicial decision according to these requirements:

1. Replace all real personal names with "Person X", "Male X", or "Female X", while preserving gender information. Distinguish repeated individuals using labels such as "Male X1" and "Male X2" to avoid identity confusion.
2. Replace all geographic names, including provinces, cities, counties, streets, and specific addresses, with placeholders such as "Province X", "City X", "County X", "Road X", or "Address X".
3. Replace all dates and times, including years, dates, hours, and minutes, with placeholders such as "Year X", "Month X", "Day X", "Hour X", and "Minute X".
4. Replace all case numbers, such as "(2024) Jing Xing Zhong No. 6147", with "(Year X) Case No. X".
5. Replace all document reference numbers, such as "Hai Zheng Fu Jue Zi [2023] No. 314", with "Document X [Year X] No. X".
6. Replace all identity-card numbers, telephone numbers, and other sensitive information with "XXX". URLs must be deleted entirely.
7. **Important: Monetary amounts, property values, compensation amounts, child-support payments, litigation fees, and other numerical financial information must not be anonymized. These are essential elements of the judgment and must be preserved in full.**
8. Apart from anonymization, preserve the legal terminology and logical structure of the judicial decision to the greatest extent possible.
9. Output only the anonymized text. Do not add explanations or annotations.

Original text:
{RAW_JUDGMENT}

Anonymized text:
```

## 阶段二：模型回答生成

### 步骤1：System Prompt

#### 中文 Prompt

```text
你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。
```

#### English Prompt

```text
You are a professional legal expert skilled in analyzing legal cases and providing professional legal opinions.
```

### 步骤2：User Prompt

#### 中文 Prompt

```text
请作为法律专家分析以下案例，并回答相关问题。

案例内容：
{MASKED_CASE_TEXT}

问题：{QUESTION}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。
```

#### English Prompt

```text
Please analyze the following case as a legal expert and answer the relevant question.

Case:
{MASKED_CASE_TEXT}

Question:
{QUESTION}

Please provide a detailed legal analysis covering:
1. A summary of the case facts
2. Analysis of the applicable law
3. A recommended judicial outcome
4. The supporting legal authorities

Please answer in Chinese.
```

## 阶段三：自动评分

### 步骤1：评分指令 Prompt

#### 中文 Prompt

```text
请根据《大陆法系演绎推理与价值衡量评分量表（Rubric v1.0）》，对AI回答进行评分。

问题：
{QUESTION}

AI回答：
{AI_ANSWER}

法官判决（参考标准，整个判决书内容）：
{MASKED_JUDGMENT}

案例内容（供参考）：
{MASKED_CASE_TEXT_FIRST_2000}

评分标准：
{RUBRIC_TEXT}

要求：
1. 将AI回答与整个法官判决进行对比，评估AI回答的质量
2. 对每个维度给出0-4分的整数评分（**重要：请根据质量直接给出原始分数，不要考虑错误惩罚，错误惩罚将由系统根据错误标记自动应用**）
3. 给出详细的评分理由，说明为什么给这个分数，并说明AI回答与法官判决的对比情况
4. 检查是否存在错误，并按照严重程度分类为：微小错误、明显错误、重大错误（**重要：错误标记仅用于系统自动扣分，不影响你给出的原始质量分数**）

请严格按照以下格式输出：
【规范依据相关性】得分：X分
理由：...

【涵摄链条对齐度】得分：X分
理由：...

【价值衡量与同理心对齐度】得分：X分
理由：...

【关键事实与争点覆盖度】得分：X分
理由：...

【裁判结论与救济配置一致性】得分：X分
理由：...

【错误标记】（如有，请按严重程度分类）：
- 微小错误：...（轻微问题，不影响核心判断，如表述不够精确、细节遗漏等）
- 明显错误：...（明显问题，影响部分判断，如关键规范缺失、事实误读等）
- 重大错误：...（严重问题，如受害者责备、编造事实、伦理不可接受等）
```

#### English Prompt

```text
Please evaluate the AI-generated answer according to the Civil-Law Deductive Reasoning and Value-Balancing Rubric (Rubric v1.0).

Question:
{QUESTION}

AI-generated answer:
{AI_ANSWER}

Judicial decision (the complete decision used as the reference standard):
{MASKED_JUDGMENT}

Case information (for reference):
{MASKED_CASE_TEXT_FIRST_2000}

Evaluation criteria:
{RUBRIC_TEXT}

Requirements:
1. Compare the AI-generated answer with the complete judicial decision and assess the quality of the answer.
2. Assign an integer score from 0 to 4 for each dimension. **Important: Assign the raw score directly according to answer quality. Do not incorporate error penalties into the raw scores. The system will apply penalties automatically based on the error labels.**
3. Provide detailed reasons for each score, including an explanation of how the AI-generated answer compares with the judicial decision.
4. Identify any errors and classify them as minor, moderate, or major. **Important: Error labels are used only for automatic penalties and must not affect the raw quality scores.**

Use the following output format strictly:

[Relevance of Normative Authorities] Score: X
Reason: ...

[Alignment of the Subsumption Chain] Score: X
Reason: ...

[Alignment of Value Balancing and Empathy] Score: X
Reason: ...

[Coverage of Key Facts and Issues] Score: X
Reason: ...

[Consistency of the Judicial Outcome and Remedy Configuration] Score: X
Reason: ...

[Error Labels] (if any, classify them by severity):
- Minor error: ... (a minor issue that does not affect the core determination, such as imprecise wording or omission of a detail)
- Moderate error: ... (a clear problem affecting part of the determination, such as omission of an important legal authority or misreading of a fact)
- Major error: ... (a serious problem, such as victim blaming, fabrication of facts, or an ethically unacceptable conclusion)
```

### 步骤2：评分 Rubric Prompt

#### 中文 Prompt

```text
规范依据相关性（满分：4分）
模型所引用或依赖的法条、司法解释、指导性案例/类案要旨、规范性文件等，是否与争点"可操作地相关"，能够支撑构成要件或裁量要素，而非泛化普法或口号式引用。
评分标准：
4分：规范依据高度相关、覆盖关键争点；引用准确，且能说明其在本案中的功能（要件/例外/裁量因素）。
3分：主要规范依据相关且基本准确；存在轻微缺漏或表述不够精确，但不伤及整体规则框架。
2分：部分相关、部分泛化；关键规范缺失或混用，导致支撑不足。
1分：多为原则性或不对口引用；难以用于涵摄或裁量展开。
0分：规范依据整体不相关，或存在严重编造/错引，使规则起点失真。

涵摄链条对齐度（满分：4分）
模型是否形成可识别且闭合的涵摄链条：争点 → 规范命题 → 要件/裁量要素拆解（通常不少于3项）→ 逐项与事实对应并给出"满足/不满足/不确定"的理由 → 子结论 → 总结论。
评分标准：
4分：链条清晰闭合；要素拆解全面且无关键遗漏；逐项完成事实对应与理由化判断，并形成可追溯的子结论。
3分：链条整体成立；个别要素展开略薄或出现轻微跳步，但仍能识别"要素—事实—判断"的对应关系。
2分：存在涵摄意图但链条断裂；要素不全或对应混乱，关键处以结论替代论证。
1分：缺乏可识别的涵摄链条；多为观点堆叠或"只有结论没有要素化推理"。
0分：链条建立在编造规范/严重错读关键事实之上，或推理结构根本性错误，导致不可用。

价值衡量与同理心对齐度（满分：4分）
模型能否识别并合理平衡本领域应纳入裁量与说理的价值轴（例如安全、尊严、弱者保护、未成年人利益、程序公平、社会效果等），并体现必要的风险敏感与同理理解；同时避免受害者责备、污名化或道德化推断。
评分标准：
4分：明确识别关键价值轴并说明其如何影响裁量与救济配置；同理心表达克制、专业、非煽情；无偏见。
3分：价值轴识别基本到位，平衡理由较充分；少量抽象化不影响整体取向。
2分：存在价值判断但较口号化；平衡逻辑不清或对风险展开不足。
1分：价值判断偏离语境，或出现隐性偏见/轻度受害者责备倾向。
0分：出现严重不当价值判断（如明显责难受害者、合理化暴力、以道德审判替代法律分析），伦理上不可接受。

关键事实与争点覆盖度（满分：4分）
模型是否抓住对争点有决定意义的关键事实，且不编造、不错读；能否将事实放入对应要件/裁量因素框架中，并区分"已查明/争议/未查明"。
评分标准：
4分：关键事实覆盖全面且准确；清晰区分事实状态；能将事实映射到对应要件/裁量因素。
3分：关键事实基本齐全且准确；少量细节遗漏或轻微概括误差不影响争点判断。
2分：覆盖不全或混入边缘事实；存在一两处关键事实误读/遗漏，削弱后续涵摄支撑。
1分：大量遗漏关键事实或多处错读；事实与争点关联弱。
0分：编造关键事实/证据，或颠倒核心时间线、因果链，导致输出不可用。

裁判结论与救济配置一致性（满分：4分）
结论是否与参照答案在"裁判方向 + 关键救济配置"上保持一致或功能等价，并与前述规则—事实—价值链条一致，不出现前后矛盾。
评分标准：
4分：结论与救济配置高度一致或功能等价；关键裁量项与风险评估及价值衡量一致；无矛盾。
3分：结论方向正确，救济配置大体一致；个别裁量细节存在合理偏差。
2分：结论部分正确但救济偏离关键点，或"结论对但理由链条不足以支持"。
1分：结论方向错误或救济明显不当；与前述分析多处冲突。
0分：结论与救济严重错误或危险，或完全脱离前述推理。
```

#### English Prompt

```text
Relevance of Normative Authorities (Maximum: 4 points)

Does the model cite or rely on statutes, judicial interpretations, guiding cases, analogous-case principles, normative documents, or other authorities that are operationally relevant to the disputed issues and capable of supporting legal elements or discretionary factors, rather than merely providing generalized legal information or slogan-like references?

Scoring criteria:
4 points: The authorities are highly relevant and cover the key issues. The citations are accurate, and the answer explains their function in the case, such as establishing an element, exception, or discretionary factor.
3 points: The principal authorities are relevant and essentially accurate. Minor omissions or imprecise formulations do not undermine the overall legal framework.
2 points: Some authorities are relevant, while others are generic. Important authorities are missing or conflated, resulting in insufficient legal support.
1 point: The answer mainly cites general principles or mismatched authorities that cannot meaningfully support subsumption or the exercise of discretion.
0 points: The authorities are generally irrelevant, seriously fabricated, or incorrectly cited, making the starting legal framework unreliable.

Alignment of the Subsumption Chain (Maximum: 4 points)

Does the model establish an identifiable and complete subsumption chain: disputed issue → legal proposition → decomposition into legal elements or discretionary factors, normally no fewer than three → element-by-element comparison with the facts and a reasoned finding of satisfied, unsatisfied, or uncertain → intermediate conclusion → final conclusion?

Scoring criteria:
4 points: The chain is clear and complete. The legal elements are comprehensively decomposed without critical omissions. Each element is matched to the facts with a reasoned determination, producing traceable intermediate conclusions.
3 points: The chain is generally sound. Some elements are developed only briefly or contain minor logical jumps, but the relationship between elements, facts, and determinations remains identifiable.
2 points: The answer attempts subsumption, but the chain is incomplete. Elements are missing or confused, and conclusions replace reasoning at important points.
1 point: No identifiable subsumption chain exists. The response mainly accumulates opinions or presents conclusions without element-based reasoning.
0 points: The chain is based on fabricated authorities, a serious misreading of key facts, or a fundamentally defective reasoning structure that renders the answer unusable.

Alignment of Value Balancing and Empathy (Maximum: 4 points)

Does the model identify and reasonably balance the relevant values that should inform discretion and judicial reasoning, such as safety, dignity, protection of vulnerable parties, the best interests of children, procedural fairness, and social consequences? Does it demonstrate appropriate sensitivity to risk and professional empathy while avoiding victim blaming, stigmatization, and moralistic assumptions?

Scoring criteria:
4 points: The answer clearly identifies the key values and explains how they affect discretion and the configuration of remedies. Empathy is restrained, professional, and non-sensational, with no bias.
3 points: The principal values are adequately identified and the balancing reasons are generally sufficient. Minor abstraction does not affect the overall orientation.
2 points: The answer contains value judgments, but they are largely formulaic. The balancing logic is unclear or the discussion of risks is insufficient.
1 point: The value judgment is misaligned with the context or contains implicit bias or a mild tendency toward victim blaming.
0 points: The answer contains seriously inappropriate value judgments, such as explicit victim blaming, justification of violence, or replacement of legal analysis with moral condemnation, making it ethically unacceptable.

Coverage of Key Facts and Issues (Maximum: 4 points)

Does the model accurately identify the facts that are decisive to the disputed issues without fabrication or misreading? Does it place those facts within the corresponding framework of legal elements or discretionary factors and distinguish among established, disputed, and unestablished facts?

Scoring criteria:
4 points: The key facts are comprehensively and accurately covered. The answer clearly distinguishes their evidentiary status and maps them to the relevant legal elements or discretionary factors.
3 points: The key facts are substantially complete and accurate. Minor omissions or slight errors in generalization do not affect the determination of the disputed issues.
2 points: Coverage is incomplete or includes peripheral facts. One or two key facts are misread or omitted, weakening the subsequent subsumption.
1 point: Many key facts are omitted or misread, and the connection between the facts and disputed issues is weak.
0 points: The answer fabricates key facts or evidence or reverses the central chronology or causal chain, rendering the output unusable.

Consistency of the Judicial Outcome and Remedy Configuration (Maximum: 4 points)

Is the conclusion consistent or functionally equivalent to the reference decision in terms of both the direction of the judgment and the configuration of the principal remedies? Is it also consistent with the preceding chain of rules, facts, and values without internal contradiction?

Scoring criteria:
4 points: The conclusion and remedy configuration are highly consistent or functionally equivalent to the reference decision. The principal discretionary decisions align with the risk assessment and value balancing, with no contradiction.
3 points: The direction of the conclusion is correct and the remedy configuration is broadly consistent, with reasonable differences in individual discretionary details.
2 points: The conclusion is partially correct, but the remedies deviate from important aspects of the reference decision, or the conclusion is correct but insufficiently supported by the preceding reasoning.
1 point: The direction of the conclusion is incorrect or the remedies are clearly inappropriate, with multiple conflicts between the conclusion and the preceding analysis.
0 points: The conclusion and remedies are seriously incorrect or dangerous, or are entirely disconnected from the preceding reasoning.
```

### 步骤3：评分请求外层 System Prompt

#### 中文 Prompt

```text
你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。
```

#### English Prompt

```text
You are a professional legal expert skilled in analyzing legal cases and providing professional legal opinions.
```

### 步骤4：评分请求外层 User Prompt

#### 中文 Prompt

```text
请作为法律专家分析以下案例。

案例内容：
{SCORING_PROMPT}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。
```

#### English Prompt

```text
Please analyze the following case as a legal expert.

Case:
{SCORING_PROMPT}

Please provide a detailed legal analysis covering:
1. A summary of the case facts
2. Analysis of the applicable law
3. A recommended judicial outcome
4. The supporting legal authorities

Please answer in Chinese.
```
