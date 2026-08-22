import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CASE_COUNT = Number(process.env.CROSS_JUDGE_CASE_COUNT || 20);
const EXPECTED_EXPERT_ROWS = Number(process.env.CROSS_JUDGE_EXPERT_COUNT || 125);
const BASE_SAMPLE_COUNT = CASE_COUNT * 5;
const DISAGREEMENT_SAMPLE_COUNT = EXPECTED_EXPERT_ROWS - BASE_SAMPLE_COUNT;
const OUTPUT_DIR = process.env.CROSS_JUDGE_OUTPUT_DIR
  || path.join(ROOT, "outputs/20260822_cross_family_judging_sample20");
const SAMPLE_JSON = process.env.CROSS_JUDGE_SAMPLE_JSON
  || path.join(OUTPUT_DIR, `专家复核样本_${EXPECTED_EXPERT_ROWS}份.json`);
const OUTPUT_XLSX = process.env.CROSS_JUDGE_OUTPUT_XLSX
  || path.join(OUTPUT_DIR, `${CASE_COUNT}案最终评价_专家复核打标表.xlsx`);
const PREVIEW_DIR = path.join(OUTPUT_DIR, "previews");

const COLORS = {
  navy: "#17365D",
  blue: "#D9EAF7",
  human: "#FFF2CC",
  adjudication: "#E2F0D9",
  auto: "#E7E6E6",
  warning: "#FCE4D6",
  white: "#FFFFFF",
  border: "#B7C9D6",
  text: "#1F2937",
};

function txt(value) {
  if (value === null || value === undefined) return "";
  const s = Array.from(String(value)).filter((ch) => {
    const cp = ch.codePointAt(0);
    return cp === 0x09 || cp === 0x0A || cp === 0x0D
      || (cp >= 0x20 && cp <= 0xD7FF)
      || (cp >= 0xE000 && cp <= 0xFFFD)
      || (cp >= 0x10000 && cp <= 0x10FFFF);
  }).join("");
  return s.length > 32760 ? `${s.slice(0, 32740)}\n[超出Excel单元格限制，已截断]` : s;
}

function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function colName(n) {
  let out = "";
  for (let x = n; x > 0; x = Math.floor((x - 1) / 26)) {
    out = String.fromCharCode(65 + ((x - 1) % 26)) + out;
  }
  return out;
}

function setColumnWidths(sheet, widths) {
  widths.forEach((width, i) => {
    sheet.getRange(`${colName(i + 1)}:${colName(i + 1)}`).format.columnWidth = width;
  });
}

function writeRows(sheet, headers, rows, chunkSize = 50) {
  sheet.getRange(`A1:${colName(headers.length)}1`).values = [headers];
  for (let start = 0; start < rows.length; start += chunkSize) {
    const chunk = rows.slice(start, start + chunkSize);
    const firstRow = start + 2;
    const lastRow = firstRow + chunk.length - 1;
    sheet.getRange(`A${firstRow}:${colName(headers.length)}${lastRow}`).values = chunk;
  }
}

