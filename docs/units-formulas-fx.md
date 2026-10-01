# 单位、货币、经验公式与参数治理

本迭代沿用 C–R–L–T–P 的统一知识信封。物理单位、货币和可执行公式是 Git 编写的控制定义；设备参数与汇率是有来源、时间和证据的断言；计算结果是带模型快照与输入引用的派生记录。领域包只改变检索和工具策略。

## 物理单位与货币

- `control/units/*.yaml` 为每个物理单位声明稳定 ID、语义版本、量纲、基准单位及到基准单位的精确十进制系数。转换通过共同基准单位完成；不能跨量纲转换。`W`、`kW` 属于功率，`kWh` 属于能量。
- 带物理量纲的 predicate 在 `value.dimension` 和 `value.units` 中限定允许的单位。来源断言保留原数值和单位；模型运行轨迹记录标准化换算，不改写原始事实。
- 公式运算先把物理量转换到共同计算基准，再按声明的输出单位换回。因而 `1000 W + 1 kW = 2 kW`，`1000 W × 1 hour = 1 kWh`；加减仍必须量纲相同。
- `control/currencies/*.yaml` 注册货币代码及最小货币单位的小数位。`currency` 值使用三位大写代码，如 `{type: currency, literal: "120000.00", unit: CNY}`。模型拒绝未注册币种和隐式混币运算。
- `calendar_days` 等历史模型单位保留兼容语义；业务日历和货币换算均不从物理单位表推断。

## 经验公式与系数

声明式模型仍在 `control/models/*.yaml`。注册表校验输入、输出单位、量纲、公式操作、版本以及 `applies_to`、输入 `predicate`、`output_predicate` 与本体概念的绑定。公式只能使用确定性操作节点，不接受任意代码。

`equipment_hourly_depreciation` 演示 `(acquisition_cost - residual_value) / (useful_life_hours × utilization_factor)`。`utilization_factor` 是已注册 predicate；某设备或适用参数方案的具体系数是带有效期、来源和证据的断言。系数变化时更新断言，公式语义变化时递增模型版本。

`POST /v1/calculate/entity` 按 `as_of` 从指定实体选取已确认且在有效期内的输入断言。缺值或同时间歧义会显式报错。派生结果存入耐久 `knowledge.state.db`，包含模型版本、模型定义快照、输入断言引用、来源引用、换算轨迹及结果；不会自动写回业务断言。旧记录在索引重建后仍可解释。Agent 可用 `calculate_entity` 引用 `derived:<run_id>`。

## 汇率

`exchange_rate` 只接受带 `base_currency`、`quote_currency`、`rate_type` 限定的报价，值单位必须是 `QUOTE_per_BASE`，如 `CNY_per_USD`。它绑定 `exchange_quote` 概念；实际报价由连接器接入，不写入 Markdown。连接器映射可从记录字段提取限定词。报价必须已确认、有来源、在请求时间有效且未超过新鲜度窗口。

`POST /v1/fx/convert` 要求显式金额、报价节点、`as_of` 和可选 `rate_type`。服务拒绝币种方向不一致、缺失或过期报价；由受治理的 `fx_conversion` 模型计算，按目标币种的小数位舍入，并记录使用的报价断言和货币定义。普通 `/v1/calculate` 不能调用该模型绕开报价验证。Agent 可在成本领域使用 `convert_currency`。

## Studio 审批流程

Studio 白板展示并编辑本体、Schema、模型和 predicate 的单位契约。物理单位与货币是固定控制定义，不在白板上展示或编辑；它们继续由 `control/units/` 与 `control/currencies/` 注册并供校验与计算使用。模型编辑器可填写版本、适用概念、输入属性、公式表达式，并对草稿试算；试算不产生耐久结果。模型到概念、输入 predicate、输出 predicate 的依赖在画布中可见。

上述控制定义的修改生成高风险 ChangeSet，必须递增语义版本，且提案人不能批准自己的高风险变更。批准只记录治理决定。对单个模型、单位或货币 YAML 文件，具有发布权限的人可在 Studio 点击“应用到真源”：后端验证批准内容与提案时的真源修订一致，再写入 Git 工作树并返回新的 source revision；随后点击“验证发布”编译并登记。多文件变更仍通过现有外部 Git 应用及验证发布流程。历史模型运行仍保存当时的模型定义和输入引用；回退应以新的受审版本发布。

成功编译过的模型定义另存于耐久 `model_revision` 表，可从 `GET /v1/models/history?id=...` 读取。Studio 的模型详情可查看这些版本，并把选中的历史内容作为更高版本草稿提交审核；不会直接覆盖当前版本。

Studio 是非实例控制面。设备参数和汇率的实际值经权威连接器及断言治理进入运行时；前端不会把企业运营值写成 Markdown。单文件控制定义可以经批准后通过受限服务端端点写入 Git 工作树，但该端点不创建 Git commit；仍需按团队流程提交并部署真源文件。

## 兼容与运维

已有数量值继续保留原单位；仅声明了 `dimension` 的新 predicate 强制使用注册单位。已有模型的裸数字输入仍解释为模型声明单位。状态库启动时给旧派生记录增加输入引用与模型快照字段；旧运行记录的新增字段为空，不能事后伪造来源。升级前同时备份 `runtime/knowledge.state.db` 和 `runtime/knowledge.ledger.db`。
