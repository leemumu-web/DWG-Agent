# 历史回归审计

## 现有覆盖

`test_api_regressions.py`、`test_smoke_flow.py`、`test_new_features.py` 保存跨 API 主链；`test_deep_verify.py`、`test_rigorous.py`、`test_edge_cases.py` 保存深度/边界审计；`test_cross_audit_fixes.py` 固定多轮审计发现的跨域缺陷。

## 证据边界

输入沿用每个历史问题的 fixture 和请求顺序，输出是已修缺陷不复发的证据。测试按问题归档，不为目录形式拆散；新增单域行为优先写入对应领域目录。