function styleTable(sheet, rowCount, columnCount) {
  const last = colName(columnCount);
  sheet.getRange(`A1:${last}1`).format = {
    fill: COLORS.navy,
    font: { bold: true, color: COLORS.white, size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { color: COLORS.border, style: "continuous", weight: 1 },
  };
  sheet.getRange(`A1:${last}1`).format.rowHeight = 46;
  sheet.getRange(`A2:${last}${rowCount + 1}`).format = {
    font: { color: COLORS.text, size: 10 },
    verticalAlignment: "top",
    wrapText: true,
    borders: { color: "#D9E2F3", style: "continuous", weight: 1 },
  };
  sheet.getRange(`A2:${last}${rowCount + 1}`).format.rowHeight = 56;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
}

function errorPresence(errors) {
  return Object.fromEntries(["微小错误", "明显错误", "重大错误"].map((level) => [
    level,
    Array.isArray(errors?.[level]) && errors[level].length ? "有" : "无",
  ]));
}

await fs.mkdir(OUTPUT_DIR, { recursive: true });
await fs.mkdir(PREVIEW_DIR, { recursive: true });
const sample = JSON.parse(await fs.readFile(SAMPLE_JSON, "utf8"));

if (sample.length !== EXPECTED_EXPERT_ROWS) throw new Error(`Expected ${EXPECTED_EXPERT_ROWS} expert items, got ${sample.length}`);
if (new Set(sample.map((item) => item.blind_id)).size !== EXPECTED_EXPERT_ROWS) throw new Error("Blind IDs are not unique");

const workbook = Workbook.create();

// Sheet 1: concise protocol and full 0–4 anchors.
const guide = workbook.worksheets.add("标注说明");
const guideRows = [
  [`${CASE_COUNT}案最终评价专家复核`, "仅复核最终评价；不重新评价问题生成或回答生成过程。"],
  ["样本", `${EXPECTED_EXPERT_ROWS}份回答：${BASE_SAMPLE_COUNT}份基础平衡样本覆盖全部${BASE_SAMPLE_COUNT}个问题；${DISAGREEMENT_SAMPLE_COUNT}份高分歧加抽样；五种回答条件各${EXPECTED_EXPERT_ROWS / 5}份。`],
  ["盲法", "两名法律专家只使用“专家1标注/专家2标注”页独立评分；标注完成并锁定前，不查看“仲裁与分析”页。"],
  ["评分顺序", "先阅读全文与参考裁判，再对五维分别给0–4整数原始分；不要人工执行错误惩罚。"],
  ["错误标记", "分别标记微小/明显/重大错误为“无/有”，并在错误说明中写明可核对的事实或规范依据。"],
  ["仲裁触发", "任一维度两专家相差>1分、重大错误标记不一致、或原始总分相差>4分时，由第三名专家仲裁。"],
  ["数据单位", "统计推断按case聚类；Excel每行是一份“case×问题×回答条件”的复核单位。"],
  ["维度1：规范依据相关性", "4=关键规范高度相关且准确并说明功能；3=主要相关，轻微缺漏；2=部分相关且关键规范不足；1=多为原则性或不对口；0=整体不相关或严重编造/错引。"],
  ["维度2：涵摄链条对齐度", "4=争点—规则—要件—事实—子结论—总结果闭合；3=整体成立，轻微跳步；2=有涵摄意图但关键断裂；1=观点堆叠或只有结论；0=结构根本错误。"],
  ["维度3：价值衡量与同理心", "4=关键价值轴充分平衡且克制专业；3=价值识别基本到位；2=较口号化或风险展开不足；1=偏离语境或隐性偏见；0=严重受害者责备或伦理不可接受。"],
  ["维度4：关键事实与争点覆盖", "4=关键事实全面准确并区分事实状态；3=基本齐全，轻微遗漏；2=覆盖不全或一两处关键误读；1=大量遗漏/错读；0=编造关键事实或颠倒核心链条。"],
  ["维度5：结论与救济一致性", "4=裁判方向和关键救济高度一致/功能等价；3=方向正确且救济大体一致；2=部分正确但偏离关键点；1=方向错误或救济明显不当；0=严重错误、危险或完全脱离推理。"],
  ["微小错误", "轻微不精确或细节遗漏，不影响核心判断。"],
  ["明显错误", "影响部分判断的关键规范缺失、事实误读或推理缺口。"],
  ["重大错误", "编造关键事实/规范、裁判方向严重错误、受害者责备或伦理不可接受。"],
  ["专家页黄色区域", "需人工填写；原始总分由五维自动求和。"],
  ["仲裁页绿色区域", "仅当“需仲裁=是”时由第三名专家填写；灰色区域为机器评分和模型身份，盲评锁定前勿查看。"],
];
guide.getRange(`A1:B${guideRows.length}`).values = guideRows;
guide.getRange("A1:B1").merge();
guide.getRange("A1").values = [[`${CASE_COUNT}案最终评价跨模型互评：专家复核说明`]];
guide.getRange("A1:B1").format = {
  fill: COLORS.navy,
  font: { bold: true, color: COLORS.white, size: 14 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
guide.getRange(`A2:A${guideRows.length}`).format = {
  fill: COLORS.blue,
  font: { bold: true, color: COLORS.text },
  verticalAlignment: "top",
  wrapText: true,
  borders: { color: COLORS.border, style: "continuous", weight: 1 },
};
guide.getRange(`B2:B${guideRows.length}`).format = {
  font: { color: COLORS.text },
  verticalAlignment: "top",
  wrapText: true,
  borders: { color: COLORS.border, style: "continuous", weight: 1 },
};
guide.getRange(`A1:B${guideRows.length}`).format.rowHeight = 48;
guide.getRange("A:A").format.columnWidth = 28;
guide.getRange("B:B").format.columnWidth = 110;
guide.showGridLines = false;
guide.freezePanes.freezeRows(1);

const expertHeaders = [
  "序号", "盲审ID", "案件ID", "脱敏标题", "问题编号", "问题", "脱敏案情", "参考裁判", "AI回答（盲审）",
  "标注状态", "规范依据相关性(0-4)", "涵摄链条对齐度(0-4)", "价值衡量与同理心(0-4)",
  "关键事实与争点覆盖(0-4)", "裁判结论与救济一致性(0-4)", "原始总分", "微小错误", "明显错误", "重大错误",
  "错误说明", "总体评价", "标注员", "标注日期", "置信度",
];

function expertRows() {
  return sample.map((item) => [
    item.sequence,
    item.blind_id,
    item.case_id,
    item.masked_title,
    item.question_number,
    item.question,
    item.masked_case_text,
    item.masked_judgment,
    item.answer,
    "待标注",
    "", "", "", "", "", "", "", "", "", "", "", "", "", "",
  ]);
}

function buildExpertSheet(name, tableName) {
  const sheet = workbook.worksheets.add(name);
  const rows = expertRows();
  writeRows(sheet, expertHeaders, rows, 25);
  styleTable(sheet, rows.length, expertHeaders.length);
  sheet.freezePanes.freezeColumns(6);
  sheet.tables.add(`A1:X${rows.length + 1}`, true, tableName);
  setColumnWidths(sheet, [8, 12, 27, 28, 9, 50, 78, 62, 78, 13, 18, 19, 20, 22, 22, 13, 13, 13, 13, 50, 50, 12, 14, 13]);
  sheet.getRange(`A2:I${rows.length + 1}`).format.fill = COLORS.blue;
  sheet.getRange(`J2:X${rows.length + 1}`).format.fill = COLORS.human;
  sheet.getRange(`J2:J${rows.length + 1}`).dataValidation = { rule: { type: "list", values: ["待标注", "已完成", "需复核"] } };
  sheet.getRange(`K2:O${rows.length + 1}`).dataValidation = { rule: { type: "whole", operator: "between", formula1: 0, formula2: 4 } };
  for (const col of ["Q", "R", "S"]) {
    sheet.getRange(`${col}2:${col}${rows.length + 1}`).dataValidation = { rule: { type: "list", values: ["无", "有"] } };
  }
  sheet.getRange(`X2:X${rows.length + 1}`).dataValidation = { rule: { type: "list", values: ["高", "中", "低"] } };
  sheet.getRange("P2").formulas = [["=IF(COUNT(K2:O2)<5,\"\",SUM(K2:O2))"]];
  sheet.getRange(`P2:P${rows.length + 1}`).fillDown();
  sheet.getRange(`K2:P${rows.length + 1}`).format.numberFormat = "0.00";
  return sheet;
}

const expert1 = buildExpertSheet("专家1标注", "Expert1Annotations");
const expert2 = buildExpertSheet("专家2标注", "Expert2Annotations");

const arbitrationHeaders = [
  "序号", "盲审ID", "样本层", "案件ID", "回答条件（盲评锁定前勿看）",
  "专家1总分", "专家2总分", "总分差", "专家1重大错误", "专家2重大错误", "最大维度差", "需仲裁",
  "仲裁状态", "仲裁_规范依据", "仲裁_涵摄链条", "仲裁_价值与同理心", "仲裁_事实与争点", "仲裁_结论与救济",
  "仲裁_微小错误", "仲裁_明显错误", "仲裁_重大错误", "仲裁说明", "仲裁员", "仲裁日期",
  "原DeepSeek单评分", "跨家族共识分", "评分者1及总分", "评分者2及总分", "评分者3及总分", "三方总分极差", "重大错误票数",
];

const arbitrationRows = sample.map((item) => {
  const ratings = item.machine_ratings || [];
  const ratingCells = ratings.map((rating) => `${rating.judge_label}: ${rating.total_score}`);
  while (ratingCells.length < 3) ratingCells.push("");
  return [
    item.sequence,
    item.blind_id,
    item.review_stratum,
    item.case_id,
    item.answer_condition,
    "", "", "", "", "", "", "",
    "待处理", "", "", "", "", "", "", "", "", "", "", "",
    num(item.original_deepseek_total),
    num(item.consensus?.total_score),
    ratingCells[0], ratingCells[1], ratingCells[2],
    num(item.consensus?.judge_score_range),
    num(item.consensus?.error_vote_counts?.["重大错误"]),
  ];
});

const arbitration = workbook.worksheets.add("仲裁与分析");
writeRows(arbitration, arbitrationHeaders, arbitrationRows, 25);
styleTable(arbitration, arbitrationRows.length, arbitrationHeaders.length);
arbitration.freezePanes.freezeColumns(5);
arbitration.tables.add(`A1:AE${arbitrationRows.length + 1}`, true, "AdjudicationAnalysis");
setColumnWidths(arbitration, [8, 12, 16, 27, 28, 13, 13, 12, 16, 16, 14, 12, 13, 18, 18, 20, 20, 20, 16, 16, 16, 48, 12, 14, 16, 16, 24, 24, 24, 16, 16]);
arbitration.getRange(`A2:E${arbitrationRows.length + 1}`).format.fill = COLORS.warning;
arbitration.getRange(`F2:L${arbitrationRows.length + 1}`).format.fill = COLORS.blue;
arbitration.getRange(`M2:X${arbitrationRows.length + 1}`).format.fill = COLORS.adjudication;
arbitration.getRange(`Y2:AE${arbitrationRows.length + 1}`).format.fill = COLORS.auto;

// Pull the two independent labels and calculate the pre-specified adjudication trigger.
arbitration.getRange("F2").formulas = [["='专家1标注'!P2"]];
arbitration.getRange(`F2:F${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("G2").formulas = [["='专家2标注'!P2"]];
arbitration.getRange(`G2:G${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("H2").formulas = [["=IF(OR(F2=\"\",G2=\"\"),\"\",ABS(F2-G2))"]];
arbitration.getRange(`H2:H${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("I2").formulas = [["='专家1标注'!S2"]];
arbitration.getRange(`I2:I${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("J2").formulas = [["='专家2标注'!S2"]];
arbitration.getRange(`J2:J${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("K2").formulas = [["=IF(OR('专家1标注'!P2=\"\",'专家2标注'!P2=\"\"),\"\",MAX(ABS('专家1标注'!K2-'专家2标注'!K2),ABS('专家1标注'!L2-'专家2标注'!L2),ABS('专家1标注'!M2-'专家2标注'!M2),ABS('专家1标注'!N2-'专家2标注'!N2),ABS('专家1标注'!O2-'专家2标注'!O2)))"]];
arbitration.getRange(`K2:K${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange("L2").formulas = [["=IF(OR(F2=\"\",G2=\"\"),\"待两位专家完成\",IF(OR(H2>4,K2>1,I2<>J2),\"是\",\"否\"))"]];
arbitration.getRange(`L2:L${arbitrationRows.length + 1}`).fillDown();
arbitration.getRange(`M2:M${arbitrationRows.length + 1}`).dataValidation = { rule: { type: "list", values: ["待处理", "已仲裁", "无需仲裁"] } };
arbitration.getRange(`N2:R${arbitrationRows.length + 1}`).dataValidation = { rule: { type: "whole", operator: "between", formula1: 0, formula2: 4 } };
for (const col of ["S", "T", "U"]) {
  arbitration.getRange(`${col}2:${col}${arbitrationRows.length + 1}`).dataValidation = { rule: { type: "list", values: ["无", "有"] } };
}
arbitration.getRange(`F2:H${arbitrationRows.length + 1}`).format.numberFormat = "0.00";
arbitration.getRange(`K2:K${arbitrationRows.length + 1}`).format.numberFormat = "0.00";
arbitration.getRange(`N2:R${arbitrationRows.length + 1}`).format.numberFormat = "0.00";
arbitration.getRange(`Y2:Z${arbitrationRows.length + 1}`).format.numberFormat = "0.00";
arbitration.getRange(`AD2:AE${arbitrationRows.length + 1}`).format.numberFormat = "0.00";

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(OUTPUT_XLSX);

for (const [sheetName, range, fileName, scale] of [
  ["标注说明", "A1:B17", "标注说明.png", 1],
  ["专家1标注", "A1:X8", "专家1标注.png", 0.75],
  ["专家2标注", "A1:X8", "专家2标注.png", 0.75],
  ["仲裁与分析", "A1:AE8", "仲裁与分析.png", 0.70],
]) {
  const preview = await workbook.render({ sheetName, range, scale, format: "png" });
  await fs.writeFile(path.join(PREVIEW_DIR, fileName), new Uint8Array(await preview.arrayBuffer()));
}

const inspect = await workbook.inspect({
  kind: "sheet,table,formula",
  maxChars: 12000,
  tableMaxRows: 5,
  tableMaxCols: 14,
  options: { maxResults: 200 },
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 200 },
  maxChars: 12000,
});
await fs.writeFile(path.join(OUTPUT_DIR, "workbook_inspect.ndjson"), inspect.ndjson ?? String(inspect));
await fs.writeFile(path.join(OUTPUT_DIR, "workbook_formula_errors.ndjson"), errors.ndjson ?? String(errors));

console.log(JSON.stringify({
  output: OUTPUT_XLSX,
  expertRows: sample.length,
  strata: Object.fromEntries([...new Set(sample.map((item) => item.review_stratum))].map((name) => [name, sample.filter((item) => item.review_stratum === name).length])),
  conditionCounts: Object.fromEntries([...new Set(sample.map((item) => item.answer_condition))].map((name) => [name, sample.filter((item) => item.answer_condition === name).length])),
  previews: PREVIEW_DIR,
}, null, 2));
