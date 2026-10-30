# H1–H5句子编号定位：独立小样本重试版本

用户于2026-10-03授权调整引文定位并重试。本版不覆盖旧提示词、旧回执或ACTIVE_PROTOCOL，不启动全量。五类定义继续直接读取`prompts/hallucination_h1_h5_20261002/taxonomy_user.txt`，字节不变。

## 唯一变化

模型不再自由生成`quote`，改为输出`sentence_ids`；另外四字段`category/reason/correct_content/confidence`不变。程序按原回答的位置区间生成逐字引文，并保留句子编号及偏移量旁表。原回答不清洗、不改写、不规范化空白。

编号表采用`lossless-unicode-spans-v1`，从S000001开始，保留全部字符与顺序。偏移量按Unicode code point计数，end为开区间；在中英文句末标点和换行处分段，跳过小数点、数字列表标记和URL内部点号，纯空白并入相邻段。编号是便于定位的工程片段，不声称完全等同语法句法分析。

已绑定原回答SHA-256的编号表必须可确定性重建。每个事件选择一个或多个严格已知、升序、连续、不重复的编号。程序据第一个片段start至最后一个片段end截取原文。允许同一句中多个不同事件，禁止完全相同的重复事件。

## 科学边界

逐字定位通过，只证明引文确实来自本回答，不能证明选对句子，更不能证明存在法律幻觉。H1–H5边界、H5前置核验、盲化、法源证据要求、NONE条件均沿用之前规则。法律核验不足仍不进入正式统计。

使用本版的诊断请求须另存原始请求、原始回包、新版编号输出、机器生成引文、offset/hash旁表和协议hash。需分别报告JSON错误、编号错误、语义定位待复核与法律核验待完成；不能把消除逐字匹配失败表述为证明法律判定精度提高。

## API

- `build_sentences(answer)`返回`{schema, answer_sha256, offset_unit, sentences:[{id,start,end,text}]}`。
- `validate_sentences(answer, table)`核验当前原文hash、全覆盖、字面文本及确定性重建。
- `build_messages_from_packet(packet)`返回`(messages, anchored_packet)`；输入是旧五字段盲包，实际送出的anchored_packet以`sentence_table`替代原`answer`，原文只出现一次，且等于`json.loads(messages[1]['content'])`。
- `parse_output(text, answer, table=None)`返回裸`NONE`或五字段原生事件列表；不自动纠JSON、不模糊匹配、不修正未知编号。
- `hydrate_output(parsed, answer, table=None)`返回裸`NONE`或`{events:[旧五字段事件], anchors:[编号/偏移/hash旁表]}`。事件引用可供原五字段下游消费；同句多个独立事件不能再沿用旧的“重复quote即无效”限制。
- `answer_hash`、`packet_hash`、`prompt_hashes`、`protocol_hashes`提供可审计hash。
