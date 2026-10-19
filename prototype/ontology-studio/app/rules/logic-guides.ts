import exampleDefinitions from "./logic-examples.json";
import type { LogicAsset, LogicDocument, LogicKind } from "./logic-api";

export type LogicGuide = {
  question: string; definition: string; choose: string; difference: string;
  input: string; output: string; scenario: string; result: string;
  maintenance: string[]; checks: string[]; fields: { name: string; meaning: string; example: string }[];
};

export const logicGuides: Record<LogicKind, LogicGuide> = {
  business_rule: {
    question: "哪些具体实例符合条件？",
    definition: "针对主体、候选及可选上下文的属性作比较，返回符合条件的候选集合，并说明每个候选的判断依据。",
    choose: "用于设备能力匹配、供应商资格筛选、零件与资源的适用性判断。",
    difference: "筛选结果可以有零个、一个或多个候选；最终推荐哪一个，需要另有明确的决策策略。",
    input: "subject 主体、candidates 候选列表、可选 contexts；属性使用已注册谓词。",
    output: "合格候选 ID、逐项判断和来源；缺少事实时返回信息不足。",
    scenario: "以要求功率 10 kW 的基准设备为主体。候选 A 为 12000 W、B 为 8 kW；转换单位后，A 符合要求，B 未通过。",
    result: "把主体要求改为 6 kW，两个候选都符合；候选能力缺失时不能认定合格。示例主体也是设备，迁移到零件选型时需先注册零件、尺寸及设备能力属性。",
    maintenance: ["选定主体与候选概念；需要比较工序、订单等事实时添加具名上下文角色。", "为每项输入选择来源角色和已绑定到该概念的谓词，再维护必须同时满足的比较条件。", "存在特例时，在“限定具体实例”填写适用 ID；实例范围不会自动形成覆盖优先级。", "验证符合、不符合、缺数及不同主体参数的案例，递增版本并提交审核。"],
    checks: ["所有条件按 AND 组合；复杂分支可使用决策表或决策模型。", "主体适用条件失败是“不适用”，候选条件失败是“未通过”；缺数是“信息不足”。", "适用关系仅作为推导结果；实际设备指派由权威业务系统记录。"],
    fields: [{ name: "角色与概念", meaning: "类型限定每个参与对象，运行时再绑定实例。", example: "主体 equipment；候选 equipment；上下文 baseline" }, { name: "输入与条件", meaning: "变量引用注册谓词；右值可引用另一输入或固定值。", example: "available_power ≥ required_power" }, { name: "实例范围", meaning: "可选的特例适用范围；空列表表示不额外限制。", example: "subject_ids: [\"equipment:special\"]" }],
  },
  business_constraint: {
    question: "这个实例的事实是否合法？",
    definition: "校验单个主体必须满足的业务条件。启用后，编译知识和接收连接器记录时也会执行该约束。",
    choose: "用于业务数据必须满足的条件，例如额定功率大于零。",
    difference: "约束输出是否合法；匹配规则输出哪些候选合适。启用约束会影响事实接收，维护前需要评估现有实例。",
    input: "subject 一个主体的已注册属性；当前编译期约束只支持主体角色。",
    output: "valid 合法、invalid 不合法或 unknown 信息不足。",
    scenario: "设备额定功率必须大于 0 W。输入 10 kW 时合法；输入 0 W 时不合法；未提供功率时信息不足。",
    result: "案例预期 invalid 或 unknown，并且实际返回相同结果，也属于“案例通过”。案例通过表示逻辑符合预期，不代表该实例可以入库。",
    maintenance: ["选择受约束的主体概念，维护主体属性输入和必须满足的条件。", "固定阈值涉及量值时同时填写数值和注册单位。", "用合法值、边界值、非法值和缺少事实的输入验证结果，再评估当前数据是否满足约束。", "修改后递增版本；审核通过后再应用与发布，处理被阻断的数据应回到其权威来源。"],
    checks: ["启用后，invalid 和 unknown 都不能通过该约束的编译检查。", "不支持候选、上下文或限定特定实例；跨实例适用性应使用匹配规则。", "工作台提供的是业务约束，规范结构、引用完整性等技术校验仍由原有编译器承担。"],
    fields: [{ name: "主体概念", meaning: "启用时受此约束检查的实例类型。", example: "equipment" }, { name: "主体输入", meaning: "只能从主体的注册谓词取值。", example: "power ← rated_power" }, { name: "固定值", meaning: "阈值使用 JSON；量值必须显式给出单位。", example: '{"literal":"0","unit":"W"}' }],
  },
  model: {
    question: "数值应该怎么算？",
    definition: "用声明式公式、输入单位、输出单位与精度作确定性计算。这里的模型是计算公式。",
    choose: "用于工时、成本、能耗、折旧或风险分数等数值计算。",
    difference: "公式计算数值；决策表决定不同条件下输出什么；决策模型负责把多个逻辑步骤连接起来。",
    input: "inputs 按变量名给出数值；量值可使用 literal 与 unit，纯数值按该输入声明的单位解释。",
    output: "output.value 十进制字符串、output.unit 单位，以及计算过程。",
    scenario: "工时 = 基准工时 × 工作量系数。基准 1 hour、系数 2，输出 2.0 hour；基准 30 minute、系数 2，输出 1.0 hour。",
    result: "先统一计算单位，再按定义精度舍入。数值“2.0”使用字符串，避免编辑时丢失十进制精度。",
    maintenance: ["定义所有输入变量及单位，选择输出单位和精度。", "使用声明式运算节点维护公式；每项声明输入都必须被公式引用。", "验证正常输入、单位换算和业务边界；成本等场景还应保持币种一致。", "修改公式、系数定义或单位接口后递增版本，并检查引用它的工序与决策模型。"],
    checks: ["支持 input、const、add / sum、multiply、subtract、divide 和 date_diff_days，不支持任意脚本。", "加减要求量纲相容，乘除及输出单位也要通过量纲校验。", "经验系数和实际价格来自场景或权威事实；带权威汇率要求的模型不能用普通手填预览绕过来源治理。"],
    fields: [{ name: "输入变量", meaning: "变量标识、单位及可选的实体属性映射。", example: "baseline_time: hour；workload_factor: one" }, { name: "公式", meaning: "通过有限运算节点描述计算依赖。", example: "multiply(input baseline_time, input workload_factor)" }, { name: "输出单位与精度", meaning: "确定输出含义及舍入位数。", example: "hour；precision = 8" }],
  },
  decision_table: {
    question: "这些条件成立时，输出什么？",
    definition: "用条件列、规则行和输出列维护条件到结果的映射，并明确多行命中时的处理方式。",
    choose: "用于尺寸分档、工艺分支、设备等级选择，或根据多个业务条件选择标准工序。",
    difference: "决策表适合分支判断；复杂计算交给计算模型，跨步骤依赖交给决策模型。表输出设备等级后，仍需结合实例匹配才能确定具体设备。",
    input: "facts 场景字段或 item 重复对象字段；列声明 number、string 或 boolean，数值可声明注册单位。",
    output: "各输出列的值、命中行、是否使用默认输出；COLLECT 返回全部命中结果。",
    scenario: "尺寸演示值 ≤ 300 输出 small，> 300 输出 large。输入 100 得到 small，300 仍是 small，600 得到 large。",
    result: "示例只使用统一口径的演示数值，没有声明毫米。正式尺寸规则需要明确并注册长度单位，再配置数值列单位。",
    maintenance: ["设置输入来源、字段名、类型和适用单位，定义输出列含义。", "逐行维护条件与输出，明确边界值，例如 ≤ 300 与 > 300。", "选择命中策略：UNIQUE 唯一命中，FIRST 取首条，COLLECT 收集全部；需要时配置显式默认输出。", "验证边界、未命中和多条件组合，发布新版本，再由工艺或决策模型显式升级引用。"],
    checks: ["一行内的条件全部满足才命中；* 表示该列不限制。", "UNIQUE 在验证阶段拒绝重叠行；FIRST 的行顺序就是优先顺序。", "UNIQUE / FIRST 未命中且无默认输出时报错；COLLECT 可以返回空列表。", "当前是受限 DMN 式表，不支持完整 DMN XML / FEEL。"],
    fields: [{ name: "输入列", meaning: "列标识与实际输入字段分开配置。", example: "列 size ← facts.size" }, { name: "单元格", meaning: "* 任意；比较、闭区间与集合按列类型解释。", example: "<=300；[100..300]；A|B" }, { name: "输出与策略", meaning: "每行填写全部输出；命中策略控制多行结果。", example: "equipment_class = small；UNIQUE" }],
  },
  decision_model: {
    question: "多个判断和计算如何协作？",
    definition: "把已启用的规则、约束、公式和决策表组织成无环依赖图，明确每个节点的输入来源和最终输出。",
    choose: "用于先校验、再筛选、再计算或分支判断等多步骤决策，以及让多个场景复用同一决策链。",
    difference: "决策模型维护业务逻辑之间的数据依赖；工艺模板维护工序编排。依赖图不表示设备实际开工时间或产能排程。",
    input: "显式场景对象；节点绑定到 facts、上游 node 或 literal，不能自动猜测字段映射。",
    output: "具名最终 output，以及每个已执行节点的输入、引用版本和结果。",
    scenario: "先调用工时模型：1 hour × 2 = 2 hour；再把该工时传入能耗模型：3 kW × 2 hour = 6 kWh。",
    result: "duration 节点先执行，energy 依赖它的输出。示例衔接处均使用 hour；取 output.value 传递纯数值时，必须核对上下游单位一致。",
    maintenance: ["先发布并启用要复用的规则、公式或表，再添加节点并选择固定版本引用。", "逐个维护输入映射；公式可以绑定整个 inputs 对象，也可把变量逐项绑定到场景或上游结果。", "声明最终输出并验证整条链；检查停止位置、缺项和版本，而不只检查最后一个值。", "发布新版本时保存依赖定义快照；升级引用后重新运行完整案例，并在“关联与使用”检查使用方。"],
    checks: ["最多 30 个节点；拒绝循环、未知节点和无效引用，不支持嵌套决策模型。", "约束不通过、规则事实缺失或没有合格候选时，决策图停止，最终 output 为空。", "输入映射路径最多八层；数字字符串可选择列表项，例如 [\"results\",\"0\"]。", "真实实例直接绑定目前适用于规则和约束；决策图使用显式输入映射。"],
    fields: [{ name: "节点与引用", meaning: "引用已启用资产的具体版本；发布时保留定义快照。", example: "duration → model:operation_cycle_time@1.0.0" }, { name: "输入映射", meaning: "source 选 facts / node / literal；path 逐层选择字段。", example: '{"source":"node","node":"duration","path":["output","value"]}' }, { name: "最终输出", meaning: "给需要返回的节点结果命名。", example: "duration → duration.output；energy → energy.output" }],
  },
};

