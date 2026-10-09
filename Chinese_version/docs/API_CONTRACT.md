# API 契约

本页简要说明当前公开的 Python 与命令行契约。源码 API 版本为
1.1.1（延续 v1.1 契约），与桌面应用和 AppImage 分别版本化；此补丁不改变参数或38列峰表契约。函数签名、字段和错误的
详细定义维护在[外部 API 文档](external-api/README.md)。

## 11. Public nmrforge_api contract

公开 API 为 Bruker NMR 数据提供参考构建和参数组合研究。它记录
处理输入、输出、溯源、状态和警告；不执行统计推断、科学解释或
跨谱峰对应。

每个条件独立构建参考谱。每个 workflow 与条件会在其候选谱中独立
检测并定位峰。API 不建立参考峰与候选峰或不同条件之间的对应关系；
组合峰表中的 reference_peak_id 和 assignment 字段留空。

统一峰表有 38 列，包含逻辑 F1/F2/F3 坐标和核标记；只有核身份
明确时，H/N 字段才作为兼容别名。峰编号仅在单张谱内有效。当前
峰定位使用受支持的三点抛物线方法。权威字段和语义见
[输出与记录](external-api/06-outputs-and-records.md)。

采样与重构支持取决于维数和输入元数据。NUS 日程必须能从受支持的
元数据中恢复；API 不会根据标称采样比例猜测日程。详见
[输入与数据](external-api/05-inputs-and-data.md)及
[限制](external-api/09-limitations-and-roadmap.md)。

## 参考与组合边界

组合研究必须显式指定参考。组合运行只读复用冻结的参考 FID；
输入证据缺失或变化时，须显式重建参考。组合运行不会静默重新转换
或合并原始数据。恢复方式见[限制](external-api/09-limitations-and-roadmap.md)
及[故障排查](external-api/10-troubleshooting.md)。

每次运行都会记录请求与解析后的参数、处理脚本、产物哈希、工具版本、
状态和警告。输出文档定义记录字段和产物目录；记录会区分请求值与
实际应用值。

## 公开入口

从 nmrforge_api 导入 Python 接口。命令行帮助为：

    python -m nmrforge_api --help

公开函数和命令列表见 [API 参考](external-api/03-api-reference.md)与
[CLI 参考](external-api/04-cli-reference.md)。输入要求见
[输入与数据](external-api/05-inputs-and-data.md)；产物、记录和警告
见[输出与记录](external-api/06-outputs-and-records.md)。可从
[外部 API 指南](external-api/README.md)开始，查看可运行示例与
完整文档索引。

## 兼容性

兼容性接口报告外部可观察行为和契约变化。修改公开函数、输出字段、
峰表列、错误码或处理行为时，应同步更新实现、测试、参考文档和
兼容性元数据。当前详情见外部 API 指南及包内公开的兼容性函数。

Qt 和桌面展示不属于 nmrforge_api 契约。不能从 API 版本推断桌面版本。
