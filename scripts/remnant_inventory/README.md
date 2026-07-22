# 余料库离线工具

本目录归属余料库上线与运行边界，不连接 HTTP、用户会话或正式业务数据库。

- `report_corpus.py`：从显式外部目录递归枚举 DWG/DXF，计算 SHA-256，复用批量 DWG 转换边界和余料 DXF Stage，向显式输出目录写 `report.json` 与 `candidates.csv`。

工具不会把源图写进输出目录；转换需要的副本只存在于自动清理的系统临时目录。生产图纸、报告输出和 ODA 运行文件均不得提交到 Git。