export type GuideRegistry = { id: string; kind?: string; bindings?: { predicateId: string }[] }[];
export function guideExample(kind: LogicKind, assets: LogicAsset[], definitions: GuideRegistry) {
  const definition = structuredClone(exampleDefinitions[kind]) as LogicDocument;
  const missing: string[] = [];
  const needs = (id: string, label: string) => { if (!definitions.some(item => item.id === id)) missing.push(label); };
  if (kind === "business_rule" || kind === "business_constraint") {
    needs("equipment", "设备概念 equipment"); needs("rated_power", "额定功率谓词 rated_power"); needs("unit:kW", "单位 kW"); needs("unit:W", "单位 W");
    const equipment = definitions.find(item => item.id === "equipment");
    if (equipment?.bindings?.length && !equipment.bindings.some(item => item.predicateId === "rated_power")) missing.push("设备概念对 rated_power 的属性绑定");
  }
  if (kind === "model") { needs("unit:hour", "单位 hour"); needs("unit:minute", "单位 minute"); needs("unit:one", "无量纲单位 one"); }
  if (kind === "decision_model") {
    needs("unit:minute", "教学案例使用的单位 minute");
    const expected = [{ id: "operation_cycle_time", output: "hour", inputs: { baseline_time: "hour", workload_factor: "one" } }, { id: "operation_energy", output: "kWh", inputs: { effective_power: "kW", elapsed_time: "hour" } }];
    for (const contract of expected) {
      const asset = assets.find(item => item.kind === "model" && item.id === contract.id && item.lifecycle === "active");
      const inputs = asset?.definition.inputs;
      if (!asset || asset.definition.output_unit !== contract.output || !Array.isArray(inputs) || inputs.length !== Object.keys(contract.inputs).length || !Object.entries(contract.inputs).every(([id, unit]) => inputs.some(input => input && typeof input === "object" && (input as Record<string, unknown>).id === id && (input as Record<string, unknown>).unit === unit))) missing.push(`已启用且接口匹配的模型 ${contract.id}`);
      else for (const node of definition.nodes as Record<string, unknown>[]) if (String(node.ref).startsWith(`model:${contract.id}@`)) node.ref = asset.ref;
    }
  }
  return { definition, missing, input: definition.test_cases![0].input, expected: definition.test_cases![0].expected };
}
